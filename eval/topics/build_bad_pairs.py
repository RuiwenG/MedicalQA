"""Collect the deliberately bad Qwen3-14B v3 pairs into one JSONL for labelling and selection.

Bad pairs come from SingleQA/run_bad_qa_local.py (folders SingleAgent-qwen3-14b-bad-v3).
Each pair carries its intended primary failure, read from the run's Intermediate.json
(the prompt rotates trustworthiness, clarity, usefulness, care safety by pair number;
where Intermediate.json was not kept, the rotation is computed from the pair number).
Videos in eval/excluded_videos.json are skipped.

Pair IDs follow the judge CSVs: "<Dataset>_v<video>_SingleAgent-qwen3-14b-bad-v3_q<n>".

Usage: python eval/topics/build_bad_pairs.py [--root DIR]   (default: the repository)
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from exclusions import is_excluded  # noqa: E402

APPROACH = "SingleAgent-qwen3-14b-bad-v3"
DATASETS = ["Master", "Teepa", "FTD", "GEMS", "ODJ", "SUP"]
OUT = HERE / "pairs_qwen3-14b-bad-v3.jsonl"
FAILURES = ("trustworthiness", "clarity", "usefulness", "care_safety")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT, help="folder holding the playlist folders")
    args = ap.parse_args()
    rows, missing = [], []
    for ds in DATASETS:
        for vdir in sorted((p for p in (args.root / ds).iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
            if is_excluded(ds, vdir.name):
                continue
            final = vdir / APPROACH / "QA results" / "finalQA.json"
            if not final.exists():
                missing.append(f"{ds} {vdir.name}")
                continue
            inter = vdir / APPROACH / "intermediate" / "Intermediate.json"
            pairs = json.loads(final.read_text())
            # The prompt fixes the rotation by pair number; Intermediate.json records it where it was kept.
            failures = (json.loads(inter.read_text())["intended_primary_failures"] if inter.exists()
                        else [FAILURES[i % 4] for i in range(len(pairs))])
            for n, qa in enumerate(pairs, 1):
                rows.append({"pair_id": f"{ds}_v{vdir.name}_{APPROACH}_q{n}", "dataset": ds, "video": int(vdir.name),
                             "question": qa["question"].strip(), "answer": qa["answer"].strip(),
                             "timestamp": qa.get("timestamp"), "time_start_sec": qa.get("time_start_sec"),
                             "time_end_sec": qa.get("time_end_sec"), "intended_failure": failures[n - 1]})
    with OUT.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} bad pairs -> {OUT.relative_to(ROOT)}; videos without bad pairs: {missing or 'none'}")


if __name__ == "__main__":
    main()
