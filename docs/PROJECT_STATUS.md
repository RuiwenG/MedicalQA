# Project Status & Handoff — MedicalQA

_Last updated: 2026-08-01_

This doc tracks the migration to **Qwen 3.5 9B** and the current state of the
QA-extraction pipelines. It's meant as a handoff so teammates know what's done
and what's left.

## Environment (HPC)

- **Location:** `oignat_lab/Ruiwen/MedicalQA`
- **Virtual env:** `.mvenv` (activate before running anything)
- The actual working/execution environment is the **HPC**, not a local laptop.
- Test videos: `test_dataset.csv` (10 videos).

## Model migration: Qwen 2.5 7B → Qwen 3.5 9B

Qwen 3.5 9B has the reasoning ("thinking") feature. We do **not** want thinking
enabled in these pipelines, so `enable_thinking=False` was added to every
`tokenizer.apply_chat_template(...)` call.

### Done ✅

- **Configuration changes for Qwen 3.5 9B across 4 pipelines** (reasoning disabled):
  - `SingleQA` — `SingleQA/models/model_handler.py`
  - `MultiAgentChunking` — the 5 agents (`agent1_architect`, `agent2_inquisitor`,
    `agent3_scorer_single`, `agent4_justifier`, `agent5_synthesizer`)
  - `LLMChunking` — `LLMChunking/agents/base_agent.py` (covers both its agents)
  - `RAG` — `agent1_question_generation.py`, `agent2_answer_synthesis.py`
- **Video 1 processed by both Single-agent and Multi-agent — works.** Runs
  end-to-end on Qwen 3.5 9B.
  - **Current prompt: only "educational value"** — no "mentorship value". Agents extract high educational QAs generically.
  - ⚠️ This is still a quick prompt and **can be refined** (see ToDos).

> Note: `enable_thinking=False` requires the loaded tokenizer's chat template to
> support that kwarg (Qwen 3.5 does). If an old Qwen 2.5 tokenizer is ever loaded,
> it will raise a `TypeError`.

## Inference moved to an API backend (2026-08-01)

All four pipelines now call Qwen over HTTP through `common_utils/llm_client.py`
instead of loading weights in-process. The nine former
`apply_chat_template` → `model.generate` → `decode` blocks are gone; each agent
makes a single `chat(...)` call.

- **Config lives in `.env`** (gitignored, template in `.env.example`).
  Any OpenAI-compatible endpoint works: Alibaba Model Studio, a self-hosted
  vLLM pod, DeepInfra, Together, OpenRouter.
- **The local path still exists.** `QWEN_BACKEND=local` restores the original
  transformers behaviour, so `slurm/run_script.sh` keeps working — add
  `export QWEN_BACKEND=local` to the job script to pin it.
- **Reasoning stays off.** `QWEN_THINKING_PARAM` sends the provider's
  disable-thinking flag, and the client strips any `<think>` block that comes
  back regardless — necessary because the Architect's JSON parser scans for the
  first `[`.
- **No `main.py` changed.** The handler/agent classes kept their method names,
  so the pipeline logic and the `run.py` contract are untouched.
- **RAG still needs local BGE-M3.** Only Qwen moved; embeddings are unchanged
  (now with a CPU fallback instead of a hardcoded `cuda`).

Measured cost for the 10-video `test_dataset.csv` across all four approaches is
roughly $0.15–0.20 on a small-model tier — the transcripts are only ~3k tokens
each. The multi-agent pipeline makes ~186 calls per video, so rate limits and
round-trip latency matter more than price.

## ToDos (what's left)

- [ ] **Refine the prompt** for single- and multi-agent pipelines (if necessary).
      Current prompt only targets generic "educational value" (no dementia focus);
      revisit whether the framing needs more tuning.
- [ ] **Run the single-agent pipeline on the remaining videos (2–10)** in
      `test_dataset.csv`. (Video 1 done.)
- [ ] **Run the multi-agent pipeline on the remaining videos (2–10)** in
      `test_dataset.csv`. (Video 1 done.)
- [ ] **Human eval discussion — Monday 2026-08-03.** Then figure out the script
      for the **human agreement score**.
