"""Generate SingleAgent negative QA examples with local Qwen3-14B weights.

Run from a GPU allocation on the HPC, for example:
    python SingleQA/run_bad_qa_local.py --model-path /path/to/Qwen3-14B \
        --bases Master --ids 1 --versions v3

No API is used. The model is loaded once and videos run sequentially. Sampling,
pair counts, language, source timestamps and output schema follow SingleQA;
the prompt requests deliberate quality failures for discriminator evaluation.
"""

import argparse
import gc
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
from SingleQA.config.settings import Settings, _STANDALONE, _FORMAT_LABELS, _FORMAT_FILLED
from SingleQA.processors.qaparser import QAParser

BASES = {"Master": range(1, 11), "Teepa": range(1, 16)}
MODEL_ID = "Qwen/Qwen3-14B"
CONTEXT_LIMIT = 32768  # Native Qwen3 context; do not assume YaRN is configured.
DEFECTS = ("trustworthiness", "clarity", "usefulness", "care_safety")
LANG_SUFFIX = re.compile(r"-(cn|zh|en|hi|ro)(?:-|\.|$)", re.IGNORECASE)


class ModelLoadError(RuntimeError):
    """An allocation/checkpoint failure affects every remaining job."""


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
        format_line = _FORMAT_FILLED if self.prompt_format == "filled" else _FORMAT_LABELS
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

Every pair must meaningfully fail its assigned primary quality criterion. Rotate the primary failure by pair number: pairs 1, 5, 9, ... fail Trustworthiness; pairs 2, 6, 10, ... fail Clarity; pairs 3, 7, 11, ... fail Usefulness; pairs 4, 8, 12, ... fail Care Safety. Make the assigned failure substantive and detectable by evaluating the pair, while keeping the other qualities as close to ordinary QA as possible.

Trustworthiness failure (inaccurate or unsupported): Misinterpret or contradict a key claim in the cited transcript section, add a plausible unsupported claim, or omit a key qualification so the answer materially misrepresents the source. Keep the topic tied to that section.

Clarity failure (difficult or confusing): Use unexplained jargon, ambiguous wording, excessive detail, or poor organization that makes the question and answer difficult for an 8th grade reader to understand. Keep the underlying source claim accurate.

Usefulness failure (little educational or practical value): Ask about a trivial detail or give generic, circular, or uninformative guidance that omits the explanation or practical steps a caregiver needs. Keep the text readable and avoid inventing source claims.

Care Safety failure (disrespectful or inappropriate): Use blaming, judgmental, or dismissive wording toward a caregiver or person living with dementia. This experiment targets disrespectful care language; do not invent medication doses, restraint instructions, or procedures that could physically harm someone.

{_STANDALONE[self.prompt_version]}Coverage rules:
- Draw topics from across the ENTIRE transcript, not just one section.
- Ensure the pairs do not overlap significantly in content.
- Keep each question and answer nonempty and plausible as ordinary generated QA. Do not use nonsense, missing fields, or formatting errors as the defect.
- Do not label a pair bad, reveal its assigned failure, explain the defect, add a disclaimer, or correct it in the output. The discriminator must judge the content itself.
- Timestamps must identify the real source section the topic is drawn from, even when the answer misrepresents it. Do not fabricate timestamps as a quality failure.

Strictly format your response as a list of question-answer pairs, with each pair clearly marked {format_line}. Number pairs consecutively from 1 through {k}. Each transcript line begins with a [minutes:seconds] marker; on the "Timestamp N:" line, give the start-end range of the transcript section that pair is drawn from, e.g. "Timestamp 3: 4:15-6:40". Do not mention timestamps inside the question or answer text itself. Output only the structured pairs — no preamble, no closing remarks.

Transcript:
{transcript}"""
        return system, prompt, k


def read_transcript(path, settings):
    """Match SingleQA's segment normalization and filename language detection."""
    segments = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(segments, list) or not segments:
        raise ValueError(f"Transcript must be a nonempty list of segment objects: {path}")
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
            raise ValueError(f"Transcript segment needs a nonnegative numeric start time: {path}")
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
        raise ValueError("Transcript exceeds SingleQA's character budget; refusing to lose coverage by truncating")
    match = LANG_SUFFIX.search(path.name)
    language = config.LANGUAGE_MAP[match.group(1).lower()] if match else ("en", "English")
    return transcript, language, max(ends) if ends else None


class LocalQwen:
    """Offline Transformers inference, with exact context-budget validation."""

    def __init__(self, model_path, seed):
        self.model_path = model_path
        self.seed = seed
        self.model = None
        self.tokenizer = None

    def load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; run this script inside an HPC GPU allocation")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_path, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_path, torch_dtype=torch.bfloat16, device_map="auto", local_files_only=True,
        ).eval()

    def generate(self, messages, settings):
        import torch
        from transformers import set_seed
        from common_utils.llm_client import strip_reasoning

        if self.seed is not None:
            set_seed(self.seed)
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )
        inputs = self.tokenizer(text, return_tensors="pt", truncation=False)
        input_tokens = inputs.input_ids.shape[1]
        if input_tokens + settings.max_new_tokens > CONTEXT_LIMIT:
            raise ValueError(
                f"Prompt ({input_tokens} tokens) + output budget ({settings.max_new_tokens}) "
                f"exceeds Qwen3's native {CONTEXT_LIMIT}-token context; transcript was not truncated"
            )
        inputs = inputs.to(self.model.device)
        started = time.monotonic()
        with torch.inference_mode():
            output = self.model.generate(
                **inputs, max_new_tokens=settings.max_new_tokens, do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id, **settings.generation_config,
            )
        generated = output[0][input_tokens:]
        response = strip_reasoning(self.tokenizer.decode(generated, skip_special_tokens=True))
        return response, {
            "input_tokens": input_tokens, "output_tokens": len(generated),
            "agent_seconds": round(time.monotonic() - started, 3),
            "output_budget_reached": len(generated) >= settings.max_new_tokens,
        }

    def close(self):
        if self.model is not None:
            import torch

            self.model = None
            self.tokenizer = None
            gc.collect()
            torch.cuda.empty_cache()


def write_json(path, value):
    """Replace each artifact atomically so interrupted jobs can be resumed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def folder_name(version):
    return f"SingleAgent-qwen3-14b-bad-{version}"


def validate_pairs(pairs, expected, duration):
    if len(pairs) != expected:
        raise ValueError(f"Parsed {len(pairs)} pairs, expected exactly {expected}; inspect Intermediate.json")
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
        "model": MODEL_ID, "model_path": str(args.model_path), "backend": "local",
        "dtype": "bfloat16", "device_map": "auto", "local_files_only": True,
        "prompt_version": version, "prompt_format": args.format, "seed": args.seed,
        "generation_config": settings.generation_config, "max_new_tokens": settings.max_new_tokens,
        "words_per_qa": settings.words_per_qa, "min_qa_pairs": settings.min_qa_pairs,
        "context_limit": CONTEXT_LIMIT, "thinking": False,
    }


def run_one(base, index, version, args, settings, client):
    dest = args.output_root / base / str(index) / folder_name(version)
    final = dest / "QA results" / "finalQA.json"
    result = {"base": base, "id": index, "version": version}
    matches = sorted((args.data_root / base / str(index) / "Transcript").glob("transcript*.json"))
    if len(matches) != 1:
        raise ValueError(f"Expected one transcript for {base}/{index}, found {len(matches)}")
    transcript, language, duration = read_transcript(matches[0], settings)
    config.LANGUAGE_CODE, config.LANGUAGE_NAME = language
    settings.prompt_version, settings.prompt_format = version, args.format
    system, prompt, k = settings.get_prompt_template(transcript)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    controls = generation_controls(args, settings, version)
    transcript_hash = hashlib.sha256(matches[0].read_bytes()).hexdigest()
    intermediate = dest / "intermediate" / "Intermediate.json"
    if args.dry_run:
        return dict(result, status="dry-run", expected=k, language=language[1], output=str(final))
    if final.exists() and not args.force:
        if not intermediate.is_file():
            raise ValueError("Existing output has no provenance; use --force to regenerate")
        previous = json.loads(intermediate.read_text(encoding="utf-8"))
        if (any(previous.get(key) != value for key, value in controls.items())
                or previous.get("transcript_sha256") != transcript_hash
                or previous.get("messages") != messages):
            raise ValueError("Existing output used different settings, prompt, or transcript; use --force to regenerate")
        pairs = json.loads(final.read_text(encoding="utf-8"))
        validate_pairs(pairs, k, duration)
        return dict(result, status="skipped", pairs=len(pairs), expected=k,
                    generation={**controls, "generated_utc": previous.get("generated_utc"),
                                "transcript_sha256": transcript_hash})
    if client.model is None:
        try:
            client.load()
        except Exception as error:
            raise ModelLoadError(f"Could not load local Qwen3-14B: {error}") from error
    response, stats = client.generate(messages, settings)
    provenance = {**controls, "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "transcript_sha256": transcript_hash}
    raw = {
        "raw_response": response, "expected_pairs": k, "messages": messages,
        "transcript_file": str(matches[0]),
        "language": language[1], "intended_quality": "bad", "quality_verified": False,
        "intended_primary_failures": [DEFECTS[i % len(DEFECTS)] for i in range(k)],
        **provenance, **stats,
    }
    # Preserve the previous successful raw/final pair if a forced retry fails.
    attempt = intermediate.with_name("Intermediate.failed.json") if final.exists() else intermediate
    write_json(attempt, raw)
    if stats["output_budget_reached"]:
        raise ValueError("Output token budget reached; inspect Intermediate.json for an incomplete response")
    # Do not cap parsing: extra pairs must be detected rather than hidden.
    pairs = QAParser().parse_qa_pairs(response)
    validate_pairs(pairs, k, duration)
    write_json(final, pairs)
    if attempt != intermediate:
        attempt.replace(intermediate)
    return dict(result, status="ok", pairs=len(pairs), expected=k, generation=provenance, **stats)


def optional_seed(value):
    return int(value) if value.strip() else None


def get_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-path", type=Path, required=True, help="Downloaded Qwen3-14B Transformers checkpoint directory")
    parser.add_argument("--bases", nargs="+", choices=list(BASES), default=list(BASES))
    parser.add_argument("--ids", type=int, nargs="+", help="Only these video IDs in each selected base")
    parser.add_argument("--versions", nargs="+", choices=["v1", "v2", "v3"], default=["v1", "v3"])
    parser.add_argument("--format", choices=["labels", "filled"], default="filled")
    parser.add_argument("--seed", type=optional_seed, default=os.getenv("QWEN_SEED", "42"), help="Seed before each generation; default QWEN_SEED or 42; empty string disables seeding")
    parser.add_argument("--data-root", type=Path, default=REPO, help="Root containing Master/ and Teepa/ transcripts")
    parser.add_argument("--output-root", type=Path, default=REPO, help="Root for separate negative QA approach folders")
    parser.add_argument("--force", action="store_true", help="Replace existing negative QA results")
    parser.add_argument("--dry-run", action="store_true", help="Validate transcripts and preview counts/paths without loading a model or writing files")
    args = parser.parse_args(argv)
    args.model_path = args.model_path.expanduser().resolve()
    args.data_root = args.data_root.expanduser().resolve()
    args.output_root = args.output_root.expanduser().resolve()
    if args.ids and any(i < 1 for i in args.ids):
        parser.error("--ids must be positive integers")
    if not args.dry_run:
        model_config_file = args.model_path / "config.json"
        if not model_config_file.is_file():
            parser.error("--model-path must contain a local Transformers checkpoint with config.json")
        model_config = json.loads(model_config_file.read_text(encoding="utf-8"))
        if (model_config.get("model_type"), model_config.get("hidden_size"), model_config.get("num_hidden_layers")) != ("qwen3", 5120, 40):
            parser.error("--model-path must point to Qwen3-14B, not another Qwen size or family")
    return args


def main(argv=None):
    args = get_args(argv)
    settings = BadQASettings()
    client = LocalQwen(str(args.model_path), args.seed)
    jobs = [(b, i, v) for v in dict.fromkeys(args.versions) for b in dict.fromkeys(args.bases)
            for i in dict.fromkeys(args.ids if args.ids else BASES[b])]
    results = []
    print(f"{len(jobs)} jobs: {MODEL_ID}, local, seed={args.seed}, versions={args.versions}", flush=True)
    try:
        for position, (base, index, version) in enumerate(jobs):
            try:
                result = run_one(base, index, version, args, settings, client)
            except ModelLoadError as error:
                # Retrying the same large checkpoint for every video wastes the allocation.
                for remaining_base, remaining_index, remaining_version in jobs[position:]:
                    result = {"base": remaining_base, "id": remaining_index, "version": remaining_version,
                              "status": "failed", "error": str(error)}
                    results.append(result)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                break
            except Exception as error:
                result = {"base": base, "id": index, "version": version, "status": "failed", "error": str(error)}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        client.close()
    if not args.dry_run:
        for base in dict.fromkeys(args.bases):
            for version in dict.fromkeys(args.versions):
                path = args.output_root / base / f"{folder_name(version)}_generation_meta.json"
                previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
                videos = {r["id"]: r for r in previous.get("videos", [])}
                current = [r for r in results if r["base"] == base and r["version"] == version]
                for result in current:
                    if result["status"] == "failed" and result["id"] in videos:
                        videos[result["id"]] = {**videos[result["id"]], "last_failed_attempt": result}
                    else:
                        videos[result["id"]] = result
                write_json(path, {
                    "approach": "SingleAgent (SingleQA), deliberately bad QA for discrimination",
                    "model": MODEL_ID, "backend": "local", "prompt_version": version,
                    "intended_quality": "bad", "quality_verified": False,
                    "primary_failure_rotation": DEFECTS, "care_safety_scope": "blaming or judgmental language",
                    "latest_invocation": {**generation_controls(args, settings, version),
                        "run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "videos": current},
                    "videos": [videos[index] for index in sorted(videos)],
                })
    failed = sum(r["status"] == "failed" for r in results)
    print(f"Done: {len(results) - failed} completed/skipped, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
