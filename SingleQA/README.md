# SingleAgent QA generation with local Qwen3.5-9B

`run_qwen35_9b_local.py` runs the existing **SingleQA single-agent architecture**:
one transcript-wide generation call using the shared positive QA prompt and parser.
It loads `/WAVE/datasets/oignat_lab/QWEN3.5_9B` once and processes videos sequentially,
fully offline, with thinking disabled. No inference API or API key is used.

Defaults cover every numbered video folder in **Master, Teepa, ODJ, SUP, GEMS,
and FTD** (currently 94 transcripts), using the current **v3** prompt and `filled`
format. It reads exactly one `transcript*.json` from each video's `Transcript/`
folder; alternative transcription folders are separate source variants and are
not included. Pair counts and sampling match `config/settings.py`: at least four
pairs, approximately one per 250 words, temperature 0.3, top-p 0.9, repetition
penalty 1.05, and an 8,192-token output budget. The seed defaults to 42.

From the repository root, preview all inputs without loading weights or writing files:

```bash
python SingleQA/run_qwen35_9b_local.py --dry-run
```

Submit the full batch on the HPC:

```bash
sbatch slurm/run_qwen35_9b_local.sh
```

The SLURM script follows this repository's HPC configuration: partition
`oignat_lab`, node `oignat01`, one GPU, 16 CPUs, 128 GB host RAM, seven days,
`module load Anaconda3`, and `.mvenv/bin/activate`. Adjust resource directives
if your allocation differs. The environment needs CUDA-compatible PyTorch,
Accelerate, and a Transformers release exposing `Qwen3_5ForConditionalGeneration`
(the repository pins Transformers 5.14.1). The complete Transformers checkpoint
and tokenizer must already exist at the model path. The loader requires a GPU
with bfloat16 support and enough GPU memory for the weights and generation.
The model class follows the [official checkpoint configuration](https://huggingface.co/Qwen/Qwen3.5-9B/blob/main/config.json)
and [Transformers Qwen3.5 documentation](https://huggingface.co/docs/transformers/en/model_doc/qwen3_5).

Optional runs, with all flags passed through by the SLURM script:

```bash
# One-video smoke run
sbatch slurm/run_qwen35_9b_local.sh --videos Master/1

# Run all three prompt versions
sbatch slurm/run_qwen35_9b_local.sh --versions v1 v2 v3

# Select playlists or a separate output root
sbatch slurm/run_qwen35_9b_local.sh --bases ODJ SUP --output-root /path/to/qa-output

# Inside an existing GPU allocation, run Python directly
python -u SingleQA/run_qwen35_9b_local.py
```

Use `--data-root /path/to/MedicalQA` if transcripts live elsewhere. The output
root defaults to the data root. Results are stored at:

```text
<output-root>/<base>/<id>/SingleAgent-qwen3.5-9b-v3/QA results/finalQA.json
<output-root>/<base>/<id>/SingleAgent-qwen3.5-9b-v3/intermediate/Intermediate.json
<output-root>/<base>/SingleAgent-qwen3.5-9b-v3_generation_meta.json
```

The final schema retains `question`, `answer`, `timestamp`, `time_start_sec`, and
`time_end_sec`. Intermediate files retain the raw response, full prompts, input
hash, model path, generation settings, seeds, token counts, and timing. Metadata is
updated after each video. Logs go to `slurm/singleQA-qwen35-9b-<jobid>.out/.err`.

Resubmitting skips validated completed results when the input, prompt, model path,
and settings match. A mismatch requires `--force`. Failed individual videos do
not stop the remaining videos; any failure gives a nonzero exit status. A model
loading failure stops the batch immediately. Outputs are written atomically;
failed forced retries preserve prior successful artifacts. Run one job at a time
for the same output folders.

The runner rejects empty QA text, incorrect pair counts, invalid timestamp ranges,
and responses that reach the output budget. Inspect the raw response for failed
jobs and use `--max-new-tokens 16384` if more output space is needed. It checks the
exact prompt plus output budget against the checkpoint's context limit and never
truncates a transcript. These are structural checks; source grounding and care
quality still require review. A dry run checks transcripts and prompts, but does
not test token budgets, GPU memory, or model compatibility.

### Recovering validation failures without repeating inference

If the batch reports timestamp or pair-count failures, the model may have
completed generation successfully; the saved response failed structural checks.
The Qwen3.5 runner now accepts Markdown around structural labels such as
`**Answer 1:**` and converts `H:MM:SS` timestamps to the equivalent `M:SS` format
expected by the shared parser. Each `Timestamp` line is attached to the question
it follows, so a skipped or repeated label number (`Timestamp 8:` then
`Timestamp 10:`) no longer leaves a pair without a time, and a range written as
`11:45-End` ends at the transcript's last second. To reparse existing responses on the HPC without
a GPU allocation:

```bash
source .mvenv/bin/activate
python SingleQA/run_qwen35_9b_local.py --reparse-only
```

Use the same `--model-path`, `--data-root`, `--output-root`, version, and sampling
flags as the original invocation if you changed any defaults. The checkpoint
does not need to be present for this mode. Completed finals are skipped, and
missing finals are written only if the saved response matches the current input
and generation settings and passes validation. No model calls are made. A job
with no saved response is reported as failed; it is never silently regenerated.

Each parsed response now produces `intermediate/validation_report.json` with
the expected and actual pair count, transcript duration, timestamp lines, parsed
pairs, and validation result. Failure logs include the actual start/end values
and allowed transcript endpoint. Markdown cleanup cannot repair a genuinely
missing pair, missing timestamp, or an invented out-of-bounds time; such jobs
remain failed for inspection. The runner never fabricates or clamps timestamps
or lowers the requested pair count to make a response pass. Saved raw responses
and successful results remain available.

### Retrying failed responses with new seeds

A response with no timestamps or the wrong pair count has to be generated again,
and the same seed would reproduce it. `--retries N` tries up to N more seeds
(`--seed`+1, +2, ...) for a video whose response fails validation or reaches the
output budget, and stops at the first one that passes:

```bash
sbatch slurm/run_qwen35_9b_local.sh --retries 3
```

Every attempt is saved as `intermediate/attempts/seed-<seed>.json`. A seed that
already has a saved response for the same input and settings is reparsed instead
of generated, so resubmitting does not repeat GPU work, and the seed-42
responses from runs made before this option existed are reused from
`Intermediate.json`. `Intermediate.json` holds the response behind `finalQA.json`:
its `seed` is the requested `--seed` (which completed results are matched on) and
`sampling_seed` is the seed that produced it. The batch log and metadata also
report `sampling_seed` and `attempts`. `--reparse-only` tries every saved seed.
A video that fails on all seeds lists each seed's error and writes no final.

## Local negative QA generation

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
