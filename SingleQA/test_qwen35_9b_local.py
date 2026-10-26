"""Offline regression checks for the Qwen3.5 batch runner (no GPU required)."""

import contextlib
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from SingleQA import run_qwen35_9b_local as runner


RESPONSE = "\n\n".join(
    f"Question {i}: What helps a caregiver with task {i}?\n"
    f"Timestamp {i}: 0:00-0:10\nAnswer {i}: Explain the task calmly."
    for i in range(1, 5)
)
STATS = {"input_tokens": 200, "output_tokens": 120, "agent_seconds": 1,
         "output_budget_reached": False}


class BatchTests(unittest.TestCase):
    def make_fixture(self, root):
        path = root / "Master" / "1" / "Transcript" / "transcript-en.json"
        runner.write_json(path, [{"start": 0, "end": 10, "text": "Explain the task calmly."}])
        checkpoint = root / "checkpoint"
        runner.write_json(checkpoint / "config.json", {
            "model_type": "qwen3_5", "text_config": {"hidden_size": 4096, "num_hidden_layers": 32},
        })
        args = runner.get_args(["--data-root", str(root), "--output-root", str(root / "output"),
                                "--model-path", str(checkpoint), "--bases", "Master"])
        settings = runner.Settings()
        client = Mock(model=None)
        client.load.side_effect = lambda: setattr(client, "model", object())
        client.generate.return_value = (RESPONSE, STATS)
        return args, settings, client

    def test_success_resume_and_changed_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args, settings, client = self.make_fixture(root)
            result = runner.run_one("Master", "1", "v3", args, settings, client)
            self.assertEqual((result["status"], result["pairs"]), ("ok", 4))
            resumed = runner.run_one("Master", "1", "v3", args, settings, client)
            self.assertEqual(resumed["status"], "skipped")
            client.load.assert_called_once()
            client.generate.assert_called_once()
            transcript = root / "Master/1/Transcript/transcript-en.json"
            runner.write_json(transcript, [{"start": 0, "end": 10, "text": "Changed input."}])
            with self.assertRaisesRegex(ValueError, "provenance"):
                runner.run_one("Master", "1", "v3", args, settings, client)

    def test_failed_forced_retry_preserves_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, settings, client = self.make_fixture(Path(temporary))
            runner.run_one("Master", "1", "v3", args, settings, client)
            dest = args.output_root / "Master/1" / runner.folder_name("v3")
            final, raw = dest / "QA results/finalQA.json", dest / "intermediate/Intermediate.json"
            previous = final.read_bytes(), raw.read_bytes()
            args.force = True
            client.generate.return_value = ("incomplete", {**STATS, "output_budget_reached": True})
            with self.assertRaisesRegex(ValueError, "Output budget"):
                runner.run_one("Master", "1", "v3", args, settings, client)
            self.assertEqual((final.read_bytes(), raw.read_bytes()), previous)
            self.assertTrue(raw.with_name("Intermediate.failed.json").exists())

    def test_dry_run_discovers_noncontiguous_ids_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args, _, _ = self.make_fixture(root)
            runner.write_json(root / "Master/99/Transcript/transcript-en.json",
                              [{"start": 0, "duration": 10, "text": "Second transcript."}])
            with patch.object(runner.LocalQwen35, "load") as load, contextlib.redirect_stdout(io.StringIO()) as output:
                code = runner.main(["--data-root", str(root), "--bases", "Master", "--dry-run",
                                    "--model-path", str(root / "nonexistent"),
                                    "--output-root", str(args.output_root)])
            self.assertEqual(code, 0)
            self.assertIn("2 videos, 2 jobs", output.getvalue())
            self.assertFalse(args.output_root.exists())
            load.assert_not_called()

    def test_missing_transcript_fails_but_batch_continues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_fixture(root)
            (root / "Master/2").mkdir()
            runner.write_json(root / "Master/3/Transcript/transcript-en.json",
                              [{"start": 0, "end": 10, "text": "Last transcript."}])
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = runner.main(["--data-root", str(root), "--bases", "Master", "--dry-run"])
            self.assertEqual(code, 1)
            self.assertIn('[3/3]', output.getvalue())
            self.assertIn('"status": "failed"', output.getvalue())

    def test_model_load_failure_stops_after_one_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args, _, _ = self.make_fixture(root)
            runner.write_json(root / "Master/2/Transcript/transcript-en.json",
                              [{"start": 0, "end": 10, "text": "Next transcript."}])
            with patch.object(runner.LocalQwen35, "load", side_effect=runner.ModelLoadError("no GPU")) as load, \
                    contextlib.redirect_stdout(io.StringIO()):
                code = runner.main(["--data-root", str(root), "--bases", "Master",
                                    "--model-path", str(args.model_path), "--output-root", str(args.output_root)])
            self.assertEqual(code, 1)
            load.assert_called_once()

    def test_context_overflow_never_truncates_or_generates(self):
        client = runner.LocalQwen35("unused", 42)
        inputs = Mock()
        inputs.input_ids.shape = (1, 90)
        client.tokenizer = Mock(return_value=inputs)
        client.model = Mock()
        client.model.config.text_config.max_position_embeddings = 100
        settings = runner.Settings()
        settings.max_new_tokens = 20
        torch_stub, transformers_stub = types.ModuleType("torch"), types.ModuleType("transformers")
        transformers_stub.set_seed = Mock()
        with patch.dict("sys.modules", {"torch": torch_stub, "transformers": transformers_stub}):
            with self.assertRaisesRegex(ValueError, "was not truncated"):
                client.generate([{"role": "user", "content": "source"}], settings)
        self.assertFalse(client.tokenizer.call_args.kwargs["truncation"])
        self.assertFalse(client.tokenizer.apply_chat_template.call_args.kwargs["enable_thinking"])
        inputs.to.assert_not_called()
        client.model.generate.assert_not_called()

    def test_invalid_pair_count_and_timestamps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly 4"):
            runner.validate_pairs([], 4, 10)
        for start, end in ((0, 11), (5, 5), (-1, 5), (0, float("nan"))):
            with self.subTest(start=start, end=end), self.assertRaisesRegex(ValueError, "timestamp"):
                runner.validate_pairs([{"question": "Why?", "answer": "Help.",
                                        "time_start_sec": start, "time_end_sec": end}], 1, 10)


if __name__ == "__main__":
    unittest.main()
