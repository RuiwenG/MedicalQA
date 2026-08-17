import sys
from typing import List, Dict, Optional
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from common_utils.llm_client import get_client
from common_utils.paths import qwen_model_path


class LLMAgent:
    """Shared Qwen access for both LLMChunking agents."""

    def __init__(self, model_path=qwen_model_path):
        self.model_path = model_path
        self.client = None

    def load_model(self):
        """Connect to the configured Qwen backend."""
        self.client = get_client(model_path=self.model_path)
        print("✅ Qwen client ready")

    def generate_response(
        self, messages: List[Dict], generation_params: Optional[Dict] = None
    ) -> str:
        """Generate a response from Qwen."""
        if self.client is None:
            raise RuntimeError("Client not initialised. Call load_model() first.")

        default_params = {
            "max_new_tokens": 2048,
            "temperature": 0.7,
            "top_p": 0.9,
            "repetition_penalty": 1.1,
        }

        if generation_params:
            default_params.update(generation_params)

        return self.client.chat(
            messages,
            max_tokens=default_params.get("max_new_tokens", 2048),
            temperature=default_params.get("temperature"),
            top_p=default_params.get("top_p"),
            repetition_penalty=default_params.get("repetition_penalty"),
        )

    def unload_model(self):
        """Release the backend (a no-op for the API client)."""
        if self.client is not None:
            self.client.close()
            self.client = None
