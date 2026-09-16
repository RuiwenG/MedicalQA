#!/usr/bin/env python3
"""Rebuild the first-deployed SingleAgent evaluation corpus from Git history.

The first Supabase-connected Netlify package was commit 2323ee9. Its
``qa_data.json`` contains the same SingleAgent pairs as generation commit
``d580199``: the original fixed-20, pre-four-criteria SingleAgent output.

Run this script from anywhere inside the MedicalQA repository. It writes only
this deployment folder's ``qa_data.json``.
"""

import json
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


def main() -> None:
    root = repo_root()
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

    output = {"pairs": pairs, "videos": source["videos"]}
    destination = Path(__file__).resolve().parent / "qa_data.json"
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
