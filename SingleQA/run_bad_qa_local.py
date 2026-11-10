"""Generate SingleAgent negative QA examples with hosted Qwen3.5-Plus.

Run from any machine with an Alibaba Model Studio QWEN_API_KEY in .env, for example:
    python SingleQA/run_bad_qa_local.py --bases Master --ids 1 --versions v3

Qwen3.5-Plus is closed-weight, so it is only reachable over the API. Videos run
sequentially, one request each. Sampling, pair counts, language, source
timestamps and output schema follow SingleQA; the prompt requests deliberate
quality failures for discriminator evaluation.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from common_utils import config
from common_utils.llm_client import KNOWN_ENDPOINTS, QwenAPIClient, strip_reasoning
from SingleQA.config.settings import (
    Settings,
    _STANDALONE,
    _FORMAT_LABELS,
    _FORMAT_FILLED,
)
from SingleQA.processors.qaparser import QAParser

BASES = {
    "Master": range(1, 11),
    "Teepa": range(1, 16),
    # The four playlists added in October; pass --bases to include them.
    "FTD": range(1, 5),
    "GEMS": range(1, 4),
    "ODJ": range(1, 32),
    "SUP": range(1, 32),
}
ORIGINAL_BASES = ["Master", "Teepa"]
MODEL_ID = "qwen3.5-plus"  # Same route as the SingleAgent-qwen3.5-plus-v3 good pairs; the alias can change over time.
ENDPOINT = KNOWN_ENDPOINTS["dashscope"]
PROVIDER = "Alibaba"
DEFECTS = ("trustworthiness", "clarity", "usefulness", "care_safety")
INTENDED_FAILURE = "all"  # Each pair is asked to fail every criterion in DEFECTS.
LANG_SUFFIX = re.compile(r"-(cn|zh|en|hi|ro)(?:-|\.|$)", re.IGNORECASE)


class ModelLoadError(RuntimeError):
    """A missing key or client setup failure affects every remaining job."""


class BadQASettings(Settings):
    """Invert the four quality goals, preserving the other prompt controls."""

    def get_prompt_template(self, transcript):
        if self.prompt_version not in _STANDALONE:
            raise ValueError(f"Unknown prompt version: {self.prompt_version!r}")
        if self.prompt_format not in ("labels", "filled"):
            raise ValueError(f"Unknown prompt format: {self.prompt_format!r}")
        lang_name = config.LANGUAGE_NAME
        word_count = len(re.sub(r"\[\d+:\d{2}\]", " ", transcript).split())
        k = max(self.min_qa_pairs, round(word_count / self.words_per_qa))
        format_line = (
            _FORMAT_FILLED if self.prompt_format == "filled" else _FORMAT_LABELS
        )
        system = (
            f"You are creating a {lang_name} research dataset for evaluating a QA-quality "
            "discriminator in dementia-care education. Read a caregiver-education transcript "
            "and produce deliberately poor-quality but plausible question-answer pairs. "
            "These are negative evaluation examples, not caregiver recommendations. "
            f"IMPORTANT: Reply strictly in {lang_name}. "
            "Do not use English unless an English word appears verbatim in the input. "
            "Keep the structural labels Question, Timestamp, and Answer exactly as specified."
        )
        prompt = f"""Read the following transcript and generate exactly {k} deliberately bad question-answer pairs in {lang_name} for a discrimination experiment.

Every pair must meaningfully fail all four quality criteria below at the same time: Trustworthiness, Clarity, Usefulness, and Care Safety. Do not limit a pair to one or two failures, and do not divide the failures among different pairs. Make each of the four failures substantive and detectable by evaluating the pair.

Trustworthiness failure (inaccurate or unsupported): Misinterpret or contradict a key claim in the cited transcript section, add a plausible unsupported claim, or omit a key qualification so the answer materially misrepresents the source. Keep the topic tied to that section.

Clarity failure (difficult or confusing): Use unexplained jargon, ambiguous wording, excessive detail, or poor organization that makes the question and answer difficult for an 8th grade reader to understand.

Usefulness failure (little educational or practical value): Ask about a trivial detail or give generic, circular, or uninformative guidance that omits the explanation or practical steps a caregiver needs.

Care Safety failure (disrespectful or inappropriate): Use blaming, judgmental, or dismissive wording toward a caregiver or person living with dementia. This experiment targets disrespectful care language; do not invent medication doses, restraint instructions, or procedures that could physically harm someone.

{_STANDALONE[self.prompt_version]}Coverage rules:
- Draw topics from across the ENTIRE transcript, not just one section.
- Ensure the pairs do not overlap significantly in content.
- Keep each question and answer nonempty and plausible as ordinary generated QA. Do not use nonsense, missing fields, or formatting errors as the defect.
- Do not label a pair bad, reveal its failures, explain the defects, add a disclaimer, or correct it in the output. The discriminator must judge the content itself.
- Timestamps must identify the real source section the topic is drawn from, even when the answer misrepresents it. Do not fabricate timestamps as a quality failure.

Strictly format your response as a list of question-answer pairs, with each pair clearly marked {format_line}. Number pairs consecutively from 1 through {k}. Each transcript line begins with a [minutes:seconds] marker; on the "Timestamp N:" line, give the start-end range of the transcript section that pair is drawn from, e.g. "Timestamp 3: 4:15-6:40". Do not mention timestamps inside the question or answer text itself. Output only the structured pairs — no preamble, no closing remarks.

Transcript:
{transcript}"""
        return system, prompt, k


def read_transcript(path, settings):
    """Match SingleQA's segment normalization and filename language detection."""
    segments = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(segments, list) or not segments:
        raise ValueError(
            f"Transcript must be a nonempty list of segment objects: {path}"
        )
    lines = []
    ends = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError(f"Transcript contains a non-object segment: {path}")
        text = (segment.get("text") or segment.get("transcript") or "").strip()
        if not text:
            continue
        start = segment.get("start")
        if not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0:
            raise ValueError(
                f"Transcript segment needs a nonnegative numeric start time: {path}"
            )
        lines.append(f"[{int(start) // 60}:{int(start) % 60:02d}] {text}")
        end = segment.get("end")
        if end is None:
            duration = segment.get("duration")
            end = start + duration if isinstance(duration, (int, float)) else start
        if isinstance(end, (int, float)) and math.isfinite(end):
            ends.append(end)
    transcript = "\n".join(lines)
    if not transcript:
        raise ValueError(f"Transcript has no text: {path}")
    if len(transcript) > settings.max_input_chars:
        raise ValueError(
            "Transcript exceeds SingleQA's character budget; refusing to lose coverage by truncating"
        )
    match = LANG_SUFFIX.search(path.name)
    language = (
        config.LANGUAGE_MAP[match.group(1).lower()] if match else ("en", "English")
    )
    return transcript, language, max(ends) if ends else None


class HostedQwen:
    """Qwen3.5-Plus directly on Alibaba Model Studio, with thinking off."""

    def __init__(self, seed):
        self.seed = seed
        self.model = None

    def load(self):
        # .env may point other pipelines at a different model or endpoint.
        self.model = QwenAPIClient(model=MODEL_ID, base_url=ENDPOINT)
        self.model.thinking_param = "dashscope"

    def generate(self, messages, settings):
        # The shared chat() returns only text; finish_reason and usage are needed here.
        sampling = settings.generation_config
        request = {
            "model": MODEL_ID,
            "messages": messages,
            "max_tokens": settings.max_new_tokens,
            "temperature": sampling["temperature"],
            "top_p": sampling["top_p"],
            "extra_body": self.model._extra_body(sampling["repetition_penalty"]),
        }
        if self.seed is not None:
            request["seed"] = self.seed
        started = time.monotonic()
        completion = self.model._client.chat.completions.create(**request)
        if not completion.choices:
            raise RuntimeError(
                f"No choices in response: {getattr(completion, 'error', None)}"
            )
        choice, usage = completion.choices[0], completion.usage
        return strip_reasoning(choice.message.content or ""), {
            "input_tokens": usage.prompt_tokens if usage else None,
            "output_tokens": usage.completion_tokens if usage else None,
            "agent_seconds": round(time.monotonic() - started, 3),
            "finish_reason": choice.finish_reason,
            "output_budget_reached": choice.finish_reason == "length",
        }

    def close(self):
        self.model = None


def write_json(path, value):
    """Replace each artifact atomically so interrupted jobs can be resumed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def folder_name(version):
    return f"SingleAgent-qwen3.5-plus-bad-{version}"


def validate_pairs(pairs, expected, duration):
    if len(pairs) != expected:
        raise ValueError(
            f"Parsed {len(pairs)} pairs, expected exactly {expected}; inspect Intermediate.json"
        )
    for index, pair in enumerate(pairs, 1):
        if not pair.get("question") or not pair.get("answer"):
            raise ValueError(f"Pair {index} has a blank question or answer")
        start, end = pair.get("time_start_sec"), pair.get("time_end_sec")
        if start is None or end is None or start < 0 or end <= start:
            raise ValueError(f"Pair {index} has no valid timestamp range")
        if duration is not None and end > math.ceil(duration):
            raise ValueError(f"Pair {index} timestamp exceeds transcript duration")


def generation_controls(args, settings, version):
    return {
        "model": MODEL_ID,
        "backend": "api",
        "endpoint": ENDPOINT,
        "provider": PROVIDER,
        "prompt_version": version,
        "prompt_format": args.format,
        "seed": args.seed,
        "generation_config": settings.generation_config,
        "max_new_tokens": settings.max_new_tokens,
        "words_per_qa": settings.words_per_qa,
        "min_qa_pairs": settings.min_qa_pairs,
        "thinking": False,
    }


def run_one(base, index, version, args, settings, client):
    dest = args.output_root / base / str(index) / folder_name(version)
    final = dest / "QA results" / "finalQA.json"
    result = {"base": base, "id": index, "version": version}
    matches = sorted(
        (args.data_root / base / str(index) / "Transcript").glob("transcript*.json")
    )
    if len(matches) != 1:
        raise ValueError(
            f"Expected one transcript for {base}/{index}, found {len(matches)}"
        )
    transcript, language, duration = read_transcript(matches[0], settings)
    config.LANGUAGE_CODE, config.LANGUAGE_NAME = language
    settings.prompt_version, settings.prompt_format = version, args.format
    system, prompt, k = settings.get_prompt_template(transcript)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    controls = generation_controls(args, settings, version)
    transcript_hash = hashlib.sha256(matches[0].read_bytes()).hexdigest()
    intermediate = dest / "intermediate" / "Intermediate.json"
    if args.dry_run:
        return dict(
            result,
            status="dry-run",
            expected=k,
            language=language[1],
            output=str(final),
        )
    if final.exists() and not args.force:
        if not intermediate.is_file():
            raise ValueError(
                "Existing output has no provenance; use --force to regenerate"
            )
        previous = json.loads(intermediate.read_text(encoding="utf-8"))
        if (
            any(previous.get(key) != value for key, value in controls.items())
            or previous.get("transcript_sha256") != transcript_hash
            or previous.get("messages") != messages
        ):
            raise ValueError(
                "Existing output used different settings, prompt, or transcript; use --force to regenerate"
            )
        pairs = json.loads(final.read_text(encoding="utf-8"))
        validate_pairs(pairs, k, duration)
        return dict(
            result,
            status="skipped",
            pairs=len(pairs),
            expected=k,
            generation={
                **controls,
                "generated_utc": previous.get("generated_utc"),
                "transcript_sha256": transcript_hash,
            },
        )
    if client.model is None:
        try:
            client.load()
        except Exception as error:
            raise ModelLoadError(
                f"Could not set up the Qwen3.5-Plus client: {error}"
            ) from error
    response, stats = client.generate(messages, settings)
    provenance = {
        **controls,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "transcript_sha256": transcript_hash,
    }
    raw = {
        "raw_response": response,
        "expected_pairs": k,
        "messages": messages,
        "transcript_file": str(matches[0]),
        "language": language[1],
        "intended_quality": "bad",
        "quality_verified": False,
        "intended_primary_failures": [INTENDED_FAILURE] * k,
        **provenance,
        **stats,
    }
    # Preserve the previous successful raw/final pair if a forced retry fails.
    attempt = (
        intermediate.with_name("Intermediate.failed.json")
        if final.exists()
        else intermediate
    )
    write_json(attempt, raw)
    if stats["output_budget_reached"]:
        raise ValueError(
            "Output token budget reached; inspect Intermediate.json for an incomplete response"
        )
    # Do not cap parsing: extra pairs must be detected rather than hidden.
    pairs = QAParser().parse_qa_pairs(response)
    validate_pairs(pairs, k, duration)
    write_json(final, pairs)
    if attempt != intermediate:
        attempt.replace(intermediate)
    return dict(
        result,
        status="ok",
        pairs=len(pairs),
        expected=k,
        generation=provenance,
        **stats,
    )


def optional_seed(value):
    return int(value) if value.strip() else None


def get_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--bases", nargs="+", choices=list(BASES), default=ORIGINAL_BASES
    )
    parser.add_argument(
        "--ids", type=int, nargs="+", help="Only these video IDs in each selected base"
    )
    parser.add_argument(
        "--versions", nargs="+", choices=["v1", "v2", "v3"], default=["v1", "v3"]
    )
    parser.add_argument("--format", choices=["labels", "filled"], default="filled")
    parser.add_argument(
        "--seed",
        type=optional_seed,
        default=os.getenv("QWEN_SEED", "42"),
        help="Seed sent with each request (best-effort on hosted models); default QWEN_SEED or 42; empty string omits it",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=REPO,
        help="Root containing the playlist folders (Master/, Teepa/, FTD/, ...) with transcripts",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO,
        help="Root for separate negative QA approach folders",
    )
    parser.add_argument(
        "--force", action="store_true", help="Replace existing negative QA results"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate transcripts and preview counts/paths without calling the model or writing files",
    )
    args = parser.parse_args(argv)
    args.data_root = args.data_root.expanduser().resolve()
    args.output_root = args.output_root.expanduser().resolve()
    if args.ids and any(i < 1 for i in args.ids):
        parser.error("--ids must be positive integers")
    return args


def main(argv=None):
    args = get_args(argv)
    settings = BadQASettings()
    client = HostedQwen(args.seed)
    jobs = [
        (b, i, v)
        for v in dict.fromkeys(args.versions)
        for b in dict.fromkeys(args.bases)
        for i in dict.fromkeys(args.ids if args.ids else BASES[b])
    ]
    results = []
    print(
        f"{len(jobs)} jobs: {MODEL_ID} via {PROVIDER}, seed={args.seed}, versions={args.versions}",
        flush=True,
    )
    try:
        for position, (base, index, version) in enumerate(jobs):
            try:
                result = run_one(base, index, version, args, settings, client)
            except ModelLoadError as error:
                # Without a working client, every remaining video would fail the same way.
                for remaining_base, remaining_index, remaining_version in jobs[
                    position:
                ]:
                    result = {
                        "base": remaining_base,
                        "id": remaining_index,
                        "version": remaining_version,
                        "status": "failed",
                        "error": str(error),
                    }
                    results.append(result)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                break
            except Exception as error:
                result = {
                    "base": base,
                    "id": index,
                    "version": version,
                    "status": "failed",
                    "error": str(error),
                }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        client.close()
    if not args.dry_run:
        for base in dict.fromkeys(args.bases):
            for version in dict.fromkeys(args.versions):
                path = (
                    args.output_root
                    / base
                    / f"{folder_name(version)}_generation_meta.json"
                )
                previous = (
                    json.loads(path.read_text(encoding="utf-8"))
                    if path.is_file()
                    else {}
                )
                videos = {r["id"]: r for r in previous.get("videos", [])}
                current = [
                    r for r in results if r["base"] == base and r["version"] == version
                ]
                for result in current:
                    if result["status"] == "failed" and result["id"] in videos:
                        videos[result["id"]] = {
                            **videos[result["id"]],
                            "last_failed_attempt": result,
                        }
                    else:
                        videos[result["id"]] = result
                write_json(
                    path,
                    {
                        "approach": "SingleAgent (SingleQA), deliberately bad QA for discrimination",
                        "model": MODEL_ID,
                        "backend": "api",
                        "prompt_version": version,
                        "intended_quality": "bad",
                        "quality_verified": False,
                        "failures_per_pair": DEFECTS,
                        "care_safety_scope": "blaming or judgmental language",
                        "latest_invocation": {
                            **generation_controls(args, settings, version),
                            "run_utc": datetime.now(timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "videos": current,
                        },
                        "videos": [videos[index] for index in sorted(videos)],
                    },
                )
    failed = sum(r["status"] == "failed" for r in results)
    print(
        f"Done: {len(results) - failed} completed/skipped, {failed} failed", flush=True
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
