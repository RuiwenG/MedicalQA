"""Shared Qwen chat client.

Every pipeline talks to Qwen through this module instead of loading weights
itself, so there is exactly one place that knows about endpoints, retries and
generation parameters.

Two backends, selected with the ``QWEN_BACKEND`` environment variable:

``api`` (default)
    Any OpenAI-compatible Qwen endpoint -- Alibaba Model Studio (DashScope),
    a self-hosted vLLM pod, DeepInfra, Together, OpenRouter.
``local``
    The original ``transformers`` path, kept so the HPC/SLURM workflow in
    ``slurm/run_script.sh`` still works unchanged.

Configuration comes from ``.env`` (see ``.env.example``). Nothing here reads a
key for any provider other than Qwen.
"""

import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(str(Path(__file__).parent.parent))
# Importing the package runs common_utils/__init__.py, which loads .env from the
# repository root before paths.py resolves any environment variables.
import common_utils  # noqa: F401
from common_utils.paths import qwen_model_path

# Endpoints that speak the OpenAI chat-completions protocol.
KNOWN_ENDPOINTS = {
    "dashscope": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "dashscope-cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "together": "https://api.together.xyz/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

DEFAULT_MODEL = "qwen3.5-plus"

# Sampling seed. These pipelines feed a comparison study, so identical input
# must give identical output; without this, the same transcript yielded between
# 9 and 19 QA pairs across runs. Set QWEN_SEED="" to opt out and sample freely.
_seed_env = os.getenv("QWEN_SEED", "42")
SEED = int(_seed_env) if _seed_env.strip() else None

# Some servers ignore the "no thinking" flag and inline the reasoning instead.
# Strip it: the JSON/regex parsers downstream scan for the first '[' or a
# "Question 1:" prefix and a stray reasoning block corrupts them.
_THINK_BLOCK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_ORPHAN_OPEN = re.compile(r"^.*?</(think|thinking|reasoning)>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove inline reasoning blocks from a model response."""
    if not text:
        return text
    cleaned = _THINK_BLOCK.sub("", text)
    if "</think" in cleaned.lower() or "</reasoning" in cleaned.lower():
        cleaned = _ORPHAN_OPEN.sub("", cleaned)
    return cleaned.strip()


class QwenAPIClient:
    """Qwen over an OpenAI-compatible HTTP endpoint."""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 180.0,
        max_retries: int = 5,
    ):
        try:
            from openai import OpenAI
        except ImportError as error:
            raise ImportError(
                "The 'openai' package is required for the API backend.\n"
                "    pip install openai\n"
                "Or set QWEN_BACKEND=local to load the model weights instead."
            ) from error

        self.model = model or os.getenv("QWEN_MODEL", DEFAULT_MODEL)
        self.base_url = base_url or self._resolve_base_url()
        key = api_key or os.getenv("QWEN_API_KEY") or os.getenv("VLLM_POD_API_KEY")
        if not key:
            raise RuntimeError(
                "No Qwen API key found. Set QWEN_API_KEY in .env (see .env.example)."
            )

        # How to ask the server to skip reasoning. Qwen3.5 has thinking on by
        # default; leaving it on would multiply the output-token bill and slow
        # every call, and these pipelines never use the reasoning trace.
        self.thinking_param = os.getenv("QWEN_THINKING_PARAM", "dashscope").lower()
        self.max_retries = max_retries
        self._client = OpenAI(
            api_key=key, base_url=self.base_url, timeout=timeout, max_retries=max_retries
        )

    @staticmethod
    def _resolve_base_url() -> str:
        """Pick an endpoint from .env, preferring an explicit URL."""
        explicit = os.getenv("QWEN_BASE_URL")
        if explicit:
            return explicit.rstrip("/")

        provider = os.getenv("QWEN_PROVIDER", "").lower()
        if provider in KNOWN_ENDPOINTS:
            return KNOWN_ENDPOINTS[provider]

        # A self-hosted vLLM pod serving Qwen is also OpenAI-compatible.
        pod = os.getenv("VLLM_POD_ENDPOINT")
        if pod:
            pod = pod.rstrip("/")
            return pod if pod.endswith("/v1") else f"{pod}/v1"

        return KNOWN_ENDPOINTS["dashscope"]

    def _extra_body(self, repetition_penalty: Optional[float]) -> Dict:
        extra: Dict = {}
        if self.thinking_param == "dashscope":
            extra["enable_thinking"] = False
        elif self.thinking_param == "vllm":
            extra["chat_template_kwargs"] = {"enable_thinking": False}
        elif self.thinking_param == "openrouter":
            extra["reasoning"] = {"enabled": False}
        if repetition_penalty is not None:
            extra["repetition_penalty"] = repetition_penalty
        return extra

    def chat(
        self,
        messages: List[Dict],
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        repetition_penalty: Optional[float] = None,
    ) -> str:
        """Send a chat request and return the assistant text.

        Mirrors the old ``tokenize -> model.generate -> decode`` block, so the
        callers keep their existing generation parameters.
        """
        request = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if SEED is not None:
            # Best-effort: most OpenAI-compatible servers honour this, but none
            # guarantee bit-identical output the way the local backend does.
            request["seed"] = SEED
        if temperature is not None:
            request["temperature"] = temperature
        if top_p is not None:
            request["top_p"] = top_p

        extra_body = self._extra_body(repetition_penalty)
        if extra_body:
            request["extra_body"] = extra_body

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.chat.completions.create(**request)
                text = response.choices[0].message.content or ""
                return strip_reasoning(text)
            except Exception as error:  # network blips, 429s the SDK gave up on
                last_error = error
                if attempt == self.max_retries - 1:
                    break
                backoff = min(2**attempt, 30)
                print(f"⚠️ Qwen API call failed ({error}); retrying in {backoff}s...")
                time.sleep(backoff)

        raise RuntimeError(
            f"Qwen API call failed after {self.max_retries} attempts: {last_error}"
        )

    def close(self):
        """No resources to release; present so callers can treat both backends alike."""
        return None


class QwenLocalClient:
    """Original transformers path, exposing the same ``chat`` interface."""

    def __init__(self, model_path=qwen_model_path, **_ignored):
        self.model_path = model_path
        self.model = None
        self.tokenizer = None

    def _ensure_loaded(self):
        if self.model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        print(f"⏳ Loading Qwen weights from {self.model_path}...")
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, trust_remote_code=True
        )
        # transformers 5.x renamed torch_dtype -> dtype; keep the old spelling
        # as a fallback so this still runs on a 4.x install.
        kwargs = dict(device_map="auto", trust_remote_code=True)
        try:
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_path, dtype=torch.bfloat16, **kwargs
            ).eval()
        except TypeError:
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_path, torch_dtype=torch.bfloat16, **kwargs
            ).eval()
        self.model.generation_config.max_length = 131072
        self.model.generation_config.max_new_tokens = 8192

    def chat(
        self,
        messages: List[Dict],
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        repetition_penalty: Optional[float] = None,
    ) -> str:
        import torch

        self._ensure_loaded()
        if SEED is not None:
            # Re-seed before every call so a given (prompt, seed) pair always
            # produces the same text, independent of how many calls preceded it.
            from transformers import set_seed

            set_seed(SEED)
        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        inputs = self.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=131072
        ).to(self.model.device)

        params = {
            "max_new_tokens": max_tokens,
            "pad_token_id": self.tokenizer.eos_token_id,
        }
        if temperature is not None:
            params.update({"temperature": temperature, "do_sample": True})
        if top_p is not None:
            params["top_p"] = top_p
        if repetition_penalty is not None:
            params["repetition_penalty"] = repetition_penalty

        with torch.no_grad():
            outputs = self.model.generate(**inputs, **params)

        response = self.tokenizer.decode(
            outputs[0][inputs.input_ids.shape[1] :], skip_special_tokens=True
        )
        return strip_reasoning(response)

    def close(self):
        import torch

        if self.model is not None:
            del self.model
            self.model = None
        if self.tokenizer is not None:
            del self.tokenizer
            self.tokenizer = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def get_client(model_path=qwen_model_path, **kwargs):
    """Build the client for the configured backend.

    ``QWEN_BACKEND=local`` restores the on-GPU path used by the SLURM jobs;
    anything else (the default) uses the HTTP API.
    """
    backend = os.getenv("QWEN_BACKEND", "api").strip().lower()
    if backend == "local":
        print("🖥️  Qwen backend: local weights")
        return QwenLocalClient(model_path=model_path)

    client = QwenAPIClient(**kwargs)
    print(f"🌐 Qwen backend: API — model '{client.model}' at {client.base_url}")
    return client
