"""Build a judge input (qa_data.json format) from one approach's finalQA.json files.

Every pair of --approach in the kept videos (eval/excluded_videos.json) goes in, with
the generator's own timestamps as t/te. Video links and languages are copied from the
existing judge inputs. Videos with no finalQA.json are skipped and listed. Pair uids
match the topic-label pair ids ("<Dataset>_v<video>_<approach>_q<n>").

Usage: python eval/llm_judge/build_qa_data.py --approach SingleAgent-qwen3.5-9b-v3
       -> eval/llm_judge/qa_data_qwen3.5-9b-v3.json
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from exclusions import is_excluded  # noqa: E402

DATASETS = ["Master", "Teepa", "FTD", "GEMS", "ODJ", "SUP"]
VIDEO_SOURCES = ["qa_data_qwen3-14b.json", "qa_data_qwen3-14b-new69.json"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--approach", required=True, help="output folder name inside each video")
    args = ap.parse_args()
    known = {}
    for name in VIDEO_SOURCES:
        for ds, vids in json.loads((HERE / name).read_text())["videos"].items():
            known.setdefault(ds, {}).update(vids)

    pairs, videos, missing = [], {}, []
    for ds in DATASETS:
        for vdir in sorted((p for p in (REPO / ds).iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
            if is_excluded(ds, vdir.name):
                continue
            final = vdir / args.approach / "QA results" / "finalQA.json"
            if not final.exists():
                missing.append(f"{ds} {vdir.name}")
                continue
            videos.setdefault(ds, {})[vdir.name] = known[ds][vdir.name]
            for n, qa in enumerate(json.loads(final.read_text()), 1):
                pairs.append({"uid": f"{ds}_v{vdir.name}_{args.approach}_q{n}", "dataset": ds,
                              "video": int(vdir.name), "approach": args.approach,
                              "question": qa["question"].strip(), "answer": qa["answer"].strip(),
                              "t": qa["time_start_sec"], "te": qa["time_end_sec"], "ts": "model"})
    out = HERE / f"qa_data_{args.approach.removeprefix('SingleAgent-')}.json"
    out.write_text(json.dumps({"pairs": pairs, "videos": videos}, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(pairs)} pairs from {sum(map(len, videos.values()))} videos "
          f"(no finalQA.json: {missing or 'none'}) -> {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
