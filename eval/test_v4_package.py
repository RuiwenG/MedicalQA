#!/usr/bin/env python3
"""Regression checks for the isolated v4 Netlify and Supabase packages."""

import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "eval-v4-netlify"
SQL = ROOT / "eval-v4-sql"
ARCHIVE = ROOT / "eval-v4-netlify.zip"


class V4PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (DEPLOY / "index.html").read_text(encoding="utf-8")
        cls.config = (DEPLOY / "config.js").read_text(encoding="utf-8")
        cls.data_bytes = (DEPLOY / "qa_data.json").read_bytes()
        cls.data = json.loads(cls.data_bytes)
        cls.manifest = json.loads((DEPLOY / "manifest.json").read_text())
        cls.schema = (SQL / "01_create_ratings_v4.sql").read_text(encoding="utf-8")

    def test_javascript_syntax(self):
        scripts = re.findall(
            r"<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", self.html
        )
        self.assertTrue(scripts)
        checker = (
            "const vm=require('node:vm'),fs=require('node:fs');"
            "new vm.Script(fs.readFileSync(0,'utf8'));"
        )
        proc = subprocess.run(
            ["node", "-e", checker],
            input="\n".join(scripts),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_v4_config_and_storage_are_isolated(self):
        self.assertIn('RATINGS_TABLE: "ratings_v4"', self.config)
        self.assertIn('STUDY_VERSION: "v4"', self.config)
        self.assertIn("MAX_PAIRS: 40", self.config)
        self.assertNotIn("ratings_v3", self.html)
        self.assertIn('storeKey: "dqa:" + slug(CFG.STUDY_VERSION', self.html)
        self.assertIn("version: 7", self.html)
        self.assertIn('const LAST_OPTS_KEY = "dqa:lastopts:"', self.html)

    def test_latest_ui_features_are_present(self):
        for marker in (
            "optionalIssues: true",
            'otherOption: "Other"',
            "Dictation (speech to text)",
            "METRICS_BEFORE_VIDEO",
        ):
            self.assertIn(marker, self.html)

    def test_corpus_and_manifest(self):
        pairs = self.data["pairs"]
        active = [p for p in pairs if p["approach"] == "SingleAgent"]
        videos = {(p["dataset"], p["video"]) for p in active}
        self.assertEqual(len(pairs), 2634)
        self.assertEqual(len(active), 226)
        self.assertEqual(len(videos), 25)
        self.assertEqual(len({p["uid"] for p in pairs}), len(pairs))
        digest = hashlib.sha256(self.data_bytes).hexdigest()
        self.assertEqual(self.manifest["qa_data_sha256"], digest)
        self.assertEqual(self.manifest["total_pairs_in_file"], len(pairs))
        self.assertEqual(self.manifest["active_pairs"], len(active))
        self.assertEqual(self.manifest["active_videos"], len(videos))

    def test_schema_is_v4_and_non_destructive(self):
        lowered = self.schema.lower()
        self.assertNotIn("drop table", lowered)
        self.assertNotIn("drop view", lowered)
        self.assertNotIn("ratings_v3", lowered)
        self.assertIn("create table public.ratings_v4", lowered)
        self.assertIn("create view public.ratings_v4_final", lowered)
        self.assertIn("with check (study_version = 'v4')", lowered)
        for prefix in (
            "qna_trustworthiness",
            "qna_clarity",
            "qna_usefulness",
            "qna_care_safety",
            "qna_standalone",
        ):
            self.assertIn(prefix + "_issue", self.schema)
            self.assertIn(prefix + "_binary", self.schema)

    def test_archive_matches_deploy_folder(self):
        self.assertTrue(ARCHIVE.exists(), "build eval-v4-netlify.zip")
        expected = {
            p.name for p in DEPLOY.iterdir() if p.is_file() and not p.name.startswith(".")
        }
        with ZipFile(ARCHIVE) as archive:
            self.assertEqual(set(archive.namelist()), expected)
            for name in expected:
                self.assertEqual(archive.read(name), (DEPLOY / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
