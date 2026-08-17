import os
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
memory_root = Path(__file__).parent.parent

# GPU Memory Function path
gpu_memory_path = memory_root / "common_utils"

# Model paths.
# Defaults are the absolute paths on the WAVE HPC; set QWEN_MODEL_PATH /
# BGE_MODEL_PATH to run anywhere the weights live somewhere else. Only used by
# QWEN_BACKEND=local -- the API backend ignores qwen_model_path entirely.
qwen_model_path = os.getenv("QWEN_MODEL_PATH", "/WAVE/datasets/oignat_lab/QWEN3.5_9B")
bge_model_path = os.getenv("BGE_MODEL_PATH") or (project_root / "BGE-M3")

# project_root = os.path.dirname(os.path.abspath(__file__))
# model_path = os.path.join(project_root, "Qwen model is here")
