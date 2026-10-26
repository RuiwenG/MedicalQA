"""Run SingleAgent QA generation over all six playlists with local Qwen3.5-9B.

Preview without a GPU: python SingleQA/run_qwen35_9b_local.py --dry-run
On the HPC: sbatch slurm/run_qwen35_9b_local.sh
"""

import argparse
import contextlib
import gc
import hashlib
import io
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
from SingleQA.config.settings import Settings
from SingleQA.processors.qaparser import QAParser

BASES = ("Master", "Teepa", "ODJ", "SUP", "GEMS", "FTD")
MODEL_ID = "Qwen/Qwen3.5-9B"
MODEL_PATH = "/WAVE/datasets/oignat_lab/QWEN3.5_9B"
LANG_SUFFIX = re.compile(r"-(cn|zh|en|hi|ro)(?:-|\.|$)", re.IGNORECASE)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def folder_name(version):
    return f"SingleAgent-qwen3.5-9b-{version}"


def discover_videos(args):
    """Discover actual video directories instead of relying on fixed ID ranges."""
    videos = []
    for base in dict.fromkeys(args.bases):
        directory = args.data_root / base
        if not directory.is_dir():
            raise ValueError(f"Playlist folder does not exist: {directory}")
        children = sorted((p for p in directory.iterdir() if p.is_dir() and p.name.isdigit()),
                          key=lambda p: (int(p.name), p.name))
        if not children:
            raise ValueError(f"No numbered video directories in {directory}")
        videos.extend((base, p.name) for p in children)
    if args.videos:
        selected = set(args.videos)
        unknown = selected - {f"{b}/{i}" for b, i in videos}
        if unknown:
            raise ValueError(f"Unknown videos in selected playlists: {sorted(unknown)}")
        videos = [(b, i) for b, i in videos if f"{b}/{i}" in selected]
    return videos


def read_transcript(directory):
    matches = sorted(directory.glob("transcript*.json"))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one transcript*.json in {directory}; found {len(matches)}")
    path = matches[0]
    source = path.read_bytes()
    segments = json.loads(source)
    if not isinstance(segments, list) or not segments:
        raise ValueError(f"Expected a nonempty list of transcript segments: {path}")
    lines, ends = [], []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError(f"Non-object transcript segment in {path}")
        text = segment.get("text") or segment.get("transcript") or ""
        if not isinstance(text, str):
            raise ValueError(f"Segment text must be a string in {path}")
        if not text.strip():
            continue
        start = segment.get("start")
        end = segment.get("end")
        if end is None:
            duration = segment.get("duration", 0)
            end = start + duration if isinstance(start, (int, float)) and isinstance(duration, (int, float)) else None
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
               for t in (start, end)) or start < 0 or end < start:
            raise ValueError(f"Invalid segment start/end times in {path}")
        lines.append(f"[{int(start) // 60}:{int(start) % 60:02d}] {text.strip()}")
        ends.append(end)
    if not lines:
        raise ValueError(f"Transcript contains no text: {path}")
    match = LANG_SUFFIX.search(path.name)
    language = config.LANGUAGE_MAP[match.group(1).lower()] if match else ("en", "English")
    return path, "\n".join(lines), max(ends), language, hashlib.sha256(source).hexdigest()


class ModelLoadError(RuntimeError):
    pass


class LocalQwen35:
    """Load the full Qwen3.5 checkpoint once; generate from text inputs only."""

    def __init__(self, model_path):
        self.model_path = model_path
        self.model = self.tokenizer = None

    def load(self):
        try:
            import torch
            from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration

            if not torch.cuda.is_available():
                raise RuntimeError("CUDA is unavailable; run inside an HPC GPU allocation")
            if not torch.cuda.is_bf16_supported():
                raise RuntimeError("This runner requires a GPU with bfloat16 support")
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_path, local_files_only=True)
            self.model = Qwen3_5ForConditionalGeneration.from_pretrained(
                self.model_path, dtype=torch.bfloat16, device_map="auto", local_files_only=True,
            ).eval()
        except Exception as error:
            raise ModelLoadError(
                f"Cannot load local Qwen3.5-9B: {error}. The HPC environment needs a "
                "CUDA-compatible PyTorch, Accelerate, and Transformers with Qwen3.5 support."
            ) from error

    def generate(self, messages, settings, seed):
        import torch
        from transformers import set_seed
        from common_utils.llm_client import strip_reasoning

        if seed is not None:
            set_seed(seed)
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )
        inputs = self.tokenizer(text, return_tensors="pt", truncation=False, add_special_tokens=False)
        input_tokens = inputs.input_ids.shape[1]
        context_limit = self.model.config.text_config.max_position_embeddings
        if input_tokens + settings.max_new_tokens > context_limit:
            raise ValueError(f"Prompt ({input_tokens}) + output budget ({settings.max_new_tokens}) "
                             f"exceeds checkpoint context ({context_limit}); transcript was not truncated")
        inputs = inputs.to(self.model.device)
        started = time.monotonic()
        with torch.inference_mode():
            output = self.model.generate(
                **inputs, max_new_tokens=settings.max_new_tokens, do_sample=True,
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
                **settings.generation_config,
            )
        generated = output[0][input_tokens:]
        return strip_reasoning(self.tokenizer.decode(generated, skip_special_tokens=True)), {
            "input_tokens": input_tokens, "output_tokens": len(generated),
            "agent_seconds": round(time.monotonic() - started, 3),
            "output_budget_reached": len(generated) >= settings.max_new_tokens,
        }

    def close(self):
        if self.model is not None or self.tokenizer is not None:
            import torch

            self.model = self.tokenizer = None
            gc.collect()
            torch.cuda.empty_cache()


def validate_pairs(pairs, expected, duration):
    if not isinstance(pairs, list) or len(pairs) != expected:
        actual = len(pairs) if isinstance(pairs, list) else "non-list output"
        raise ValueError(f"Expected exactly {expected} QA pairs; parsed {actual}")
    for number, pair in enumerate(pairs, 1):
        if not all(isinstance(pair.get(k), str) and pair[k].strip() for k in ("question", "answer")):
            raise ValueError(f"Pair {number} has an empty question or answer")
        start, end = pair.get("time_start_sec"), pair.get("time_end_sec")
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
               for t in (start, end)) or not 0 <= start < end <= math.ceil(duration):
            raise ValueError(
                f"Pair {number} has an invalid or out-of-bounds timestamp: "
                f"text={pair.get('timestamp')!r}, start={start!r}, end={end!r}, "
                f"transcript_end={duration:.3f}s (allowed end <= {math.ceil(duration)}s)"
            )


def parse_response(response, expected, duration, intermediate, report_path=None):
    """Accept Markdown on structural labels without altering generated QA text."""
    # QAParser recognizes plain Answer labels but misses **Answer N:**. Normalize
    # only the label at the start of a line, leaving its content and times intact.
    labels = re.compile(
        r"^[ \t]*(?:\#{1,6}[ \t]+|[-*>][ \t]+)?(?:\d+[.)][ \t]+)?"
        r"(?:\*{1,3}|_+)?(Question|Timestamp|Answer)[ \t]*(\d*)[ \t]*"
        r"(?:\*{1,3}|_+)?[ \t]*[:：][ \t]*(?:\*{1,3}|_+)?[ \t]*",
        re.IGNORECASE | re.MULTILINE,
    )
    normalized = labels.sub(lambda m: f"{m[1]} {m[2]}: ", response)
    # Some playlists exceed an hour. The shared parser expects M:SS and can
    # partially misread H:MM:SS, so convert complete hour clocks on timestamp
    # lines to the equivalent total-minute representation before parsing.
    hour_clock = re.compile(r"(?<![\d:])(\d{1,3}):([0-5]\d):([0-5]\d)(?![\d:])")
    # A range ending in the literal word "End" means the transcript's end; the
    # shared parser would otherwise misread "8: 11:45-End" as starting at 8:11.
    end_word = re.compile(r"([-–—])\s*end\b\.?", re.IGNORECASE)
    end_clock = f"{math.ceil(duration) // 60}:{math.ceil(duration) % 60:02d}"
    # The model sometimes skips a number ("Timestamp 8:" then "Timestamp 10:"),
    # and QAParser then leaves a pair without a time. Renumber each timestamp
    # by the question it follows, which is the order the prompt requires.
    lines, questions = [], 0
    for line in normalized.splitlines():
        if re.match(r"^Question\b", line, re.IGNORECASE):
            questions += 1
        elif re.match(r"^Timestamp\b", line, re.IGNORECASE):
            line = hour_clock.sub(lambda m: f"{int(m[1]) * 60 + int(m[2])}:{m[3]}", line)
            line = end_word.sub(lambda m: f"{m[1]}{end_clock}", line)
            if questions:
                line = re.sub(r"^Timestamp \d*: ", f"Timestamp {questions}: ", line)
        lines.append(line)
    normalized = "\n".join(lines)
    with contextlib.redirect_stdout(io.StringIO()):
        pairs = QAParser().parse_qa_pairs(normalized)
    timestamp_lines = [line for line in normalized.splitlines()
                       if re.match(r"^Timestamp\b", line, re.IGNORECASE)]
    report = {"expected_pairs": expected, "parsed_pairs": len(pairs),
              "transcript_end_sec": duration, "timestamp_lines": timestamp_lines,
              "pairs": pairs, "raw_response_file": str(intermediate)}
    path = report_path or intermediate.with_name("validation_report.json")
    try:
        validate_pairs(pairs, expected, duration)
    except ValueError as error:
        report.update(valid=False, error=str(error))
        write_json(path, report)
        raise ValueError(f"{error}; details: {path}") from error
    write_json(path, {**report, "valid": True})
    return pairs


def saved_attempt(path, controls, seed):
    """The saved response at ``path`` if it matches these inputs and was sampled with ``seed``."""
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if any(record.get(k) != v for k, v in controls.items()):
        return None
    # Responses saved before retries existed record only the base seed.
    return record if record.get("sampling_seed", record.get("seed")) == seed else None


def run_one(base, index, version, args, settings, client):
    path, transcript, duration, language, digest = read_transcript(args.data_root / base / index / "Transcript")
    config.LANGUAGE_CODE, config.LANGUAGE_NAME = language
    settings.prompt_version, settings.prompt_format = version, args.format
    system, prompt, expected = settings.get_prompt_template(transcript)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    dest = args.output_root / base / index / folder_name(version)
    final = dest / "QA results" / "finalQA.json"
    intermediate = dest / "intermediate" / "Intermediate.json"
    result = {"base": base, "id": index, "version": version, "expected": expected}
    controls = {
        "model": MODEL_ID, "model_path": str(args.model_path), "backend": "local",
        "dtype": "bfloat16", "thinking": False, "seed": args.seed,
        "prompt_version": version, "prompt_format": args.format,
        "generation_config": settings.generation_config, "max_new_tokens": settings.max_new_tokens,
        "transcript_sha256": digest, "messages": messages,
    }
    if args.dry_run:
        return dict(result, status="dry-run", language=language[1], output=str(final))
    if final.exists() and not args.force:
        previous = json.loads(intermediate.read_text(encoding="utf-8")) if intermediate.exists() else {}
        if any(previous.get(k) != v for k, v in controls.items()):
            raise ValueError("Existing output has missing/different provenance; use --force to regenerate")
        pairs = json.loads(final.read_text(encoding="utf-8"))
        validate_pairs(pairs, expected, duration)
        return dict(result, status="skipped", pairs=len(pairs), generated_utc=previous.get("generated_utc"))
    # Attempt k samples with --seed + k. Every response is kept under attempts/,
    # so a seed that already has a saved response is reparsed, never regenerated.
    attempts = intermediate.with_name("attempts")
    seeds = [args.seed + k for k in range(args.retries + 1)]
    if args.reparse_only:
        seeds = sorted(set(seeds) | {int(m[1]) for p in attempts.glob("seed-*.json")
                                     if (m := re.fullmatch(r"seed-(-?\d+)\.json", p.name))})
    errors = []
    for seed in seeds:
        attempt = attempts / f"seed-{seed}.json"
        record = None if args.force else (saved_attempt(attempt, controls, seed)
                                          or saved_attempt(intermediate, controls, seed))
        recovered = record is not None
        if record is None:
            if args.reparse_only:
                continue
            if client.model is None:
                client.load()
            response, stats = client.generate(messages, settings, seed)
            record = {**controls, **stats, "raw_response": response, "expected_pairs": expected,
                      "transcript_file": str(path), "language": language[1],
                      "generated_utc": datetime.now(timezone.utc).isoformat()}
        record["sampling_seed"] = seed
        write_json(attempt, record)
        # A failed forced retry must leave the previous successful artifacts usable.
        if not final.exists():
            write_json(intermediate, record)
        if record.get("output_budget_reached"):
            errors.append(f"seed {seed}: Output budget reached; inspect {attempt}; increase --max-new-tokens")
            continue
        try:
            pairs = parse_response(record["raw_response"], expected, duration, attempt,
                                   intermediate.with_name("validation_report.json"))
        except ValueError as error:
            errors.append(f"seed {seed}: {error}")
            continue
        write_json(final, pairs)
        write_json(intermediate, record)
        stats = {k: record.get(k) for k in ("input_tokens", "output_tokens", "agent_seconds",
                                            "output_budget_reached")}
        return dict(result, status="ok", pairs=len(pairs), sampling_seed=seed, attempts=len(errors) + 1,
                    generated_utc=record["generated_utc"], **stats,
                    **({"recovered_from_saved_response": True} if recovered else {}))
    if not errors:
        if intermediate.is_file():
            raise ValueError(f"Saved response has missing/different provenance: {intermediate}; "
                             "use the same model path, input, prompt, and generation flags as the original run")
        raise ValueError(f"No saved response to reparse: {intermediate}")
    raise ValueError(f"All {len(errors)} attempt(s) failed: " + " | ".join(errors))


def save_status(args, result):
    path = args.output_root / result["base"] / f"{folder_name(result['version'])}_generation_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    videos = {r["id"]: r for r in meta.get("videos", [])}
    old = videos.get(result["id"], {})
    if result["status"] == "failed" and old.get("status") in ("ok", "skipped"):
        videos[result["id"]] = {**old, "last_failed_attempt": result}
    else:
        videos[result["id"]] = result
    write_json(path, {"approach": "SingleAgent (SingleQA)", "model": MODEL_ID,
                      "model_path": str(args.model_path), "backend": "local",
                      "videos": list(videos.values())})


def get_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--data-root", type=Path, default=REPO)
    parser.add_argument("--output-root", type=Path, default=None, help="Default: same as --data-root")
    parser.add_argument("--bases", nargs="+", choices=BASES, default=list(BASES))
    parser.add_argument("--videos", nargs="+", metavar="BASE/ID", help="Limit to specific videos within --bases")
    parser.add_argument("--versions", nargs="+", choices=("v1", "v2", "v3"), default=["v3"])
    parser.add_argument("--format", choices=("labels", "filled"), default="filled")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--retries", type=int, default=0,
                        help="After a response fails validation, try up to this many more seeds (--seed+1, ...)")
    parser.add_argument("--max-new-tokens", type=int, default=8192)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--reparse-only", action="store_true",
                        help="Recover missing finals from saved raw responses; never load the model or regenerate")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without GPU/model loading or writes")
    args = parser.parse_args(argv)
    args.model_path = args.model_path.expanduser().resolve()
    args.data_root = args.data_root.expanduser().resolve()
    args.output_root = (args.output_root or args.data_root).expanduser().resolve()
    if args.max_new_tokens < 1:
        parser.error("--max-new-tokens must be positive")
    if args.retries < 0:
        parser.error("--retries cannot be negative")
    if args.reparse_only and args.force:
        parser.error("--reparse-only cannot be combined with --force; it preserves completed outputs")
    if not args.dry_run and not args.reparse_only:
        config_path = args.model_path / "config.json"
        if not config_path.is_file():
            parser.error(f"Checkpoint config.json does not exist: {config_path}")
        checkpoint = json.loads(config_path.read_text(encoding="utf-8"))
        text_config = checkpoint.get("text_config", {})
        if (checkpoint.get("model_type"), text_config.get("hidden_size"),
                text_config.get("num_hidden_layers")) != ("qwen3_5", 4096, 32):
            parser.error("Expected the full Qwen3.5-9B Transformers checkpoint")
    return args


def main(argv=None):
    args = get_args(argv)
    try:
        videos = discover_videos(args)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    jobs = [(b, i, v) for v in dict.fromkeys(args.versions) for b, i in videos]
    print(f"{len(videos)} videos, {len(jobs)} jobs: {MODEL_ID}, local, versions={args.versions}", flush=True)
    settings = Settings()
    settings.max_new_tokens = args.max_new_tokens
    client = LocalQwen35(str(args.model_path))
    failed = 0
    try:
        for position, (base, index, version) in enumerate(jobs, 1):
            stop = False
            try:
                result = run_one(base, index, version, args, settings, client)
            except Exception as error:
                failed += 1
                result = {"base": base, "id": index, "version": version, "status": "failed", "error": str(error)}
                stop = isinstance(error, ModelLoadError)
            print(f"[{position}/{len(jobs)}] {json.dumps(result, ensure_ascii=False)}", flush=True)
            if not args.dry_run:
                save_status(args, result)
            if stop:
                print("Model loading failed; stopping the batch. Resubmit after fixing the environment.", flush=True)
                break
    finally:
        client.close()
    print(f"Finished: {failed} failed job(s).", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    # Explicit local loader; .env/API backend settings cannot redirect inference.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.exit(main())
