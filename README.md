# MedicalQA

MedicalQA turns long-form video transcripts into question-and-answer pairs. It
includes four generation approaches:

- **SingleQA** — single-agent QA extraction
- **LLMChunking** — dual-agent topic segmentation and QA generation
- **MultiAgentChunking** — multi-agent chunking, selection, and answer synthesis
- **RAG** — retrieval-augmented question generation and answer synthesis

The main entry point, `run.py`, coordinates video preprocessing and one or more
of these approaches.

> **Contributors:** see [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for the
> current state of the Qwen 3.5 9B migration, what's done, and the remaining ToDos.

## Prerequisites

- **Python 3.10 (recommended).** The repository does not declare a Python
  version. Python 3.10 is the conservative choice for the PyTorch, Whisper,
  Transformers, and LangChain dependencies used here. Use Python 3.10 rather
  than the system Python where possible.
- **A Qwen API key** (see [Model setup](#model-setup)). By default the QA
  pipelines call Qwen over HTTP and need no GPU.
- **An NVIDIA GPU with CUDA** only if you run approach 4 (RAG), which still
  uses a local BGE-M3 embedding model, or if you set `QWEN_BACKEND=local` to
  load Qwen weights directly. Whisper preprocessing also wants a GPU.

## Set up the repository

Clone the repository and enter it:

```bash
git clone git@github.com:RuiwenG/MedicalQA.git
cd MedicalQA
```

### Option A: uv (recommended)

Install [uv](https://docs.astral.sh/uv/) if needed, then create a Python 3.10
environment and install the dependencies:

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install -r requirements.txt
```

### Option B: using venv and pip on HPC
If want to set up using HPC:
```bash
module load Anaconda3
python -m venv .mvenv
source .mvenv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```


PyTorch must match the CUDA version installed on the machine. If the default
PyPI PyTorch build is unsuitable, install the appropriate build using the
[PyTorch installation guide](https://pytorch.org/get-started/locally/) before
installing the remaining requirements.

## Model setup

### Qwen (the QA pipelines)

All four approaches reach Qwen through `common_utils/llm_client.py`. Copy the
example environment file and fill in your key:

```bash
cp .env.example .env
```

`.env` is gitignored — never commit it. The settings that matter:

| Variable | Purpose |
| --- | --- |
| `QWEN_BACKEND` | `api` (default) or `local` to load weights with transformers |
| `QWEN_API_KEY` | Your key |
| `QWEN_MODEL` | Model id, e.g. `qwen3.5-plus` or `qwen3.5-flash` |
| `QWEN_PROVIDER` | `dashscope`, `deepinfra`, `together`, `openrouter`, … |
| `QWEN_BASE_URL` | An explicit endpoint; overrides `QWEN_PROVIDER` |
| `QWEN_THINKING_PARAM` | How to disable reasoning: `dashscope`, `vllm`, `none` |

Any OpenAI-compatible endpoint works, including a self-hosted vLLM pod — set
`VLLM_POD_ENDPOINT` and `QWEN_THINKING_PARAM=vllm`.

> **Keep reasoning off.** Qwen3.5 thinks by default. These pipelines never read
> the reasoning trace, and leaving it on multiplies the output-token bill.
> `QWEN_THINKING_PARAM` handles this; the client also strips any `<think>`
> block a server returns anyway, which would otherwise break the JSON and
> "Question 1:" parsers.

### Local weights (only if you need them)

- [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) — only for `QWEN_BACKEND=local`
- [BGE-M3](https://huggingface.co/BAAI/bge-m3) — always needed for approach 4 (RAG)
- [Whisper large-v3](https://huggingface.co/openai/whisper-large-v3) — only for `--preprocess`

By default, `common_utils/paths.py` looks for the Qwen and BGE-M3 model
directories beside the repository:

```text
parent-directory/
├── MedicalQA/
├── Qwen3.5-9B/
└── BGE-M3/
```

Alternatively, edit the `qwen_model_path` and `bge_model_path` values in
`common_utils/paths.py` to point to your local snapshots.

Preprocessing uses the `WHISPER_MODEL_PATH` constant in `preprocess.py`. Update
that value to the location of your local `large-v3.pt` file before running
preprocessing.

## Run the pipeline

Create a CSV file containing `index`, `url`, and `language` columns:

```csv
index,url,language
1,https://youtube.com/watch?v=example1,English
2,https://youtube.com/watch?v=example2,Chinese
```
Right now using test_dataset.csv.

Preview the work without downloading videos or loading models:

```bash
python run.py --v test_dataset.csv --dry-run
```

### Download existing YouTube captions

If a video already has YouTube captions, download them directly instead of
running Whisper. This downloads no audio and converts the caption track to the
JSON format expected by the QA pipelines:

```bash
python download_transcripts.py --csv test_dataset.csv
```

The script writes each transcript to `Master/<index>/Transcript/` and requests
both author-provided subtitles and automatic captions. Use `--only 10` to
process one video, `--overwrite` to replace an existing transcript, or
`--dry-run` to review the output paths first.

## Run the QA approaches :

Run every approaches using the transcripts already in `Master/<index>/Transcript/`
```bash
python run.py --v test_dataset.csv
```

Run one approach (approach 3 in this case, which is Multi-agent) for every video:

```bash
python run.py --v test_dataset.csv --app 3
```

`run.py` does not download audio or run Whisper by default. If a transcript is
missing, it skips that video and explains how to add one. To explicitly use the
audio/Whisper preprocessing path instead, add `--preprocess`:

```bash
python run.py --v videos.csv --preprocess
```


Run the RAG approach for one video index:

```bash
python run.py --v videos.csv --only 2 --app 4
```

Approach 4 (RAG) builds a BGE-M3 vector index from the existing transcript.
Use `--app 1 2 3` if you do not want to load BGE-M3 — those three approaches
need no local model weights at all when `QWEN_BACKEND=api`.

Approach IDs are:

| ID | Approach |
| --- | --- |
| 1 | SingleQA |
| 2 | LLMChunking |
| 3 | MultiAgentChunking |
| 4 | RAG |

Outputs are written under `Master/<video-index>/`, including audio,
transcripts, intermediate files, and the final `finalQA.json` result for each
selected approach.

## Notes

- `yt-dlp` may need YouTube cookies for some videos. The preprocessing script
  currently looks for a `youtube_cookies.txt` file in the repository root.
- CSV input works without additional spreadsheet dependencies. The included
  requirements also support `.xlsx` and `.xls` input for `preprocess.py`.
- The repository contains local Chroma database files under
  `RAG/temp_rag_chroma_db/`; RAG runs may refresh this directory.

## License

This project is provided under the [MIT License](LICENSE).
