# Local negative QA generation

`run_bad_qa_local.py` uses a local **Qwen3-14B** checkpoint to generate deliberately poor QA pairs for a quality discriminator. It makes one single-agent call per transcript and prompt version, loads the model once, and processes jobs sequentially. It uses no OpenRouter or other inference API and needs no API key; `--model-path` selects the checkpoint regardless of `QWEN_BACKEND` or `QWEN_MODEL`.

## HPC setup and runs

Run from the repository root with an existing NVIDIA CUDA GPU allocation. Activate the repository's HPC environment:

```bash
module load Anaconda3
source .mvenv/bin/activate
```

The environment needs a CUDA-compatible PyTorch build, Transformers, and Accelerate; `python-dotenv` is optional for loading `.env`. The [official Qwen3-14B model card](https://huggingface.co/Qwen/Qwen3-14B) requires `transformers>=4.51.0`. Download the complete Transformers checkpoint, tokenizer, and configuration before submitting an offline compute job. Loading uses `local_files_only=True`, bfloat16, and `device_map="auto"`; allocate sufficient GPU memory for weights and generation.

Place exactly one `transcript*.json` file in each selected `Master/<id>/Transcript/` or `Teepa/<id>/Transcript/` directory. Each file must contain a nonempty list of segments with `text` (or `transcript`) and numeric `start`; `end` or `duration` supplies the final time. Filename suffixes `-cn`, `-zh`, `-en`, `-hi`, and `-ro` select the output language; otherwise English is used. `--data-root` can select another directory containing `Master/` and `Teepa/`.

First preview one transcript. A dry run validates input and shows expected pair counts and output paths without loading a model or writing files:

```bash
python SingleQA/run_bad_qa_local.py --model-path /WAVE/datasets/oignat_lab/QWEN3 \
  --bases Master --ids 1 --versions v3 --dry-run
```

Generate a small smoke run inside the GPU allocation:

```bash
python SingleQA/run_bad_qa_local.py --model-path /WAVE/datasets/oignat_lab/QWEN3 \
  --bases Master --ids 1 --versions v3 --output-root ../negative-qa
```

Run all defaults: Master IDs 1–10, Teepa IDs 1–15, and prompt versions v1 and v3 (50 jobs):

```bash
python SingleQA/run_bad_qa_local.py --model-path /WAVE/datasets/oignat_lab/QWEN3 \
  --output-root ../negative-qa
```

`--output-root` defaults to the repository. Use a separate directory, as above, to keep negatives outside evaluation tools that automatically discover QA folders under the repository's `Master/` and `Teepa/`. Existing final results are skipped when their saved settings, prompt, and transcript match. A mismatch requires `--force` to regenerate. Failed jobs are reported individually and the process exits with status 1 if any job fails. A model-loading failure stops the batch without repeatedly loading the same checkpoint.

To submit through SLURM, run from the repository root:

```bash
sbatch slurm/run_bad_qa_local.sh
```

The script uses `/WAVE/datasets/oignat_lab/QWEN3`, activates `.mvenv` after loading Anaconda3, and requests one GPU, 16 CPUs, 128 GB RAM, and seven days on partition `oignat_lab`, node `oignat01`. Its default run covers all 25 transcripts for v1 and v3 and writes under the repository. Runner flags pass through to the Python command; for example:

```bash
sbatch slurm/run_bad_qa_local.sh --bases Master --ids 1 --versions v3 \
  --output-root ../negative-qa
```

To run the full batch with a separate output directory:

```bash
sbatch slurm/run_bad_qa_local.sh --output-root ../negative-qa
```

## Prompt and comparison settings

Sampling follows `SingleQA/config/settings.py`: temperature 0.3, top-p 0.9, repetition penalty 1.05, and up to 8,192 new tokens. The seed defaults to `QWEN_SEED` or 42 and is reset before each generation; `--seed` overrides it, and `--seed ""` disables seeding. Thinking is disabled with `enable_thinking=False`.

The requested count remains `max(4, round(transcript_word_count / 250))`. Language, transcript-wide topic coverage, source timestamps, and the v1/v2/v3 standalone wording are retained. The default `--format filled` shows content placeholders after each structural label to reduce empty-question outputs; `--format labels` reproduces the original bare-label instruction. Both parse into the same JSON schema.

Each pair has one intended primary failure, repeating by position:

| Pair positions | Intended failure |
| --- | --- |
| 1, 5, 9, … | Trustworthiness: contradict, misinterpret, or materially misrepresent the source |
| 2, 6, 10, … | Clarity: ambiguous wording, jargon, or confusing organization |
| 3, 7, 11, … | Usefulness: trivial questions or uninformative guidance |
| 4, 8, 12, … | Care safety: blaming, judgmental, or dismissive language |

The prompt asks the other qualities to remain close to ordinary QA. Care-safety failures target disrespectful language, excluding invented medication doses, restraint instructions, and physically harmful procedures. Pair text contains no defect labels, explanations, disclaimers, or corrections, so a discriminator must assess the content.

The runner rejects prompts whose input tokens plus the output budget exceed Qwen3-14B's native 32,768-token context, documented in the [official model card](https://huggingface.co/Qwen/Qwen3-14B). It does not truncate transcripts or assume YaRN is configured. Dry runs do not tokenize, so this context check occurs only during a real run.

The local backend differs from previous OpenRouter runs in serving implementation and precision. Matching sampling settings and seeds does not guarantee identical outputs across backends. For a study intended to isolate prompt effects, generate positive and negative arms with the same checkpoint, backend, format, and settings.

## Outputs and quality checks

Each job writes under `<output-root>/<base>/<id>/SingleAgent-qwen3-14b-bad-<version>/`:

- `QA results/finalQA.json`: a list of objects containing `question`, `answer`, `timestamp`, `time_start_sec`, and `time_end_sec`, with no inline quality labels.
- `intermediate/Intermediate.json`: raw response, complete messages, transcript path and SHA-256, intended failure per pair, model/checkpoint/backend, sampling settings, seed, token counts, and timing.

Run metadata is saved at `<output-root>/<base>/SingleAgent-qwen3-14b-bad-<version>_generation_meta.json`. Metadata marks these examples with `intended_quality="bad"` and `quality_verified=false`.

Metadata preserves prior video entries and records generation settings for each video separately from the latest invocation. If a forced retry fails, the previous successful final and intermediate files remain intact; the new raw response is saved as `intermediate/Intermediate.failed.json`.

Validation requires the exact requested pair count, nonempty questions and answers, and valid timestamp ranges within the transcript duration. Reaching the output token budget fails the job; the intermediate response remains available for inspection. These checks verify structure, not whether a pair actually fails its assigned quality criterion or whether its timestamp supports the topic. Review or independently score the generated examples before treating them as confirmed negative labels.
