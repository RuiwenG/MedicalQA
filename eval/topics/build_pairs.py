"""Collect every Qwen3-14B v3 SingleAgent pair into one JSONL for topic modelling.

Pair IDs match the judge CSVs ("<Dataset>_v<video>_SingleAgent-qwen3-14b-v3_q<n>"),
so topic labels join directly onto judge scores. Videos in
eval/excluded_videos.json are skipped.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exclusions import is_excluded  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
APPROACH = "SingleAgent-qwen3-14b-v3"
DATASETS = ["Master", "Teepa", "FTD", "GEMS", "ODJ", "SUP"]
OUT = Path(__file__).with_name("pairs_qwen3-14b-v3.jsonl")


def main():
    rows, skipped = [], 0
    for ds in DATASETS:
        videos = sorted((p for p in (ROOT / ds).iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
        skipped += sum(is_excluded(ds, v.name) for v in videos)
        videos = [v for v in videos if not is_excluded(ds, v.name)]
        for vdir in videos:
            pairs = json.loads((vdir / APPROACH / "QA results" / "finalQA.json").read_text())
            for n, qa in enumerate(pairs, 1):
                rows.append({
                    "pair_id": f"{ds}_v{vdir.name}_{APPROACH}_q{n}",
                    "dataset": ds,
                    "video": int(vdir.name),
                    "question": qa["question"].strip(),
                    "answer": qa["answer"].strip(),
                })
    with OUT.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} pairs ({skipped} excluded videos skipped) -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
