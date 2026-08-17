import sys
from typing import Dict, List, Optional
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from common_utils.llm_client import get_client
from common_utils.paths import qwen_model_path


class QAModel:
    """Shared Qwen access for all five MultiAgentChunking agents."""

    def __init__(self, model_path=qwen_model_path):
        self.model_path = model_path
        self.client = None

    def load_model(self):
        """Connect to the configured Qwen backend."""
        self.client = get_client(model_path=self.model_path)
        return self

    def chat(
        self,
        messages: List[Dict],
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        repetition_penalty: Optional[float] = None,
    ) -> str:
        """Send a chat request on behalf of an agent."""
        if self.client is None:
            raise RuntimeError("Client not initialised. Call load_model() first.")
        return self.client.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
        )

    def cleanup(self):
        # Release the backend (a no-op for the API client)
        if self.client is not None:
            self.client.close()
            self.client = None
