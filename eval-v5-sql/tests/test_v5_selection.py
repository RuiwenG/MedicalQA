"""Check the approved pilot selection and reproducible deployment packages offline."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "eval-v5-netlify"
SQL = ROOT / "eval-v5-sql"
REVIEW = ROOT / "eval/topics/runs/review_set_qwen3.5-9b-25"
read_json = lambda p: json.loads(p.read_text(encoding="utf-8"))
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


class PilotSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection = read_json(SITE / "selection.json")
        cls.versions = read_json(SITE / "versions.json")
        cls.data = read_json(SITE / "qa_data.json")
        cls.pairs = {p["uid"]: p for p in cls.data["pairs"]}
        cls.manifest = read_json(SITE / "manifest.json")
        cls.transcripts = read_json(SITE / "transcripts.json")
        span_dir = ROOT / "eval/widened_timestamps"
        cls.spans = {
            approach: read_json(span_dir / f"{approach}.json")["spans"]
            for approach in ("SingleAgent-qwen3.5-9b-v3", "SingleAgent-qwen3.5-9b-bad-v3")
        }
        cls.groups = [[] for _ in range(5)]
        for name in ("selection_normal.json", "selection_bad.json"):
            for i, group in enumerate(read_json(REVIEW / name)["groups"]):
                cls.groups[i].extend(group)

    def test_approved_group_membership_and_mix(self):
        self.assertEqual(len(self.pairs), 25)
        self.assertFalse(self.versions["provisional"])
        self.assertEqual(self.versions["pairs_per_version"], 5)
        self.assertEqual(list(self.versions["versions"]), [str(i) for i in range(1, 6)])
        seen = set()
        for i, group in enumerate(self.groups, 1):
            uids = self.versions["versions"][str(i)]
            self.assertEqual(uids, self.selection["versions"][i - 1])
            self.assertEqual(set(uids), {p["pair_id"] for p in group})
            self.assertEqual(Counter(p["class"] for p in group), Counter(high=3, low=1, bad=1))
            self.assertFalse(seen.intersection(uids))
            self.assertEqual(len({(self.pairs[u]["dataset"], self.pairs[u]["video"]) for u in uids}), 5)
            seen.update(uids)
        self.assertEqual(seen, set(self.pairs))

    def test_original_text_and_widened_timestamps(self):
        for uid, pair in self.pairs.items():
            ds, video, approach, n = re.fullmatch(r"(.+)_v(\d+)_(SingleAgent-.+)_q(\d+)", uid).groups()
            original = read_json(ROOT / ds / video / approach / "QA results/finalQA.json")[int(n) - 1]
            self.assertEqual(pair["question"], original["question"].strip())
            self.assertEqual(pair["answer"], original["answer"].strip())
            for label in ("class", "intended_failure", "judge_scores"):
                self.assertNotIn(label, pair)
            source = self.data["videos"][ds][video]["url"]
            self.assertIn("youtu", source)
            span = self.spans[approach][uid]
            self.assertEqual((pair["t"], pair["te"]), (span["t"], span["te"]))
            self.assertEqual((pair["t_model"], pair["te_model"]),
                             (original["time_start_sec"], original["time_end_sec"]))
            self.assertEqual(pair["ts"], "widened")
            self.assertLess(pair["t"], pair["te"])

    def test_manifest_and_sql_match_deployed_assets(self):
        manifest = self.manifest
        self.assertFalse(manifest["provisional"])
        self.assertEqual((manifest["n_versions"], manifest["pairs_per_version"], manifest["total_pairs"]), (5, 5, 25))
        self.assertEqual(manifest["pairs_with_widened_timestamp"], 25)
        self.assertEqual(manifest["qa_data_sha256"], sha(SITE / "qa_data.json"))
        self.assertEqual(manifest["transcripts_sha256"], sha(SITE / "transcripts.json"))
        self.assertEqual(manifest["versions_sha256"], sha(SITE / "versions.json"))
        self.assertEqual(manifest["selection_sha256"], sha(SITE / "selection.json"))
        loader = (SQL / "04_load_version_pairs_v5.sql").read_text()
        self.assertNotIn("PROVISIONAL", loader)
        self.assertIn(manifest["qa_data_sha256"], loader)
        self.assertIn("set pairs_per_version = 5", loader)
        for i, uids in enumerate(self.selection["versions"], 1):
            for position, uid in enumerate(uids, 1):
                self.assertIn(f"({i}, {position}, '{uid}'", loader)

    def test_transcripts_match_only_selected_video_sources(self):
        selected = {(p["dataset"], str(p["video"])) for p in self.pairs.values()}
        shipped = {(ds, v) for ds, videos in self.transcripts.items() for v in videos}
        self.assertEqual(shipped, selected)
        for dataset, video in selected:
            original = read_json(ROOT / dataset / video / "Transcript/transcript-en.json")
            expected = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                        for s in original if s["text"].strip()]
            self.assertEqual(self.transcripts[dataset][video], expected)
        for pair in self.pairs.values():
            segments = self.transcripts[pair["dataset"]][str(pair["video"])]
            self.assertTrue(any(s["end"] > pair["t"] and s["start"] < pair["te"] for s in segments), pair["uid"])

    def test_builder_reproduces_and_protects_frozen_groups(self):
        with tempfile.TemporaryDirectory(prefix="medicalqa-pilot-build-", dir="/private/tmp") as folder:
            root = Path(folder)
            (root / "site").mkdir()
            (root / "sql").mkdir()
            source = root / "selection.json"
            source.write_bytes((SITE / "selection.json").read_bytes())
            command = [sys.executable, str(SITE / "build_versions.py"), "--selection", str(source),
                       "--site-dir", str(root / "site"), "--sql-dir", str(root / "sql")]
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            subprocess.run(command, check=True, capture_output=True, env=env)
            for name in ("qa_data.json", "transcripts.json", "versions.json"):
                self.assertEqual((root / "site" / name).read_bytes(), (SITE / name).read_bytes())
            # A fresh build records its checkout even when the study inputs are identical.
            source_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            self.assertEqual(read_json(root / "site" / "manifest.json"),
                             {**self.manifest, "source_commit": source_commit})
            self.assertEqual((root / "sql/04_load_version_pairs_v5.sql").read_bytes(),
                             (SQL / "04_load_version_pairs_v5.sql").read_bytes())
            before = sha(root / "site/qa_data.json")
            changed = json.loads(source.read_text())
            changed["versions"][0][0], changed["versions"][0][1] = changed["versions"][0][1], changed["versions"][0][0]
            source.write_text(json.dumps(changed))
            result = subprocess.run(command, capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("differs from the built selection", result.stderr)
            self.assertEqual(sha(root / "site/qa_data.json"), before)

    def test_archives_match_sources(self):
        for name, folder in (("eval-v5-netlify.zip", SITE), ("eval-v5-sql.zip", SQL),
                             ("eval-v5-cloudflare.zip", ROOT / "eval-v5-cloudflare")):
            with ZipFile(ROOT / name) as archive:
                names = archive.namelist()
                self.assertTrue(all("__pycache__" not in n and not n.startswith("/") for n in names))
                for path in folder.rglob("*"):
                    if path.is_file() and "__pycache__" not in path.parts and path.name != ".DS_Store":
                        rel = path.relative_to(folder).as_posix()
                        self.assertEqual(archive.read(rel), path.read_bytes(), f"{name}: {rel}")
                if folder == SQL:
                    self.assertEqual(archive.read("eval-v5-DEPLOY.md"), (ROOT / "eval-v5-DEPLOY.md").read_bytes())
        for name in ("index.html", "config.js", "qa_data.json", "transcripts.json", "versions.json", "_headers"):
            self.assertEqual((ROOT / "eval-v5-cloudflare" / name).read_bytes(), (SITE / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
