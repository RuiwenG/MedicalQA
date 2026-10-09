"""Checks for the offline negative-QA runner; no model or GPU is needed."""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common_utils import config
from SingleQA import run_bad_qa_local as runner
from SingleQA.config.settings import Settings, _STANDALONE, _FORMAT_LABELS, _FORMAT_FILLED


def response_with_pairs(count=4, blank_question=None, blank_answer=None):
    blocks = []
    for number in range(1, count + 1):
        question = "" if number == blank_question else f"Which detail {number}?"
        answer = "" if number == blank_answer else f"Detail {number} is discussed."
        blocks.append(
            f"Question {number}: {question}\n"
            f"Timestamp {number}: 0:00-0:20\n"
            f"Answer {number}: {answer}"
        )
    return "\n\n".join(blocks)


class FakeClient:
    def __init__(self, response, output_budget_reached=False):
        self.response = response
        self.output_budget_reached = output_budget_reached
        self.model = None
        self.loads = 0
        self.calls = []

    def load(self):
        self.loads += 1
        self.model = object()

    def generate(self, messages, settings):
        self.calls.append(messages)
        return self.response, {
            "input_tokens": 100,
            "output_tokens": 100,
            "agent_seconds": 0.01,
            "output_budget_reached": self.output_budget_reached,
        }

    def close(self):
        self.model = None


class BadQATests(unittest.TestCase):
    def setUp(self):
        language = config.LANGUAGE_CODE, config.LANGUAGE_NAME
        self.addCleanup(setattr, config, "LANGUAGE_CODE", language[0])
        self.addCleanup(setattr, config, "LANGUAGE_NAME", language[1])
        config.LANGUAGE_CODE, config.LANGUAGE_NAME = "en", "English"
        self.settings = runner.BadQASettings()
        self.settings.prompt_version = "v3"
        self.settings.prompt_format = "filled"

    def make_job(self, root):
        transcript = root / "data" / "Master" / "1" / "Transcript" / "transcript-en.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps([
            {"start": 0, "end": 60, "text": "A source claim for the experiment."},
            {"start": 60, "end": 120, "text": "Another source claim for the experiment."},
        ]), encoding="utf-8")
        args = types.SimpleNamespace(
            data_root=root / "data", output_root=root / "outputs",
            model_path=root / "checkpoint", force=False, dry_run=False,
            format="filled", seed=42,
        )
        dest = args.output_root / "Master" / "1" / runner.folder_name("v3")
        return args, transcript, dest

    def run_job(self, args, client):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run_one("Master", 1, "v3", args, self.settings, client)

    def test_prompt_preserves_positive_counts_settings_and_standalone_rules(self):
        positive = Settings()
        for attribute in ("max_new_tokens", "max_input_chars", "words_per_qa", "min_qa_pairs", "generation_config"):
            self.assertEqual(getattr(self.settings, attribute), getattr(positive, attribute))
        for version in ("v1", "v2", "v3"):
            for words in (10, 1251, 2250):
                with self.subTest(version=version, words=words):
                    self.settings.prompt_version = positive.prompt_version = version
                    positive.prompt_format = self.settings.prompt_format
                    transcript = "[0:00] " + "word " * words + "[9:59]"
                    system, prompt, count = self.settings.get_prompt_template(transcript)
                    self.assertEqual(count, positive.get_prompt_template(transcript)[2])
                    self.assertIn(f"exactly {count}", prompt)
                    self.assertIn("deliberately poor-quality", system)
                    if _STANDALONE[version]:
                        self.assertIn(_STANDALONE[version], prompt)
                    else:
                        self.assertNotIn("Self-contained (Standalone)", prompt)

    def test_prompt_rotates_defects_and_preserves_structural_format(self):
        for format_name, format_line in (("labels", _FORMAT_LABELS), ("filled", _FORMAT_FILLED)):
            with self.subTest(format=format_name):
                self.settings.prompt_format = format_name
                system, prompt, _ = self.settings.get_prompt_template("[0:00] source text")
                for sequence, criterion in (("1, 5, 9", "Trustworthiness"), ("2, 6, 10", "Clarity"),
                                             ("3, 7, 11", "Usefulness"), ("4, 8, 12", "Care Safety")):
                    self.assertIn(f"pairs {sequence}, ... fail {criterion}", prompt)
                self.assertIn(format_line, prompt)
                self.assertIn("Do not label a pair bad", prompt)
                self.assertIn("real source section", prompt)
                self.assertIn("structural labels Question, Timestamp, and Answer", system)

    def test_run_one_writes_existing_schema_and_separate_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, transcript, dest = self.make_job(Path(temporary))
            client = FakeClient(response_with_pairs())
            result = self.run_job(args, client)
            pairs = json.loads((dest / "QA results" / "finalQA.json").read_text())
            raw = json.loads((dest / "intermediate" / "Intermediate.json").read_text())
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["pairs"], result["expected"])
            self.assertEqual(client.loads, 1)
            self.assertEqual(len(pairs), 4)
            for pair in pairs:
                self.assertEqual(set(pair), {"question", "answer", "timestamp", "time_start_sec", "time_end_sec"})
                self.assertTrue(pair["question"] and pair["answer"])
                self.assertEqual((pair["time_start_sec"], pair["time_end_sec"]), (0, 20))
            self.assertEqual(raw["raw_response"], client.response)
            self.assertEqual(raw["messages"], client.calls[0])
            self.assertEqual(raw["transcript_sha256"], hashlib.sha256(transcript.read_bytes()).hexdigest())
            self.assertEqual(raw["intended_primary_failures"], list(runner.DEFECTS))
            self.assertEqual(raw["intended_quality"], "bad")
            self.assertFalse(raw["quality_verified"])
            # Existing output is resumable and does not reload or regenerate.
            self.assertEqual(self.run_job(args, client)["status"], "skipped")
            self.assertEqual(client.loads, 1)
            self.assertEqual(len(client.calls), 1)

    def test_invalid_generation_keeps_raw_but_never_writes_final_pairs(self):
        cases = (
            ("extra pair", response_with_pairs(5), False, "expected exactly 4"),
            ("blank question", response_with_pairs(blank_question=2), False, "blank question or answer"),
            ("blank answer", response_with_pairs(blank_answer=2), False, "blank question or answer"),
            ("exhausted budget", response_with_pairs(), True, "Output token budget reached"),
        )
        for label, response, exhausted, error in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                args, _, dest = self.make_job(Path(temporary))
                with self.assertRaisesRegex(ValueError, error):
                    self.run_job(args, FakeClient(response, exhausted))
                self.assertFalse((dest / "QA results" / "finalQA.json").exists())
                raw = json.loads((dest / "intermediate" / "Intermediate.json").read_text())
                self.assertEqual(raw["raw_response"], response)

    def test_force_failure_preserves_previous_final_and_matching_intermediate(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, _, dest = self.make_job(Path(temporary))
            self.run_job(args, FakeClient(response_with_pairs()))
            final_path = dest / "QA results" / "finalQA.json"
            raw_path = dest / "intermediate" / "Intermediate.json"
            original_final, original_raw = final_path.read_bytes(), raw_path.read_bytes()
            args.force = True
            failed_response = response_with_pairs(5)
            with self.assertRaises(ValueError):
                self.run_job(args, FakeClient(failed_response))
            self.assertEqual(final_path.read_bytes(), original_final)
            self.assertEqual(raw_path.read_bytes(), original_raw)
            failed_raw = json.loads((dest / "intermediate" / "Intermediate.failed.json").read_text())
            self.assertEqual(failed_raw["raw_response"], failed_response)

    def test_changed_generation_controls_cannot_silently_resume_previous_output(self):
        for changed in ("seed", "format", "transcript"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                args, transcript, dest = self.make_job(Path(temporary))
                client = FakeClient(response_with_pairs())
                self.run_job(args, client)
                final_path = dest / "QA results" / "finalQA.json"
                raw_path = dest / "intermediate" / "Intermediate.json"
                original_final, original_raw = final_path.read_bytes(), raw_path.read_bytes()
                if changed == "seed":
                    args.seed = 43
                elif changed == "format":
                    args.format = "labels"
                else:
                    transcript.write_text(transcript.read_text() + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.run_job(args, client)
                self.assertEqual(final_path.read_bytes(), original_final)
                self.assertEqual(raw_path.read_bytes(), original_raw)
                self.assertEqual(len(client.calls), 1)

    def test_subset_rerun_preserves_other_video_metadata_and_success_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, transcript, _ = self.make_job(Path(temporary))
            second = args.data_root / "Master" / "2" / "Transcript" / transcript.name
            second.parent.mkdir(parents=True)
            second.write_bytes(transcript.read_bytes())
            args.model_path.mkdir()
            (args.model_path / "config.json").write_text(json.dumps({
                "model_type": "qwen3", "hidden_size": 5120, "num_hidden_layers": 40,
            }), encoding="utf-8")
            arguments = [
                "--model-path", str(args.model_path), "--data-root", str(args.data_root),
                "--output-root", str(args.output_root), "--bases", "Master", "--versions", "v3",
            ]
            with patch.object(runner, "LocalQwen", return_value=FakeClient(response_with_pairs())), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(arguments + ["--ids", "1", "2", "--seed", "42"]), 0)
            meta_path = args.output_root / "Master" / f"{runner.folder_name('v3')}_generation_meta.json"
            original = json.loads(meta_path.read_text())
            self.assertEqual([v["id"] for v in original["videos"]], [1, 2])
            self.assertTrue(all(v["generation"]["seed"] == 42 for v in original["videos"]))
            with patch.object(runner, "LocalQwen", return_value=FakeClient(response_with_pairs(5))), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(arguments + ["--ids", "1", "--seed", "43", "--force"]), 1)
            updated = json.loads(meta_path.read_text())
            first, second = updated["videos"]
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["generation"], original["videos"][0]["generation"])
            self.assertEqual(first["last_failed_attempt"]["status"], "failed")
            self.assertEqual(second, original["videos"][1])
            self.assertEqual(updated["latest_invocation"]["seed"], 43)
            self.assertEqual([v["id"] for v in updated["latest_invocation"]["videos"]], [1])

    def test_transcript_accepts_valid_end_when_duration_is_null(self):
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "transcript-cn.json"
            transcript.write_text(json.dumps([
                {"start": 5, "end": 10, "duration": None, "text": "Source text."},
            ]), encoding="utf-8")
            text, language, duration = runner.read_transcript(transcript, self.settings)
            self.assertEqual(text, "[0:05] Source text.")
            self.assertEqual(language, ("zh", "Chinese"))
            self.assertEqual(duration, 10)

    def test_dry_run_needs_no_checkpoint_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, _, _ = self.make_job(Path(temporary))
            with patch.object(runner.LocalQwen, "load") as load, \
                    patch.object(runner.LocalQwen, "generate") as generate, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                status = runner.main([
                    "--model-path", str(args.model_path), "--data-root", str(args.data_root),
                    "--output-root", str(args.output_root), "--bases", "Master", "--ids", "1",
                    "--versions", "v3", "--dry-run",
                ])
            self.assertEqual(status, 0)
            self.assertIn('"status": "dry-run"', output.getvalue())
            load.assert_not_called()
            generate.assert_not_called()
            self.assertFalse(args.output_root.exists())
            self.assertFalse(args.model_path.exists())

    def test_context_overflow_fails_before_device_transfer_or_generation(self):
        inputs = Mock()
        inputs.input_ids.shape = (1, runner.CONTEXT_LIMIT - self.settings.max_new_tokens + 1)
        tokenizer = Mock(return_value=inputs)
        tokenizer.apply_chat_template.return_value = "rendered prompt"
        client = runner.LocalQwen("unused-checkpoint", seed=42)
        client.tokenizer, client.model = tokenizer, Mock()
        torch_stub = types.ModuleType("torch")
        transformers_stub = types.ModuleType("transformers")
        transformers_stub.set_seed = Mock()
        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
            with self.assertRaisesRegex(ValueError, "exceeds Qwen3's native"):
                client.generate([{"role": "user", "content": "source"}], self.settings)
        self.assertFalse(tokenizer.apply_chat_template.call_args.kwargs["enable_thinking"])
        self.assertFalse(tokenizer.call_args.kwargs["truncation"])
        inputs.to.assert_not_called()
        client.model.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
