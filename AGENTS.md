# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, and others that read `AGENTS.md`) when working with code in this repository.

## Overview

MedicalQA turns long-form video transcripts (dementia-care education) into
question–answer pairs and compares **four generation approaches**. `run.py`
orchestrates preprocessing and runs one or more approaches per video.

| ID | Approach | Output folder | Entry point |
| -- | -------- | ------------- | ----------- |
| 1 | SingleQA | `SingleAgent` | `SingleQA/main.py` |
| 2 | LLMChunking (dual-agent) | `DualAgent` | `LLMChunking/main.py` |
| 3 | MultiAgentChunking (5 agents) | `MultiAgent-LLMChunking` | `MultiAgentChunking/main.py` |
| 4 | RAG | `RAG` | `RAG/main.py` |

Forked from **MentorQA**; the framing shifted from "mentorship value" to
"educational value" (dementia care).

## Commands

```bash
# Setup (uv recommended; venv/pip on HPC also documented in README)
uv venv --python 3.10 && source .venv/bin/activate
uv pip install -r requirements.txt

# Fetch transcripts from YouTube captions (no Whisper) into <base>/<id>/Transcript/
python download_transcripts.py --csv test_dataset.csv --base Master

# Run all four approaches over a video CSV (uses existing transcripts)
python run.py --v test_dataset.csv

# One approach for all videos (3 = MultiAgent); one video only; RAG on a single video
python run.py --v test_dataset.csv --app 3
python run.py --v test_dataset.csv --only 2 --app 4
python run.py --v test_dataset.csv --dry-run       # print commands, don't execute

# Opt into audio download + Whisper transcription instead of existing transcripts
python run.py --v test_dataset.csv --preprocess

# Regenerate the human-eval website's data after results change
python eval/web/extract_qa_data.py
```

There is **no build step and no automated test suite** — validation is running a
pipeline end-to-end on a video and inspecting `finalQA.json`, plus human eval
(`docs/HUMAN_EVAL.md`). The human eval runs from the notebook
`eval/QA_Educational_Eval_UI.ipynb` (run all cells) or the static site under
`eval/web/`.

## Architecture

### The orchestrator ↔ pipeline contract (`run.py`)

`run.py` is a thin coordinator. For each video it:
1. Builds `<base>/<id>/` with `Audio/`, `Transcript/`, and one folder per approach.
2. Spawns each approach as a **subprocess**: `python <approach>/main.py --id <id>`.
3. Parses the child's stdout for the **last** `{"agent_seconds": N}` line and
   writes it back into the CSV's `approach{n}` timing column (atomic rewrite).
4. Moves the child's standardized output files from the CWD into the video
   folder: `finalQA.json` → `QA results/`, and `Intermediate.json` /
   `chunks.json` / `debug_chunk.json` → `intermediate/`.

So every pipeline follows the same convention: it accepts `--id`, reads the
transcript from disk, writes `finalQA.json` (and optional intermediates) to the
**current working directory**, and prints `{"agent_seconds": ...}` to stdout.
When adding or modifying a pipeline, preserve that contract or `run.py` will
report a `0.0` time and fail to collect the output.

### Each pipeline is self-contained

The four approach directories each have their own `main.py`, `config/`,
`processors/`, `agents/`, and (for approaches 1/3) `models/model_handler.py`.
They import shared helpers from `common_utils/` by appending the repo root to
`sys.path` at the top of `main.py`. There is minimal cross-pipeline sharing —
treat each approach as an independent module that happens to share the model
loader pattern and output convention.

### Model loading (this branch)

Each pipeline loads Qwen **directly via `transformers`**
(`AutoModelForCausalLM.from_pretrained(..., device_map="auto",
torch_dtype=bfloat16)`) — one full model load per subprocess. The model is
**Qwen 3.5 9B** with reasoning disabled: `enable_thinking=False` is passed on
every `tokenizer.apply_chat_template(...)`. The model path is **hardcoded** in
`common_utils/paths.py` to the WAVE HPC location
(`/WAVE/datasets/oignat_lab/QWEN3.5_9B`); BGE-M3 (used by RAG) is expected at
`<repo>/BGE-M3`. Change these to run elsewhere. Sampling for SingleQA is
`temperature=0.7, do_sample=True` (`SingleQA/config/settings.py`); there is no
fixed seed on this branch.

### Datasets & output layout

`Master/` (10 dementia-care videos) and `Teepa/` (15-video "All About Dementia"
playlist) each hold `<index>/Transcript/transcript*.json` inputs and
`<index>/<Approach>/QA results/finalQA.json` outputs. The human-eval tooling
auto-discovers every `finalQA.json` and uses only its `question`/`answer` fields
so all approaches are judged identically.

## Gotchas specific to this branch

- **`run.py --base <dir>` does not reach the pipelines.** Every child `main.py`
  hardcodes `Master/{id}/Transcript` (e.g. `SingleQA/main.py:174`) and accepts
  only `--id`. So `--base Teepa` creates `Teepa/…` folders but the pipelines
  still read from `Master/`. Non-Master datasets do not run through `run.py`
  as-is on this branch, despite what the README's Teepa example implies.
- **`docs/TECHNIQUE_NOTES.md` is ahead of this branch.** It describes a
  `common_utils/llm_client.py` + `.env`/`QWEN_BACKEND` (api/local) backend
  refactor, `--base` threading, `QWEN_SEED=42`, and `temperature=0.3`. **None of
  that exists on `shrishti-test`** — verify against the actual code before
  relying on it. The docs describe the intended/other-branch state.
- **`run.py` swallows child failures.** It runs children with
  `capture_output=True` and no returncode check, so a crashed pipeline looks
  like success with `agent_seconds = 0.0` and its stderr is never printed.
- Existing `Master/` results predate the current sampling settings and are not
  reproducible from this code; do not assume `Master/` and `Teepa/` were
  generated under the same regime (see `docs/TECHNIQUE_NOTES.md` §4a).

## Conventions

- **Commits:** Conventional Commits (`type(scope): description`); sign off every
  commit (`git commit -s` / `--amend --signoff`).
- Secrets live in `.env` (gitignored); local model weights and generated
  artifacts (`RAG/temp_rag_chroma_db/`, `eval/results/`, audio, intermediates)
  are gitignored — do not commit them.

## Reference docs

- `README.md` — setup, model download, run examples.
- `docs/PROJECT_STATUS.md` — Qwen 3.5 9B migration status and ToDos.
- `docs/TECHNIQUE_NOTES.md` — methodology decisions and known issues (note the
  branch caveat above).
- `docs/HUMAN_EVAL.md` — the 8 eval metrics and how ratings are collected.
- `eval/web/README.md` — deploying the public evaluation site (Supabase).
