"""Videos left out of analysis (see excluded_videos.json for why).

Nothing is deleted: the video folders, QA pairs and judge CSVs stay in the repo,
and scripts filter on this list instead.
"""
import json
from pathlib import Path

_FILE = Path(__file__).with_name("excluded_videos.json")
EXCLUDED = {(ds, int(v)) for ds, vids in json.loads(_FILE.read_text())["videos"].items() for v in vids}


def is_excluded(dataset, video):
    return (dataset, int(video)) in EXCLUDED
