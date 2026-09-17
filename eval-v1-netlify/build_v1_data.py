#!/usr/bin/env python3
"""Rebuild the first-deployed SingleAgent evaluation corpus from Git history.

The first Supabase-connected Netlify package was commit 2323ee9. Its
``qa_data.json`` contains the same SingleAgent pairs as generation commit
``d580199``: the original fixed-20, pre-four-criteria SingleAgent output.

Run this script from anywhere inside the MedicalQA repository. It writes only
this deployment folder's ``qa_data.json``.
"""

import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from pathlib import Path


SOURCE_REV = "2323ee99760ebe2120468374670192c8cff38891"
SOURCE_PATH = "eval-pilot-netlify-review/qa_data.json"
SOURCE_APPROACH = "SingleAgent"
DEPLOYMENT_APPROACH = "SingleAgent-v1"


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit("Could not find the MedicalQA Git repository.")


def qa_index(pair: dict) -> int:
    match = re.search(r"_q(\d+)$", str(pair.get("uid", "")))
    return int(match.group(1)) if match else 10**9


def load_historical_corpus(root: Path) -> dict:
    raw = subprocess.check_output(
        ["git", "show", f"{SOURCE_REV}:{SOURCE_PATH}"],
        cwd=root,
        text=True,
    )
    source = json.loads(raw)

    pairs = []
    for original in source["pairs"]:
        if original.get("approach") != SOURCE_APPROACH:
            continue
        pair = dict(original)
        pair["approach"] = DEPLOYMENT_APPROACH
        pair["uid"] = re.sub(
            r"_SingleAgent_q(\d+)$",
            rf"_{DEPLOYMENT_APPROACH}_q\1",
            str(pair["uid"]),
        )
        pairs.append(pair)

    if len(pairs) != 487:
        raise SystemExit(f"Expected 487 historical SingleAgent pairs, found {len(pairs)}.")
    if len({pair["uid"] for pair in pairs}) != len(pairs):
        raise SystemExit("Historical corpus contains duplicate UIDs.")

    return {"pairs": pairs, "videos": source["videos"]}


def corpus_digest(corpus: dict) -> str:
    return hashlib.sha256(
        json.dumps(corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def merge_timestamps(corpus: dict, sidecar: dict, root: Path) -> dict:
    """Fail closed if an alignment was made for different Q&As or transcripts."""
    if sidecar.get("source_revision") != SOURCE_REV or sidecar.get("corpus_sha256") != corpus_digest(corpus):
        raise ValueError("Timestamp sidecar does not match the original v1 corpus.")
    expected_transcripts = {
        f'{p["dataset"]}/{p["video"]}/Transcript/transcript-en.json' for p in corpus["pairs"]
    }
    if set(sidecar.get("transcripts", {})) != expected_transcripts:
        raise ValueError("Timestamp sidecar has missing or unexpected transcripts.")
    ends = {}
    for path, digest in sidecar["transcripts"].items():
        raw = (root / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"Transcript changed since alignment: {path}")
        ends[path] = round(max(seg["end"] for seg in json.loads(raw)))
    override_path = root / "eval-v1-netlify/timestamp_overrides.json"
    if hashlib.sha256(override_path.read_bytes()).hexdigest() != sidecar.get("overrides_sha256"):
        raise ValueError("Timestamp overrides changed; rerun the v1 alignment first.")
    windows = sidecar["pairs"]
    if set(windows) != {p["uid"] for p in corpus["pairs"]}:
        raise ValueError("Timestamp sidecar has missing or unexpected Q&A IDs.")
    pairs = []
    for original in corpus["pairs"]:
        window = windows[original["uid"]]
        start, end = window["t"], window["te"]
        transcript = f'{original["dataset"]}/{original["video"]}/Transcript/transcript-en.json'
        if not (type(start) is int and type(end) is int and 0 <= start < end <= ends[transcript]):
            raise ValueError(f'Invalid timestamp range: {original["uid"]}')
        if not math.isfinite(window["score"]) or window["score"] < 0:
            raise ValueError(f'Invalid alignment score: {original["uid"]}')
        if type(window["low"]) is not bool:
            raise ValueError(f'Invalid confidence flag: {original["uid"]}')
        pairs.append({**original, "t": start, "te": end,
                      "ts": "aligned-low" if window["low"] else "aligned"})
    return {**corpus, "pairs": pairs}


def main() -> None:
    root = repo_root()
    output = load_historical_corpus(root)
    destination = Path(__file__).resolve().parent / "qa_data.json"
    sidecar_path = destination.with_name("aligned_timestamps.json")
    if sidecar_path.exists():
        output = merge_timestamps(output, json.loads(sidecar_path.read_text(encoding="utf-8")), root)
    pairs = output["pairs"]
    destination.write_text(
        json.dumps(output, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    first_40 = sorted(
        pairs,
        key=lambda pair: (
            qa_index(pair),
            pair["video"],
            pair["dataset"],
            pair["approach"],
            pair["uid"],
        ),
    )[:40]
    distribution = Counter((pair["dataset"], pair["video"]) for pair in first_40)

    print(f"Wrote {destination}")
    print(f"Pairs: {len(pairs)}")
    print(f"First 40 cover {len(distribution)} videos: {dict(sorted(distribution.items()))}")


if __name__ == "__main__":
    main()
