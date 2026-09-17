#!/usr/bin/env python3
"""Offline regression checks for the timestamp-only original-v1 deployment."""
import copy
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "eval-v1-netlify"
BASELINE = "b18cbdaba4b40fd3425156d419a80e4ee8ab3d08"
spec = importlib.util.spec_from_file_location("v1_builder", DEPLOY / "build_v1_data.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def historical(path):
    return subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], cwd=ROOT)


class TimestampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = builder.load_historical_corpus(ROOT)
        cls.data = json.loads((DEPLOY / "qa_data.json").read_text())
        cls.sidecar = json.loads((DEPLOY / "aligned_timestamps.json").read_text())

    def test_only_timestamp_metadata_changed(self):
        cleaned = {**self.data, "pairs": [
            {key: value for key, value in pair.items() if key not in {"t", "te", "ts"}}
            for pair in self.data["pairs"]
        ]}
        self.assertEqual(cleaned, self.original)
        self.assertEqual(len(self.data["pairs"]), 487)

    def test_sidecar_reproduces_entire_corpus(self):
        self.assertEqual(builder.merge_timestamps(self.original, self.sidecar, ROOT), self.data)
        for pair in self.data["pairs"]:
            self.assertIn(pair["ts"], {"aligned", "aligned-low"})

    def test_first_40_assignment_is_unchanged(self):
        def ordered(pairs):
            return sorted(pairs, key=lambda p: (builder.qa_index(p), p["video"], p["dataset"], p["approach"], p["uid"]))[:40]
        old, new = ordered(self.original["pairs"]), ordered(self.data["pairs"])
        self.assertEqual([p["uid"] for p in old], [p["uid"] for p in new])
        self.assertEqual(len({(p["dataset"], p["video"]) for p in new}), 25)

    def test_no_configuration_or_schema_changes(self):
        for path in ["eval-v1-netlify/config.js", "eval-v1-netlify/supabase_schema.sql"]:
            self.assertEqual((ROOT / path).read_bytes(), historical(path))

    def test_ui_changes_are_limited_to_video_helpers(self):
        def without_video_helpers(html):
            for start, end in [("function sourceVideo(", "function escapeAttr("),
                               ("function insertVideoPanel(", "function renderRate(")]:
                left, right = html.index(start), html.index(end)
                html = html[:left] + html[right:]
            return html
        before = historical("eval-v1-netlify/index.html").decode()
        after = (DEPLOY / "index.html").read_text()
        self.assertEqual(without_video_helpers(before), without_video_helpers(after))

    def test_invalid_sidecars_are_rejected(self):
        for case in ["corpus", "missing_uid", "negative_start", "beyond_video", "bad_flag"]:
            bad = copy.deepcopy(self.sidecar)
            uid = self.original["pairs"][0]["uid"]
            if case == "corpus":
                bad["corpus_sha256"] = "wrong-corpus"
            elif case == "missing_uid":
                bad["pairs"].pop(uid)
            elif case == "negative_start":
                bad["pairs"][uid]["t"] = -1
            elif case == "beyond_video":
                bad["pairs"][uid]["te"] = 999999
            else:
                bad["pairs"][uid]["low"] = "false"
            with self.subTest(case=case), self.assertRaises(ValueError):
                builder.merge_timestamps(self.original, bad, ROOT)

    def test_reviewed_ranges_and_archive(self):
        overrides = json.loads((DEPLOY / "timestamp_overrides.json").read_text())["pairs"]
        self.assertEqual(len(overrides), 9)
        for uid, expected in overrides.items():
            for key in ("t", "te", "note"):
                self.assertEqual(self.sidecar["pairs"][uid][key], expected[key])
        with ZipFile(ROOT / "eval-v1-netlify.zip") as archive:
            expected_files = {p.name for p in DEPLOY.iterdir() if p.is_file() and not p.name.startswith(".")}
            self.assertEqual(set(archive.namelist()), expected_files)
            for name in expected_files:
                self.assertEqual(archive.read(name), (DEPLOY / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
