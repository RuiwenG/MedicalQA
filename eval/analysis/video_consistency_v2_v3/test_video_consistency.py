"""Small checks for scoring, clustering, tests, and input validation."""
import csv
import itertools
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import video_consistency as analysis


class StatisticsTests(unittest.TestCase):
    def test_holm_two_primary_tests(self):
        np.testing.assert_allclose(analysis.holm([0.03, 0.01]), [0.03, 0.02])
        np.testing.assert_allclose(analysis.holm([0.9, 0.8]), [1, 1])

    def test_holm_is_monotone_in_sorted_pvalues(self):
        values = [0.04, 0.002, 0.03, 0.9]
        adjusted = analysis.holm(values)
        np.testing.assert_allclose(adjusted, [0.09, 0.008, 0.09, 0.9])

    def test_bootstrap_preserves_source_counts_and_is_reproducible(self):
        videos = [("Master", i) for i in range(1, 11)] + [("Teepa", i) for i in range(1, 16)]
        draws = analysis.bootstrap_indices(videos, 10000, 42)
        self.assertEqual(draws.shape, (10000, 25))
        self.assertTrue(np.all(draws[:, :10] < 10))
        self.assertTrue(np.all(draws[:, 10:] >= 10))
        self.assertTrue(any(len(set(row)) < 25 for row in draws))
        np.testing.assert_array_equal(draws, analysis.bootstrap_indices(videos, 10000, 42))

    def test_bootstrap_zero_and_constant_difference(self):
        draws = np.array([[0, 0, 1], [1, 2, 2]])
        np.testing.assert_allclose(analysis.interval(np.zeros(3), draws), [0, 0])
        np.testing.assert_allclose(analysis.interval(np.full(3, 0.2), draws), [0.2, 0.2])

    def test_swapping_versions_negates_interval(self):
        differences = np.array([-0.2, 0.1, 0.3, 0.5])
        draws = analysis.bootstrap_indices([("Master", i) for i in range(4)], 10000, 42)
        interval = analysis.interval(differences, draws)
        reversed_interval = analysis.interval(-differences, draws)
        np.testing.assert_allclose(reversed_interval, -interval[::-1])

    def test_paired_permutation_is_two_sided_and_handles_ties(self):
        signs = np.array(list(itertools.product([-1, 1], repeat=4)))
        self.assertAlmostEqual(analysis.permutation_p(np.ones(4), signs), 3 / 17)
        self.assertEqual(analysis.permutation_p(np.zeros(4), signs), 1)
        differences = np.array([-0.1, 0.3, 0.4, 0.2])
        self.assertEqual(analysis.permutation_p(differences, signs), analysis.permutation_p(-differences, signs))

    def test_leave_one_out_matches_direct_recalculation(self):
        values = np.array([0.2, -0.1, 0.6, 0.4])
        direct = [np.delete(values, i).mean() for i in range(len(values))]
        np.testing.assert_allclose(analysis.leave_one_out(values), direct)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="medicalqa-stat-tests-")
        self.repo = Path(self.temporary.name)
        self.plan = json.loads((analysis.HERE / "plan.json").read_text())
        directory = self.repo / "eval/llm_judge"
        directory.mkdir(parents=True)
        arms = {f"ordinary-{version}": version for version in ("v2", "v3")}
        videos, pairs = {"Master": {}, "Teepa": {}}, []
        for base, count in (("Master", 10), ("Teepa", 15)):
            for video in range(1, count + 1):
                videos[base][str(video)] = {"url": f"https://example.test/{base}/{video}"}
                for version in ("v2", "v3"):
                    for q in range(2 if base == "Master" and video == 1 else 1):
                        pairs.append({"uid": f"{base}-{video}-{version}-{q}", "dataset": base,
                                      "video": video, "approach": f"ordinary-{version}",
                                      "question": "Question?", "answer": "Answer."})
        (directory / "qa_data_qwen3-14b.json").write_text(json.dumps({"pairs": pairs, "videos": videos}))
        manifest = {"arms": arms, "judges": [{"name": "DeepSeek", "k": "ds", "model": "saved-test",
                                              "runs": ["run1.csv", "run2.csv"]}]}
        (directory / "judges_qwen3-14b_manifest.json").write_text(json.dumps(manifest))
        self.paths = []
        self.rows = []
        for pair in pairs:
            self.rows.append({"QA ID": pair["uid"], "Dataset": pair["dataset"], "Video Index": pair["video"],
                              "Approach": pair["approach"], "Question": pair["question"], "Answer": pair["answer"],
                              "Q&A Standalone Yes/No": "Yes", "Q&A Trustworthiness Attribute": "Good"})
        for name in ("run1.csv", "run2.csv"):
            self.paths.append(self.repo / name)
            self.write_rows(self.paths[-1], self.rows)
        for base in ("Master", "Teepa"):
            (self.repo / base).mkdir()
            for version in ("v2", "v3"):
                metadata = {"prompt_version": version, "model": "qwen/qwen3-14b", "backend": "api",
                            "endpoint": "saved", "provider": "same", "seed": 42, "prompt_format": "filled"}
                (self.repo / base / f"SingleAgent-qwen3-14b-{version}_generation_meta.json").write_text(json.dumps(metadata))

    def tearDown(self):
        self.temporary.cleanup()

    def write_rows(self, path, rows):
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def load(self):
        return analysis.read_model(self.repo, "qwen3-14b", self.plan, {})[0][0]

    def test_average_repeats_before_video_average(self):
        # One repeat rates one Q&A No. That Q&A has score 0.5, its video score 0.75.
        modified = [dict(row) for row in self.rows]
        modified[0]["Q&A Standalone Yes/No"] = "No"
        self.write_rows(self.paths[0], modified)
        group = self.load()
        self.assertEqual(group["scores"][0, 0, 0], 0.75)
        # Equal video weighting is not weighting the 26 Q&As equally.
        self.assertAlmostEqual(group["scores"][0, :, 0].mean(), 24.75 / 25)
        self.assertEqual(group["counts"][0, 0], 2)

    def test_duplicate_rating_is_rejected(self):
        self.write_rows(self.paths[0], self.rows + [self.rows[0]])
        with self.assertRaisesRegex(ValueError, "Duplicate rating"):
            self.load()

    def test_missing_repeat_rating_is_rejected(self):
        self.write_rows(self.paths[0], self.rows[1:])
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            self.load()

    def test_wrong_answer_is_rejected(self):
        modified = [dict(row) for row in self.rows]
        modified[0]["Answer"] = "Different answer."
        self.write_rows(self.paths[0], modified)
        with self.assertRaisesRegex(ValueError, "Answer mismatch"):
            self.load()

    def test_missing_outcome_is_not_zero(self):
        modified = [dict(row) for row in self.rows]
        modified[0]["Q&A Trustworthiness Attribute"] = ""
        self.write_rows(self.paths[0], modified)
        with self.assertRaisesRegex(ValueError, "Invalid/missing"):
            self.load()

    def test_changed_generator_settings_are_rejected(self):
        path = self.repo / "Master/SingleAgent-qwen3-14b-v3_generation_meta.json"
        metadata = json.loads(path.read_text())
        metadata["provider"] = "different"
        path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "generator settings changed"):
            self.load()


if __name__ == "__main__":
    unittest.main()
