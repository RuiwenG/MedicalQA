"""Videos left out of analysis (see excluded_videos.json for why).

The video folders and their rows in sup.csv / gems.csv were deleted on 2026-11-26.
Judge CSVs in eval/results written before then still contain these videos, so
scripts that read those results keep filtering on this list.
"""
import json
from pathlib import Path

_FILE = Path(__file__).with_name("excluded_videos.json")
EXCLUDED = {(ds, int(v)) for ds, vids in json.loads(_FILE.read_text())["videos"].items() for v in vids}


def is_excluded(dataset, video):
    return (dataset, int(video)) in EXCLUDED
