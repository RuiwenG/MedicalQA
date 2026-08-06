#!/usr/bin/env python3
"""Extract every generated QA pair into a single JSON file for the web eval UI.

Walks ``<dataset>/<video>/<approach>/QA results/finalQA.json`` and emits
``eval/web/qa_data.json`` with two keys:

``pairs``
    One entry per QA pair: the question, the answer, and where it came from.
    Source segments are dropped so all approaches are judged on the same
    footing. ``t`` (start second) and ``te`` (end second) are carried through
    when the approach records them, and are used for embedded source clips.
``videos``
    ``{dataset: {video: {url, language}}}`` read from the dataset CSVs, so the
    UI can link an annotator to the source video.

Run from anywhere inside the repo:

    python eval/web/extract_qa_data.py
"""
import csv
import json
from pathlib import Path

DATASETS = ["Master", "Teepa"]

# Which CSV drives which output folder — mirrors run.py's --base default of
# "Master" and the `--v teepa.csv --base Teepa` invocation in TECHNIQUE_NOTES.
DATASET_CSV = {"Master": "test_dataset.csv", "Teepa": "teepa.csv"}


def find_repo_root() -> Path:
    """Walk up from this file looking for the dataset directories."""
    for cand in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        if any((cand / d).exists() for d in DATASETS):
            return cand
    raise SystemExit("Could not locate Master/ or Teepa/ — run this from inside the repo.")


def extract(base_dir: Path) -> list[dict]:
    pairs = []
    for dataset in DATASETS:
        droot = base_dir / dataset
        if not droot.exists():
            continue
        video_dirs = sorted(
            (d for d in droot.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: int(d.name),
        )
        for vdir in video_dirs:
            for adir in sorted(d for d in vdir.iterdir() if d.is_dir()):
                qa_file = adir / "QA results" / "finalQA.json"
                if not qa_file.exists():
                    continue
                with open(qa_file, encoding="utf-8") as f:
                    items = json.load(f)
                for i, item in enumerate(items):
                    question = str(item.get("question", "")).strip()
                    answer = str(item.get("answer", "")).strip()
                    if not question or not answer:
                        continue
                    pair = {
                        # Same uid scheme as the notebook, so checkpoints and
                        # results stay comparable across both tools.
                        "uid": f"{dataset}_v{vdir.name}_{adir.name}_q{i + 1}",
                        "dataset": dataset,
                        "video": int(vdir.name),
                        "approach": adir.name,
                        "question": question,
                        "answer": answer,
                    }
                    # Only DualAgent and MultiAgent record timestamps. The UI
                    # uses these for source-video clips when available.
                    start = item.get("time_start_sec")
                    if isinstance(start, (int, float)):
                        pair["t"] = int(start)
                    end = item.get("time_end_sec")
                    if isinstance(end, (int, float)):
                        pair["te"] = int(end)
                    pairs.append(pair)
    return pairs


def load_video_urls(base_dir: Path) -> dict:
    """Read the source video URL for each video index from the dataset CSVs."""
    videos: dict[str, dict[str, dict]] = {}
    for dataset, csv_name in DATASET_CSV.items():
        csv_path = base_dir / csv_name
        if not csv_path.exists():
            print(f"  ! {csv_name} not found — {dataset} videos will have no links")
            continue
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                idx, url = (row.get("index") or "").strip(), (row.get("url") or "").strip()
                if not idx.isdigit() or not url:
                    continue
                videos.setdefault(dataset, {})[idx] = {
                    "url": url,
                    "language": (row.get("language") or "").strip(),
                }
    return videos


def main() -> None:
    base_dir = find_repo_root()
    pairs = extract(base_dir)
    if not pairs:
        raise SystemExit("No finalQA.json files found — nothing to extract.")

    video_urls = load_video_urls(base_dir)

    out_path = base_dir / "eval" / "web" / "qa_data.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"pairs": pairs, "videos": video_urls}, f,
                  ensure_ascii=False, separators=(",", ":"))

    counts: dict[tuple[str, str], int] = {}
    vids_seen: dict[tuple[str, str], set] = {}
    for p in pairs:
        key = (p["dataset"], p["approach"])
        counts[key] = counts.get(key, 0) + 1
        vids_seen.setdefault(key, set()).add(p["video"])

    print(f"Repo root: {base_dir}")
    print(f"Wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB)\n")
    print(f"{'Dataset':<10} {'Approach':<26} {'Videos':>7} {'QA pairs':>9}")
    print("-" * 55)
    for (dataset, approach), n in sorted(counts.items()):
        print(f"{dataset:<10} {approach:<26} {len(vids_seen[(dataset, approach)]):>7} {n:>9}")
    print("-" * 55)
    print(f"{'TOTAL':<44} {len(pairs):>9}")

    linked = {d: len(v) for d, v in video_urls.items()}
    timestamped = sum(1 for p in pairs if "t" in p)
    print(f"\nVideo links: {linked or 'none'}")
    print(f"Pairs with a timestamp: {timestamped} / {len(pairs)} "
          f"(used for embedded source clips when available)")
    missing = sorted({(p["dataset"], p["video"]) for p in pairs
                      if str(p["video"]) not in video_urls.get(p["dataset"], {})})
    if missing:
        print(f"! No URL for: {missing}")


if __name__ == "__main__":
    main()
