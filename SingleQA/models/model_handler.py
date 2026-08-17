import sys
from config.settings import Settings
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from common_utils.llm_client import get_client
from common_utils.paths import qwen_model_path


class QAModel:
    # Initialize
    def __init__(self, model_path=qwen_model_path):
        self.settings = Settings()
        self.model_path = model_path
        self.client = None
        # k requested in the last prompt (word_count / 250); the parser uses
        # it to validate how many pairs came back.
        self.expected_pairs = None

    # Connect to the configured Qwen backend
    def load_model(self):
        self.client = get_client(model_path=self.model_path)

    def generate_qa_pairs(self, transcript):
        """Generate QA pairs from transcript"""
        if self.client is None:
            raise RuntimeError("Client not initialised. Call load_model() first.")

        if isinstance(transcript, list):
            # Handle list of dicts like transcript.json. Keep each segment's
            # start time as an [m:ss] marker (same format the LLM judge uses)
            # so the model can cite which section every QA pair comes from.
            if transcript and isinstance(transcript[0], dict):
                lines = []
                for d in transcript:
                    text = (d.get("text") or d.get("transcript") or "").strip()
                    if not text:
                        continue
                    start = d.get("start")
                    if isinstance(start, (int, float)):
                        lines.append(f"[{int(start) // 60}:{int(start) % 60:02d}] {text}")
                    else:
                        lines.append(text)
                transcript = "\n".join(lines)
            else:
                # Join into single string context
                transcript = " ".join(
                    [str(t).strip() for t in transcript if str(t).strip()]
                )

        elif isinstance(transcript, dict):
            transcript = (
                transcript.get("text") or transcript.get("transcript") or ""
            ).strip()

        elif not isinstance(transcript, str):
            transcript = str(transcript).strip()

        # Guard against a transcript that would overflow the context window.
        # Tokenisation happens server-side now, so this is an approximate
        # character budget (~4 chars/token) rather than an exact truncation.
        if len(transcript) > self.settings.max_input_chars:
            print(
                f"⚠️ Transcript is {len(transcript)} chars; truncating to "
                f"{self.settings.max_input_chars} to stay inside the context window."
            )
            transcript = transcript[: self.settings.max_input_chars]
        print(f"ℹ️ Sending ~{len(transcript) // 4} transcript tokens")

        system_prompt, prompt, k = self.settings.get_prompt_template(transcript)
        self.expected_pairs = k
        print(f"ℹ️ Requesting k={k} QA pairs (~{self.settings.words_per_qa} words each)")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        return self.client.chat(
            messages,
            max_tokens=self.settings.max_new_tokens,
            temperature=self.settings.generation_config["temperature"],
            top_p=self.settings.generation_config["top_p"],
            repetition_penalty=self.settings.generation_config["repetition_penalty"],
        )

    def cleanup(self):
        # Release the backend (a no-op for the API client)
        if self.client is not None:
            self.client.close()
            self.client = None
