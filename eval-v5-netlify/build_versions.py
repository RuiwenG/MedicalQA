#!/usr/bin/env python3
"""Build the five 20-Q&A versions of the v5 human evaluation.

v5 hands each annotator one version of 20 Q&As. This script turns a selection
of 100 Qwen3-14B pairs (five lists of 20 uids) into everything the site and the
database need, so the two can never disagree:

``qa_data.json``
    The 100 selected pairs (question, answer, source clip) and the video links
    for the videos they come from. Nothing else is shipped to the browser.
``versions.json``
    ``{"versions": {"1": [uid, ...], ...}, "provisional": bool}`` — the 20 uids
    of each version, in the order they were listed.
``manifest.json``
    Source commit, counts and SHA-256 of both files above.
``../eval-v5-sql/04_load_version_pairs_v5.sql``
    Loads the same 100 uids into ``public.version_pairs_v5``.

Only the paper's main generator is accepted: approaches
``SingleAgent-qwen3-14b-v1``/``-v2``/``-v3``. The selection file is JSON:

    {"versions": [[20 uids], [20 uids], [20 uids], [20 uids], [20 uids]]}

    python eval-v5-netlify/build_versions.py --selection my_selection.json

Until the research team has chosen the 100 pairs, ``--provisional`` builds a
placeholder spread over the original 25 videos and the three prompt versions.
It is marked provisional in every output and the site says so on screen; it is
for testing only.

    python eval-v5-netlify/build_versions.py --provisional
"""
import argparse
import csv
import hashlib
import json
import random
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SQL_OUT = ROOT / "eval-v5-sql" / "04_load_version_pairs_v5.sql"

N_VERSIONS = 5
PAIRS_PER_VERSION = 20
APPROACHES = ["SingleAgent-qwen3-14b-v1", "SingleAgent-qwen3-14b-v2", "SingleAgent-qwen3-14b-v3"]

# Every dataset with Qwen3-14B pairs, and the CSV holding its video links.
DATASET_CSV = {
    "Master": "test_dataset.csv",
    "Teepa": "teepa.csv",
    "FTD": "ftd.csv",
    "GEMS": "gems.csv",
    "ODJ": "odj.csv",
    "SUP": "sup.csv",
}
# The provisional placeholder uses only the original 25 videos.
PROVISIONAL_DATASETS = ["Master", "Teepa"]


def seconds(value):
    """Qwen3-14B writes time_start_sec/time_end_sec as strings ("35")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def load_pairs() -> dict:
    """Every Qwen3-14B pair in the repo, keyed by uid (same scheme as v4)."""
    pairs = {}
    for dataset in DATASET_CSV:
        droot = ROOT / dataset
        if not droot.exists():
            continue
        for vdir in sorted((d for d in droot.iterdir() if d.is_dir() and d.name.isdigit()),
                           key=lambda d: int(d.name)):
            for approach in APPROACHES:
                qa_file = vdir / approach / "QA results" / "finalQA.json"
                if not qa_file.exists():
                    continue
                with open(qa_file, encoding="utf-8") as f:
                    items = json.load(f)
                for i, item in enumerate(items):
                    question = str(item.get("question", "")).strip()
                    answer = str(item.get("answer", "")).strip()
                    if not question or not answer:
                        continue
                    uid = f"{dataset}_v{vdir.name}_{approach}_q{i + 1}"
                    pair = {"uid": uid, "dataset": dataset, "video": int(vdir.name),
                            "approach": approach, "question": question, "answer": answer}
                    start, end = seconds(item.get("time_start_sec")), seconds(item.get("time_end_sec"))
                    if start is not None:
                        pair["t"] = start
                        pair["ts"] = "model"
                        if end is not None and end >= start:
                            pair["te"] = end
                    pairs[uid] = pair
    return pairs


def load_video_urls() -> dict:
    videos = {}
    for dataset, csv_name in DATASET_CSV.items():
        path = ROOT / csv_name
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                idx, url = (row.get("index") or "").strip(), (row.get("url") or "").strip()
                if idx.isdigit() and url:
                    videos.setdefault(dataset, {})[idx] = {
                        "url": url, "language": (row.get("language") or "").strip()}
    return videos


def provisional_selection(pairs: dict) -> list[list[str]]:
    """Four pairs from each of the 25 original videos, spread over v1/v2/v3.

    Video i's four pairs go to versions i, i+1, i+2, i+3 (mod 5), so every
    version gets 20 pairs from 20 different videos. Seeded, so the placeholder
    is the same on every machine.
    """
    rng = random.Random(42)
    videos = sorted({(p["dataset"], p["video"]) for p in pairs.values()
                     if p["dataset"] in PROVISIONAL_DATASETS},
                    key=lambda dv: (PROVISIONAL_DATASETS.index(dv[0]), dv[1]))
    if len(videos) != 25:
        raise SystemExit(f"expected the 25 original videos, found {len(videos)}")
    versions = [[] for _ in range(N_VERSIONS)]
    for i, (dataset, video) in enumerate(videos):
        chosen = []
        for j in range(4):
            approach = APPROACHES[(i + j) % len(APPROACHES)]
            pool = sorted(u for u, p in pairs.items()
                          if p["dataset"] == dataset and p["video"] == video
                          and p["approach"] == approach and u not in chosen)
            chosen.append(rng.choice(pool))
        for j, uid in enumerate(chosen):
            versions[(i + j) % N_VERSIONS].append(uid)
    return versions


def check_selection(versions, pairs):
    if len(versions) != N_VERSIONS:
        raise SystemExit(f"need {N_VERSIONS} versions, got {len(versions)}")
    seen = set()
    for n, uids in enumerate(versions, 1):
        if len(uids) != PAIRS_PER_VERSION:
            raise SystemExit(f"version {n} has {len(uids)} Q&As, needs {PAIRS_PER_VERSION}")
        for uid in uids:
            if uid not in pairs:
                raise SystemExit(f"version {n}: {uid} is not a Qwen3-14B pair in this repo")
            if uid in seen:
                raise SystemExit(f"{uid} appears in more than one place")
            seen.add(uid)


def sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def write_sql(versions, pairs, provisional, data_sha):
    rows = []
    for n, uids in enumerate(versions, 1):
        for pos, uid in enumerate(uids, 1):
            p = pairs[uid]
            rows.append(f"    ({n}, {pos}, {sql_text(uid)}, {sql_text(p['dataset'])}, "
                        f"{p['video']}, {sql_text(p['approach'])})")
    note = ("-- PROVISIONAL placeholder selection for testing only. Do not load it into\n"
            "-- the live database: rebuild with the agreed selection first.\n"
            if provisional else "")
    values_sql = ",\n".join(rows)
    SQL_OUT.write_text(f"""-- Generated by eval-v5-netlify/build_versions.py; do not edit by hand.
-- Loads the 5 x 20 Q&As of the v5 study into public.version_pairs_v5.
-- qa_data.json sha256: {data_sha}
{note}--
-- Run once, after 01_create_v5.sql and before the site goes live. It refuses
-- to run if any version pair is already loaded or anyone has been assigned.

begin;

do $$
begin
    if exists (select 1 from public.version_pairs_v5)
       or exists (select 1 from public.assignments_v5) then
        raise exception
            'version_pairs_v5 is already loaded or assignments exist; nothing changed';
    end if;
end
$$;

insert into public.version_pairs_v5
    (eval_version, position, qa_uid, dataset, video, approach)
values
{values_sql};

do $$
begin
    if (select count(*) from public.version_pairs_v5) <> 100 then
        raise exception 'expected 100 version pairs';
    end if;
end
$$;

commit;
""", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--selection", type=Path, help="JSON file with five lists of 20 uids")
    src.add_argument("--provisional", action="store_true",
                     help="placeholder selection for testing (marked provisional everywhere)")
    args = ap.parse_args()

    pairs = load_pairs()
    if args.provisional:
        versions = provisional_selection(pairs)
    else:
        versions = json.loads(args.selection.read_text(encoding="utf-8"))["versions"]
    check_selection(versions, pairs)

    selected = [pairs[u] for uids in versions for u in uids]
    all_videos = load_video_urls()
    videos = {}
    for p in selected:
        link = all_videos.get(p["dataset"], {}).get(str(p["video"]))
        if link:
            videos.setdefault(p["dataset"], {})[str(p["video"])] = link

    data_bytes = json.dumps({"pairs": selected, "videos": videos},
                            ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    versions_bytes = json.dumps({"provisional": args.provisional,
                                 "versions": {str(n): uids for n, uids in enumerate(versions, 1)}},
                                ensure_ascii=False, indent=1).encode("utf-8")
    (HERE / "qa_data.json").write_bytes(data_bytes)
    (HERE / "versions.json").write_bytes(versions_bytes)
    data_sha = hashlib.sha256(data_bytes).hexdigest()
    write_sql(versions, pairs, args.provisional, data_sha)

    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    by_approach, by_video = {}, set()
    for p in selected:
        by_approach[p["approach"]] = by_approach.get(p["approach"], 0) + 1
        by_video.add((p["dataset"], p["video"]))
    manifest = {
        "study_version": "v5",
        "ratings_table": "ratings_v5",
        "provisional": args.provisional,
        "source_commit": commit,
        "generator": "Qwen3-14B (SingleAgent-qwen3-14b-v1/v2/v3)",
        "n_versions": N_VERSIONS,
        "pairs_per_version": PAIRS_PER_VERSION,
        "total_pairs": len(selected),
        "videos": len(by_video),
        "pairs_by_prompt_version": dict(sorted(by_approach.items())),
        "pairs_with_timestamp": sum(1 for p in selected if "t" in p),
        "qa_data_sha256": data_sha,
        "versions_sha256": hashlib.sha256(versions_bytes).hexdigest(),
        "notes": ("PROVISIONAL placeholder selection for testing; replace before launch."
                  if args.provisional else "Agreed v5 selection."),
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"{'PROVISIONAL ' if args.provisional else ''}v5 selection: {len(selected)} Q&As "
          f"from {len(by_video)} videos, {by_approach}")
    print(f"Wrote qa_data.json, versions.json, manifest.json and {SQL_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
