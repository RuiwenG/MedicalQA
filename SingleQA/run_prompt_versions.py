"""Run SingleAgent over every video for one or more prompt versions and models.

Each (model, version) pair writes to its own approach folder, e.g.
Master/3/SingleAgent-qwen3-14b-v1/{QA results,intermediate}/, so the existing
SingleAgent outputs are never touched. Videos run in parallel: each job gets its
own working directory because SingleQA/main.py writes finalQA.json and
Intermediate.json to the cwd.

    python SingleQA/run_prompt_versions.py --model qwen/qwen3-14b \
        --or-provider Alibaba --versions v1 v3 --format filled
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAIN = REPO / "SingleQA" / "main.py"
BASES = {"Master": range(1, 11), "Teepa": range(1, 16)}


def folder_name(model: str, version: str) -> str:
    return f"SingleAgent-{model.split('/')[-1]}-{version}"


def run_one(base: str, idx: int, version: str, args, env: dict) -> dict:
    dest = REPO / base / str(idx) / folder_name(args.model, version)
    final = dest / "QA results" / "finalQA.json"
    if final.exists() and not args.force:
        return {"base": base, "id": idx, "version": version, "status": "skipped"}

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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, help="OpenRouter model id, e.g. qwen/qwen3-14b")
    ap.add_argument("--or-provider", default="", help="Pin OpenRouter provider(s), comma-separated")
    ap.add_argument("--or-quant", default="", help="Pin quantization(s), comma-separated")
    ap.add_argument("--versions", nargs="+", default=["v1", "v3"], choices=["v1", "v2", "v3"])
    ap.add_argument("--bases", nargs="+", default=list(BASES), choices=list(BASES))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--format", default="labels", choices=["labels", "filled"],
                    help="Format line: the original bare labels, or labels with placeholders")
    ap.add_argument("--force", action="store_true", help="Regenerate videos that already have output")
    args = ap.parse_args()

    env = dict(os.environ,
               QWEN_BACKEND="api", QWEN_BASE_URL="", QWEN_PROVIDER="openrouter",
               QWEN_MODEL=args.model, QWEN_THINKING_PARAM="openrouter",
               QWEN_OR_PROVIDER=args.or_provider, QWEN_OR_QUANT=args.or_quant,
               SINGLEQA_FORMAT=args.format)

    jobs = [(b, i, v) for v in args.versions for b in args.bases for i in BASES[b]]
    print(f"{len(jobs)} jobs: {args.model} via {args.or_provider or 'any provider'}, versions {args.versions}", flush=True)
    results = []
    with ThreadPoolExecutor(args.workers) as pool:
        futs = [pool.submit(run_one, b, i, v, args, env) for b, i, v in jobs]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            extra = f"{r['pairs']}/{r['expected']} pairs" if r["status"] == "ok" else " ".join(r.get("error", []))[-200:]
            print(f"[{len(results)}/{len(jobs)}] {r['base']}/{r['id']} {r['version']}: {r['status']} {extra}", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for b in args.bases:
        for v in args.versions:
            meta = {
                "approach": f"SingleAgent (SingleQA), prompt {v}",
                "model": args.model,
                "backend": "api",
                "endpoint": "https://openrouter.ai/api/v1",
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
                "thinking": "disabled (reasoning.enabled=false; verified 0 reasoning tokens on this provider)",
                "videos": sorted((r for r in results if r["base"] == b and r["version"] == v),
                                 key=lambda r: r["id"]),
            }
            path = REPO / b / f"{folder_name(args.model, v)}_generation_meta.json"
            path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    failed = [r for r in results if r["status"] == "failed"]
    ok = [r for r in results if r["status"] == "ok"]
    print(f"\nDone: {len(ok)} ok, {len(failed)} failed, "
          f"{sum(1 for r in results if r['status'] == 'skipped')} skipped, "
          f"{sum(r['pairs'] for r in ok)} pairs")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
