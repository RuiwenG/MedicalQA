#!/usr/bin/env python3
"""Protect the ongoing 225-Q&A ratings_v3 study during timestamp updates."""
import copy
import importlib.util
import json
import re
import subprocess
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "eval-pilot-netlify-review"
spec = importlib.util.spec_from_file_location("v3_times", DEPLOY / "update_timestamps.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class TimestampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original, _ = builder.read_snapshot(builder.BASELINE_PATH)
        cls.source, _ = builder.read_snapshot(builder.TIMESTAMP_PATH)
        cls.data = json.loads((DEPLOY / "qa_data.json").read_text())
        cls.overrides = json.loads((DEPLOY / "timestamp_overrides.json").read_text())

    def test_all_text_ids_order_and_videos_are_unchanged(self):
        self.assertEqual(builder.without_timestamps(self.original), builder.without_timestamps(self.data))
        self.assertEqual(len(self.data["pairs"]), 1503)
        self.assertEqual(sum(p["approach"] == "SingleAgent" for p in self.data["pairs"]), 225)

    def test_existing_timestamps_and_inactive_records_are_unchanged(self):
        for old, new in zip(self.original["pairs"], self.data["pairs"]):
            if old["approach"] != "SingleAgent":
                self.assertEqual(old, new)
            else:
                for key in ("t", "te"):
                    if key in old:
                        self.assertEqual(old[key], new[key])
                self.assertIsInstance(new["t"], int)

    def test_rebuild_matches_data_and_audit(self):
        data, pairs, transcripts = builder.merge_timestamps(self.original, self.source, self.overrides)
        self.assertEqual(data, self.data)
        audit = json.loads((DEPLOY / "timestamp_update_audit.json").read_text())
        self.assertEqual(pairs, audit["pairs"])
        self.assertEqual(transcripts, audit["transcripts"])

    def test_first_40_and_added_timestamps(self):
        def first(corpus):
            pairs = [p for p in corpus["pairs"] if p["approach"] == "SingleAgent"]
            return sorted(pairs, key=lambda p: (int(re.search(r"_q(\d+)$", p["uid"])[1]), p["video"], p["dataset"], p["approach"], p["uid"]))[:40]
        before, after = first(self.original), first(self.data)
        self.assertEqual([p["uid"] for p in before], [p["uid"] for p in after])
        self.assertEqual(sum("t" not in p for p in before), 7)
        self.assertEqual(sum("t" in p for p in after), 40)
        self.assertEqual(sum(p["ts"] == "aligned-low" for p in after), 1)

    def test_rejects_mismatched_text_video_and_existing_times(self):
        for case in ("text", "video", "timestamp"):
            source = copy.deepcopy(self.source)
            pair = next(p for p in source["pairs"] if p["approach"] == builder.SOURCE_APPROACH and p["ts"] == "model")
            if case == "text":
                pair["answer"] += " CHANGED"
            elif case == "video":
                source["videos"][pair["dataset"]][str(pair["video"])]["url"] = "wrong-video"
            else:
                pair["t"] += 1
            with self.subTest(case=case), self.assertRaises(ValueError):
                builder.merge_timestamps(self.original, source, self.overrides)

    def test_config_and_sql_not_modified(self):
        for name in ("config.js", "supabase_schema.sql"):
            old = subprocess.check_output(["git", "show", f"{builder.SOURCE_REV}:eval-pilot-netlify-review/{name}"], cwd=ROOT)
            self.assertEqual(old, (DEPLOY / name).read_bytes())

    def test_only_video_helpers_changed_in_ui(self):
        def protected(html):
            for start, end in [("function sourceVideo(", "function escapeAttr("),
                               ("function insertVideoPanel(", "function renderRate(")]:
                html = html[:html.index(start)] + html[html.index(end):]
            return html
        old = subprocess.check_output(["git", "show", f"{builder.SOURCE_REV}:eval-pilot-netlify-review/index.html"], cwd=ROOT, text=True)
        self.assertEqual(protected(old), protected((DEPLOY / "index.html").read_text()))

    def test_archive_matches_folder(self):
        with ZipFile(ROOT / "eval-pilot-netlify-review.zip") as archive:
            files = {p.name for p in DEPLOY.iterdir() if p.is_file() and not p.name.startswith(".")}
            self.assertEqual(set(archive.namelist()), files)
            for name in files:
                self.assertEqual(archive.read(name), (DEPLOY / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
