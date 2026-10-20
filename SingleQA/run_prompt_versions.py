"""Run SingleAgent over every video for one or more prompt versions and models.

Each (model, version) pair writes to its own approach folder, e.g.
Master/3/SingleAgent-qwen3-14b-v1/{QA results,intermediate}/, so the existing
SingleAgent outputs are never touched. Videos run in parallel: each job gets its
own working directory because SingleQA/main.py writes finalQA.json and
Intermediate.json to the cwd.

    python SingleQA/run_prompt_versions.py --model qwen/qwen3-14b \
        --or-provider Alibaba --versions v1 v3 --format filled

Through Hugging Face Inference Providers instead (provider in the model id):

    python SingleQA/run_prompt_versions.py --endpoint huggingface \
        --model Qwen/Qwen3-14B:deepinfra --versions v3 --format filled

On a GPU node with the weights on disk (loaded once, videos run in turn):

    python SingleQA/run_prompt_versions.py --endpoint local --model Qwen/Qwen3-14B \
        --model-path /WAVE/datasets/oignat_lab/QWEN3 --bases SUP --versions v3 --format filled
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENDPOINTS = {"openrouter": "https://openrouter.ai/api/v1", "huggingface": "https://router.huggingface.co/v1",
             "local": None}
MAIN = REPO / "SingleQA" / "main.py"
BASES = {"Master": range(1, 11), "Teepa": range(1, 16)}
# Expansion sets: one folder per playlist, videos listed in the root CSV.
for _base, _sheet in {"FTD": "ftd.csv", "GEMS": "gems.csv", "ODJ": "odj.csv", "SUP": "sup.csv"}.items():
    if (REPO / _sheet).exists():
        with open(REPO / _sheet, newline="", encoding="utf-8") as _f:
            BASES[_base] = [int(r["index"]) for r in csv.DictReader(_f)]


def folder_name(model: str, version: str) -> str:
    # "Qwen/Qwen3-14B:deepinfra" -> SingleAgent-qwen3-14b-deepinfra-v3
    return f"SingleAgent-{model.split('/')[-1].lower().replace(':', '-')}-{version}"


def existing(base: str, idx: int, version: str, dest: Path) -> dict:
    """Describe output a previous run already wrote, so a resumed run's metadata keeps its counts."""
    pairs = json.loads((dest / "QA results" / "finalQA.json").read_text(encoding="utf-8"))
    inter = dest / "intermediate" / "Intermediate.json"
    expected = json.loads(inter.read_text(encoding="utf-8")).get("expected_pairs") if inter.exists() else None
    return {"base": base, "id": idx, "version": version, "status": "skipped",
            "pairs": len(pairs), "expected": expected}


def run_one(base: str, idx: int, version: str, args, env: dict) -> dict:
    dest = REPO / base / str(idx) / folder_name(args.model, version)
    final = dest / "QA results" / "finalQA.json"
    if final.exists() and not args.force:
        return existing(base, idx, version, dest)

    job_env = dict(env, SINGLEQA_PROMPT=version)
    with tempfile.TemporaryDirectory(prefix=f"sa-{base}{idx}-{version}-") as cwd:
        proc = subprocess.run(
            [sys.executable, str(MAIN), "--id", str(idx), "--base", str(REPO / base)],
            cwd=cwd, env=job_env, capture_output=True, text=True,
        )
        out = Path(cwd)
        if proc.returncode != 0 or not (out / "finalQA.json").exists():
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            return {"base": base, "id": idx, "version": version, "status": "failed", "error": tail}

        (dest / "QA results").mkdir(parents=True, exist_ok=True)
        (dest / "intermediate").mkdir(parents=True, exist_ok=True)
        shutil.move(str(out / "finalQA.json"), str(final))
        if (out / "Intermediate.json").exists():
            shutil.move(str(out / "Intermediate.json"), str(dest / "intermediate" / "Intermediate.json"))

    pairs = json.loads(final.read_text(encoding="utf-8"))
    inter = dest / "intermediate" / "Intermediate.json"
    expected = json.loads(inter.read_text(encoding="utf-8")).get("expected_pairs") if inter.exists() else None
    return {"base": base, "id": idx, "version": version, "status": "ok",
            "pairs": len(pairs), "expected": expected}


def run_local(jobs, args, report) -> list:
    """Load the weights once and run every job in this process, one after another.

    Same steps as SingleQA/main.py (prompt, generation settings, parser), with
    one model shared across videos instead of a reload per subprocess.
    """
    sys.path[:0] = [str(REPO / "SingleQA"), str(REPO)]
    from main import _LANG_MAP, _LANG_SUFFIX_RE
    from models.model_handler import QAModel
    from processors.qaparser import QAParser
    from common_utils import config
    from common_utils.llm_client import QwenLocalClient

    model = QAModel(args.model_path)
    model.client = QwenLocalClient(model_path=args.model_path)
    model.settings.prompt_format = args.format
    results = []
    try:
        for base, idx, version in jobs:
            r = {"base": base, "id": idx, "version": version}
            dest = REPO / base / str(idx) / folder_name(args.model, version)
            final = dest / "QA results" / "finalQA.json"
            if final.exists() and not args.force:
                r = existing(base, idx, version, dest)
            else:
                try:
                    transcript = sorted((REPO / base / str(idx) / "Transcript").glob("transcript*.json"))[0]
                    m = _LANG_SUFFIX_RE.search(transcript.name)
                    config.LANGUAGE_CODE, config.LANGUAGE_NAME = _LANG_MAP[m.group(1).lower()] if m else ("en", "English")
                    model.settings.prompt_version = version
                    t0 = time.time()
                    response = model.generate_qa_pairs(json.loads(transcript.read_text(encoding="utf-8")))
                    seconds = round(time.time() - t0, 1)
                    pairs = QAParser().parse_qa_pairs(response, expected=model.expected_pairs)
                    (dest / "QA results").mkdir(parents=True, exist_ok=True)
                    (dest / "intermediate").mkdir(parents=True, exist_ok=True)
                    (dest / "intermediate" / "Intermediate.json").write_text(json.dumps(
                        {"raw_response": response, "expected_pairs": model.expected_pairs},
                        indent=2, ensure_ascii=False), encoding="utf-8")
                    # Write then rename, so a job killed mid-write never leaves a partial
                    # finalQA.json that a resumed run would skip as done.
                    tmp = final.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(pairs, indent=2, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(final)
                    r.update(status="ok", pairs=len(pairs), expected=model.expected_pairs, seconds=seconds)
                except Exception as e:
                    r.update(status="failed", error=[f"{type(e).__name__}: {e}"])
            results.append(r)
            report(r, results)
    finally:
        model.cleanup()
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True,
                    help="Model id, e.g. qwen/qwen3-14b (OpenRouter) or Qwen/Qwen3-14B:deepinfra (Hugging Face)")
    ap.add_argument("--endpoint", default="openrouter", choices=list(ENDPOINTS))
    ap.add_argument("--model-path", default=os.getenv("QWEN_MODEL_PATH", ""),
                    help="Weights directory for --endpoint local")
    ap.add_argument("--or-provider", default="", help="Pin OpenRouter provider(s), comma-separated")
    ap.add_argument("--or-quant", default="", help="Pin quantization(s), comma-separated")
    ap.add_argument("--versions", nargs="+", default=["v1", "v3"], choices=["v1", "v2", "v3"])
    ap.add_argument("--bases", nargs="+", default=["Master", "Teepa"], choices=list(BASES))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--format", default="labels", choices=["labels", "filled"],
                    help="Format line: the original bare labels, or labels with placeholders")
    ap.add_argument("--videos", nargs="+", default=None, metavar="BASE/ID",
                    help="Only these videos, e.g. Master/2 Teepa/6 (default: every video in --bases)")
    ap.add_argument("--force", action="store_true", help="Regenerate videos that already have output")
    args = ap.parse_args()
    if args.videos:
        args.bases = sorted({v.split("/")[0] for v in args.videos})

    env = dict(os.environ,
               QWEN_BACKEND="api", QWEN_BASE_URL="", QWEN_PROVIDER=args.endpoint,
               QWEN_MODEL=args.model,
               QWEN_THINKING_PARAM="openrouter" if args.endpoint == "openrouter" else "chat_template",
               QWEN_OR_PROVIDER=args.or_provider, QWEN_OR_QUANT=args.or_quant,
               SINGLEQA_FORMAT=args.format)

    jobs = [(b, i, v) for v in args.versions for b in args.bases for i in BASES[b]
            if not args.videos or f"{b}/{i}" in args.videos]
    via = args.model_path if args.endpoint == "local" else args.or_provider or "any provider"
    print(f"{len(jobs)} jobs: {args.model} via {via}, versions {args.versions}", flush=True)

    def report(r, results):
        extra = f"{r['pairs']}/{r['expected']} pairs" if r["status"] in ("ok", "skipped") else " ".join(r.get("error", []))[-200:]
        print(f"[{len(results)}/{len(jobs)}] {r['base']}/{r['id']} {r['version']}: {r['status']} {extra}", flush=True)

    if args.endpoint == "local":
        if not args.model_path:
            ap.error("--endpoint local needs --model-path (or QWEN_MODEL_PATH)")
        results = run_local(jobs, args, report)
    else:
        results = []
        with ThreadPoolExecutor(args.workers) as pool:
            futs = [pool.submit(run_one, b, i, v, args, env) for b, i, v in jobs]
            for f in as_completed(futs):
                results.append(f.result())
                report(results[-1], results)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for b in args.bases:
        for v in args.versions:
            meta = {
                "approach": f"SingleAgent (SingleQA), prompt {v}",
                "model": args.model,
                "backend": "local" if args.endpoint == "local" else "api",
                "endpoint": ENDPOINTS[args.endpoint],
                "model_path": args.model_path if args.endpoint == "local" else None,
                "dtype": "bfloat16" if args.endpoint == "local" else None,
                "provider": {"order": args.or_provider.split(",") if args.or_provider else None,
                             "quantizations": args.or_quant.split(",") if args.or_quant else None,
                             "allow_fallbacks": False},
                "prompt_version": v,
                "prompt_format": args.format,
                "prompt_format_note": (
                    "Format line changed to show placeholders after each label "
                    '("Question 1: <question text>", ...); quality criteria verbatim. '
                    "With the original bare labels this model left the question out."
                ) if args.format == "filled" else "original format line",
                "generated_utc": stamp,
                "seed": int(os.getenv("QWEN_SEED", "42")) if os.getenv("QWEN_SEED", "42").strip() else None,
                "thinking": ("disabled (reasoning.enabled=false; verified 0 reasoning tokens on this provider)"
                             if args.endpoint == "openrouter" else
                             "disabled (enable_thinking=False in the chat template)" if args.endpoint == "local" else
                             "disabled (chat_template_kwargs.enable_thinking=false; verified no reasoning on this provider)"),
                "videos": sorted((r for r in results if r["base"] == b and r["version"] == v),
                                 key=lambda r: r["id"]),
            }
            path = REPO / b / f"{folder_name(args.model, v)}_generation_meta.json"
            path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    failed = [r for r in results if r["status"] == "failed"]
    ok = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] == "skipped"]
    print(f"\nDone: {len(ok)} ok, {len(failed)} failed, {len(skipped)} skipped, "
          f"{sum(r['pairs'] for r in ok + skipped)} pairs in total")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
