import sys
from typing import Dict, List, Optional
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from common_utils.llm_client import get_client
from common_utils.paths import qwen_model_path


class QwenModelHandler:
    """Shared Qwen access for both RAG agents."""

    def __init__(self, llm_model_path=qwen_model_path):
        self.llm_model_path = llm_model_path
        self.client = None
        self.loaded = False

    def load_model(self):
        """Connect to the configured Qwen backend once and reuse it."""
        if self.loaded:
            print("✅ Reusing existing Qwen client")
            return

        self.client = get_client(model_path=self.llm_model_path)
        self.loaded = True
        print("✅ Qwen client ready")

    def chat(
        self,
        messages: List[Dict],
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        repetition_penalty: Optional[float] = None,
    ) -> str:
        """Send a chat request on behalf of an agent."""
        if not self.loaded:
            raise RuntimeError("Client not initialised. Call load_model() first.")
        return self.client.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
        )

    def unload_model(self):
        """Release the backend (a no-op for the API client)."""
        if self.client is not None:
            self.client.close()
            self.client = None
        self.loaded = False
