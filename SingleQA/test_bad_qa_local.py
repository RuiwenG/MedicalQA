"""Checks for the hosted negative-QA runner; no API key or network is needed."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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
            force=False, dry_run=False, format="filled", seed=42,
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

    def test_prompt_fails_all_defects_and_preserves_structural_format(self):
        for format_name, format_line in (("labels", _FORMAT_LABELS), ("filled", _FORMAT_FILLED)):
            with self.subTest(format=format_name):
                self.settings.prompt_format = format_name
                system, prompt, _ = self.settings.get_prompt_template("[0:00] source text")
                self.assertIn("fail all four quality criteria", prompt)
                # Failures go in both the question and the answer.
                self.assertIn("makes the question and answer difficult", prompt)
                self.assertIn("Ask about a trivial detail", prompt)
                self.assertNotIn("Put all four failures in the answer", prompt)
                self.assertNotIn("The question itself must be good", prompt)
                self.assertIn("do not invent medication doses, restraint instructions, or procedures that could physically harm someone", prompt)
                for criterion in ("Trustworthiness", "Clarity", "Usefulness", "Care Safety"):
                    self.assertIn(f"{criterion} failure", prompt)
                self.assertNotIn("Rotate", prompt)
                self.assertNotIn("source claim accurate", prompt)
                self.assertNotIn("avoid inventing source claims", prompt)
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
            self.assertEqual(raw["intended_primary_failures"], ["all"] * 4)
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
            arguments = [
                "--data-root", str(args.data_root),
                "--output-root", str(args.output_root), "--bases", "Master", "--versions", "v3",
            ]
            with patch.object(runner, "HostedQwen", return_value=FakeClient(response_with_pairs())), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(arguments + ["--ids", "1", "2", "--seed", "42"]), 0)
            meta_path = args.output_root / "Master" / f"{runner.folder_name('v3')}_generation_meta.json"
            original = json.loads(meta_path.read_text())
            self.assertEqual([v["id"] for v in original["videos"]], [1, 2])
            self.assertTrue(all(v["generation"]["seed"] == 42 for v in original["videos"]))
            with patch.object(runner, "HostedQwen", return_value=FakeClient(response_with_pairs(5))), \
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

    def test_dry_run_needs_no_api_key_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, _, _ = self.make_job(Path(temporary))
            with patch.object(runner.HostedQwen, "load") as load, \
                    patch.object(runner.HostedQwen, "generate") as generate, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                status = runner.main([
                    "--data-root", str(args.data_root),
                    "--output-root", str(args.output_root), "--bases", "Master", "--ids", "1",
                    "--versions", "v3", "--dry-run",
                ])
            self.assertEqual(status, 0)
            self.assertIn('"status": "dry-run"', output.getvalue())
            load.assert_not_called()
            generate.assert_not_called()
            self.assertFalse(args.output_root.exists())

    def hosted_call(self, finish_reason="stop", seed=42, env=None):
        """Run HostedQwen against a fake OpenAI SDK; return its output, request and SDK constructor."""
        completion = types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                finish_reason=finish_reason,
                message=types.SimpleNamespace(content="<think>plan</think>Question 1: Q?"))],
            usage=types.SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )
        environment = {"QWEN_API_KEY": "test-value", **(env or {})}
        with patch.dict(os.environ, environment), patch("openai.OpenAI") as sdk:
            sdk.return_value.chat.completions.create.return_value = completion
            client = runner.HostedQwen(seed)
            client.load()
            text, stats = client.generate([{"role": "user", "content": "source"}], self.settings)
        return text, stats, sdk.return_value.chat.completions.create.call_args.kwargs, sdk

    def test_request_goes_directly_to_alibaba_with_thinking_off(self):
        # A .env aimed at another model or endpoint must not redirect this runner.
        text, stats, request, sdk = self.hosted_call(env={
            "QWEN_MODEL": "qwen/qwen3.5-9b", "QWEN_PROVIDER": "openrouter",
            "QWEN_THINKING_PARAM": "openrouter", "OPEN_ROUTER_API_KEY": "wrong-provider",
        })
        self.assertEqual(sdk.call_args.kwargs["base_url"], "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(sdk.call_args.kwargs["api_key"], "test-value")
        self.assertEqual(request["model"], "qwen3.5-plus")
        self.assertEqual(request["extra_body"], {"enable_thinking": False, "repetition_penalty": 1.05})
        self.assertEqual((request["temperature"], request["top_p"], request["seed"]), (0.3, 0.9, 42))
        self.assertEqual(request["max_tokens"], self.settings.max_new_tokens)
        self.assertEqual(text, "Question 1: Q?")
        self.assertEqual((stats["input_tokens"], stats["output_tokens"]), (11, 7))
        self.assertFalse(stats["output_budget_reached"])

    def test_truncated_response_and_empty_seed_are_handled(self):
        _, stats, request, _ = self.hosted_call(finish_reason="length", seed=None)
        self.assertTrue(stats["output_budget_reached"])
        self.assertNotIn("seed", request)

    def test_missing_api_key_stops_every_remaining_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, _, _ = self.make_job(Path(temporary))
            cleared = {key: "" for key in ("QWEN_API_KEY", "VLLM_POD_API_KEY")}
            with patch.dict(os.environ, cleared), patch("openai.OpenAI"), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                status = runner.main([
                    "--data-root", str(args.data_root), "--output-root", str(args.output_root),
                    "--bases", "Master", "--ids", "1", "--versions", "v3",
                ])
            self.assertEqual(status, 1)
            self.assertIn("No Qwen API key", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
