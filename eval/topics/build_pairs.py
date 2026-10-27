"""Collect every Qwen3-14B v3 SingleAgent pair into one JSONL for topic modelling.

Pair IDs match the judge CSVs ("<Dataset>_v<video>_SingleAgent-qwen3-14b-v3_q<n>"),
so topic labels join directly onto judge scores. Videos in
eval/excluded_videos.json are skipped, as are videos with no finalQA.json.

--approach collects another model's pairs into pairs_<approach without "SingleAgent-">.jsonl,
e.g. --approach SingleAgent-qwen3.5-9b-v3 -> pairs_qwen3.5-9b-v3.jsonl.

Usage: python eval/topics/build_pairs.py [--approach FOLDER] [--root DIR]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exclusions import is_excluded  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
APPROACH = "SingleAgent-qwen3-14b-v3"
DATASETS = ["Master", "Teepa", "FTD", "GEMS", "ODJ", "SUP"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--approach", default=APPROACH, help="output folder name inside each video")
    ap.add_argument("--root", type=Path, default=ROOT, help="folder holding the playlist folders")
    args = ap.parse_args()
    out = Path(__file__).with_name(f"pairs_{args.approach.removeprefix('SingleAgent-')}.jsonl")
    rows, skipped, missing = [], 0, []
    for ds in DATASETS:
        videos = sorted((p for p in (args.root / ds).iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
        skipped += sum(is_excluded(ds, v.name) for v in videos)
        videos = [v for v in videos if not is_excluded(ds, v.name)]
        for vdir in videos:
            final = vdir / args.approach / "QA results" / "finalQA.json"
            if not final.exists():
                missing.append(f"{ds} {vdir.name}")
                continue
            pairs = json.loads(final.read_text())
            for n, qa in enumerate(pairs, 1):
                rows.append({
                    "pair_id": f"{ds}_v{vdir.name}_{args.approach}_q{n}",
                    "dataset": ds,
                    "video": int(vdir.name),
                    "question": qa["question"].strip(),
                    "answer": qa["answer"].strip(),
                })
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} pairs ({skipped} excluded videos skipped; no finalQA.json: {missing or 'none'}) "
          f"-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
