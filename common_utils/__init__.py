# added so that python knows this is a package
#
# Load .env here, before any submodule reads the environment. paths.py resolves
# QWEN_MODEL_PATH / BGE_MODEL_PATH at import time, so the values have to be in
# os.environ by then -- doing it in llm_client would be too late for callers
# that import paths first.
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv is optional; real env vars still work
    pass
