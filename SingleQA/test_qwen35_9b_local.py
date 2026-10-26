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
            failed = json.loads(raw.with_name("attempts").joinpath("seed-42.json").read_text())
            self.assertEqual(failed["raw_response"], "incomplete")

    def test_failed_validation_retries_with_next_seed_and_records_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, settings, client = self.make_fixture(Path(temporary))
            args.retries = 2
            client.generate.side_effect = [(RESPONSE.replace("0:00-0:10", "0:00-0:11"), STATS),
                                           (RESPONSE, STATS)]
            result = runner.run_one("Master", "1", "v3", args, settings, client)
            self.assertEqual((result["status"], result["sampling_seed"], result["attempts"]), ("ok", 43, 2))
            self.assertEqual([c.args[2] for c in client.generate.call_args_list], [42, 43])
            dest = args.output_root / "Master/1" / runner.folder_name("v3") / "intermediate"
            saved = json.loads((dest / "Intermediate.json").read_text())
            self.assertEqual((saved["seed"], saved["sampling_seed"]), (42, 43))
            self.assertTrue((dest / "attempts/seed-42.json").exists())
            self.assertEqual(runner.run_one("Master", "1", "v3", args, settings, client)["status"], "skipped")

    def test_saved_failed_response_is_reparsed_not_regenerated(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, settings, client = self.make_fixture(Path(temporary))
            client.generate.return_value = (RESPONSE.replace("0:00-0:10", "0:00-0:11"), STATS)
            with self.assertRaisesRegex(ValueError, "All 1 attempt"):
                runner.run_one("Master", "1", "v3", args, settings, client)
            dest = args.output_root / "Master/1" / runner.folder_name("v3") / "intermediate"
            # Responses saved before retries existed live only in Intermediate.json.
            (dest / "attempts/seed-42.json").unlink()
            record = json.loads((dest / "Intermediate.json").read_text())
            del record["sampling_seed"]
            runner.write_json(dest / "Intermediate.json", record)
            client.generate.reset_mock()
            client.generate.return_value = (RESPONSE, STATS)
            args.retries = 1
            result = runner.run_one("Master", "1", "v3", args, settings, client)
            self.assertEqual(result["sampling_seed"], 43)
            client.generate.assert_called_once()
            self.assertEqual(client.generate.call_args.args[2], 43)
            self.assertTrue((dest / "attempts/seed-42.json").exists())

    def test_all_failed_seeds_are_reported_and_no_final_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, settings, client = self.make_fixture(Path(temporary))
            args.retries = 1
            client.generate.return_value = ("Question 1: Why?\nAnswer 1: Help.", STATS)
            with self.assertRaisesRegex(ValueError, r"All 2 attempt\(s\) failed: seed 42: .* \| seed 43: "):
                runner.run_one("Master", "1", "v3", args, settings, client)
            dest = args.output_root / "Master/1" / runner.folder_name("v3")
            self.assertFalse((dest / "QA results/finalQA.json").exists())

    def test_reparse_only_tries_every_saved_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, settings, client = self.make_fixture(Path(temporary))
            args.retries = 1
            client.generate.side_effect = [(RESPONSE.replace("0:00-0:10", "0:00-0:11"), STATS),
                                           (RESPONSE, STATS)]
            runner.run_one("Master", "1", "v3", args, settings, client)
            dest = args.output_root / "Master/1" / runner.folder_name("v3")
            (dest / "QA results/finalQA.json").unlink()
            args.retries, args.reparse_only = 0, True
            result = runner.run_one("Master", "1", "v3", args, settings, client)
            self.assertEqual((result["sampling_seed"], result["recovered_from_saved_response"]), (43, True))
            self.assertEqual(client.generate.call_count, 2)

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
        client = runner.LocalQwen35("unused")
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
                client.generate([{"role": "user", "content": "source"}], settings, 42)
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

    def test_markdown_labels_parse_without_changing_content(self):
        response = RESPONSE
        for label in ("Question", "Timestamp", "Answer"):
            for number in range(1, 5):
                response = response.replace(f"{label} {number}:", f"**{label} {number}:**")
        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary) / "Intermediate.json"
            pairs = runner.parse_response(response, 4, 10, raw)
            self.assertEqual(pairs[0]["question"], "What helps a caregiver with task 1?")
            self.assertEqual(pairs[0]["answer"], "Explain the task calmly.")
            self.assertTrue(json.loads(raw.with_name("validation_report.json").read_text())["valid"])

    def test_normalizing_bare_labels_preserves_line_breaks(self):
        response = RESPONSE.replace("Question 1: What", "Question 1:\nWhat")
        with tempfile.TemporaryDirectory() as temporary:
            pairs = runner.parse_response(response, 4, 10, Path(temporary) / "Intermediate.json")
        self.assertEqual(pairs[0]["question"], "What helps a caregiver with task 1?")

    def test_hour_clocks_are_converted_without_changing_their_value(self):
        response = RESPONSE.replace("0:00-0:10", "01:02:03-01:02:13")
        with tempfile.TemporaryDirectory() as temporary:
            pairs = runner.parse_response(response, 4, 3800, Path(temporary) / "Intermediate.json")
        self.assertEqual(pairs[0]["time_start_sec"], 3723)
        self.assertEqual(pairs[0]["time_end_sec"], 3733)
        self.assertEqual(pairs[0]["timestamp"], "62:03-62:13")

    def test_zero_hour_clocks_do_not_lose_seconds(self):
        response = RESPONSE.replace("0:00-0:10", "00:00:02-00:00:10")
        with tempfile.TemporaryDirectory() as temporary:
            pairs = runner.parse_response(response, 4, 10, Path(temporary) / "Intermediate.json")
        self.assertEqual(pairs[0]["time_start_sec"], 2)
        self.assertEqual(pairs[0]["time_end_sec"], 10)

    def test_skipped_timestamp_number_attaches_by_question_order(self):
        response = RESPONSE.replace("Timestamp 3:", "Timestamp 4:").replace(
            "Timestamp 4: 0:00-0:10\nAnswer 4", "Timestamp 5: 0:00-0:10\nAnswer 4")
        with tempfile.TemporaryDirectory() as temporary:
            pairs = runner.parse_response(response, 4, 10, Path(temporary) / "Intermediate.json")
        self.assertEqual([p["time_end_sec"] for p in pairs], [10] * 4)

    def test_open_ended_range_runs_to_transcript_end(self):
        response = RESPONSE.replace("Timestamp 4: 0:00-0:10", "Timestamp 4: 0:05-End")
        with tempfile.TemporaryDirectory() as temporary:
            pairs = runner.parse_response(response, 4, 9.4, Path(temporary) / "Intermediate.json")
        self.assertEqual((pairs[3]["time_start_sec"], pairs[3]["time_end_sec"]), (5, 10))

    def test_missing_timestamps_still_fail(self):
        response = "\n".join(l for l in RESPONSE.splitlines() if not l.startswith("Timestamp"))
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, "Pair 1"):
            runner.parse_response(response, 4, 10, Path(temporary) / "Intermediate.json")

    def test_reparse_only_recovers_raw_response_without_checkpoint_or_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args, settings, client = self.make_fixture(root)
            runner.run_one("Master", "1", "v3", args, settings, client)
            dest = args.output_root / "Master/1" / runner.folder_name("v3")
            final = dest / "QA results/finalQA.json"
            final.unlink()
            (args.model_path / "config.json").unlink()
            with patch.object(runner.LocalQwen35, "load") as load, \
                    patch.object(runner.LocalQwen35, "generate") as generate, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = runner.main(["--data-root", str(root), "--output-root", str(args.output_root),
                                    "--model-path", str(args.model_path), "--bases", "Master", "--reparse-only"])
            self.assertEqual(code, 0)
            self.assertTrue(final.exists())
            self.assertIn('"recovered_from_saved_response": true', output.getvalue())
            load.assert_not_called()
            generate.assert_not_called()

    def test_invalid_raw_response_writes_diagnostics_and_no_final(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, settings, client = self.make_fixture(Path(temporary))
            client.generate.return_value = (RESPONSE.replace("0:00-0:10", "0:00-0:11"), STATS)
            with self.assertRaisesRegex(ValueError, r"start=0, end=11, transcript_end=10.000s"):
                runner.run_one("Master", "1", "v3", args, settings, client)
            dest = args.output_root / "Master/1" / runner.folder_name("v3")
            self.assertFalse((dest / "QA results/finalQA.json").exists())
            report = json.loads((dest / "intermediate/validation_report.json").read_text())
            self.assertFalse(report["valid"])
            self.assertEqual(report["parsed_pairs"], 4)
            self.assertEqual(len(report["timestamp_lines"]), 4)
            self.assertEqual(report["pairs"][0]["time_end_sec"], 11)

    def test_reparse_rejects_changed_input_and_preserves_completed_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, settings, client = self.make_fixture(Path(temporary))
            runner.run_one("Master", "1", "v3", args, settings, client)
            args.reparse_only = True
            result = runner.run_one("Master", "1", "v3", args, settings, client)
            self.assertEqual(result["status"], "skipped")
            dest = args.output_root / "Master/1" / runner.folder_name("v3")
            (dest / "QA results/finalQA.json").unlink()
            transcript = args.data_root / "Master/1/Transcript/transcript-en.json"
            runner.write_json(transcript, [{"start": 0, "end": 10, "text": "Changed input."}])
            with self.assertRaisesRegex(ValueError, "provenance"):
                runner.run_one("Master", "1", "v3", args, settings, client)
            client.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
