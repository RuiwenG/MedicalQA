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

## Hosted negative QA generation

`run_bad_qa_local.py` uses hosted **Qwen3.5-Plus** to generate deliberately poor QA pairs for a quality discriminator. Plus is closed-weight, so there is no local or HPC path: the runner calls `qwen3.5-plus` directly on Alibaba Model Studio (international endpoint), the same route as the `SingleAgent-qwen3.5-plus-v3` pairs. It makes one single-agent call per transcript and prompt version and processes jobs sequentially. The model and endpoint are fixed in the script, so `QWEN_BACKEND`, `QWEN_MODEL`, `QWEN_PROVIDER`, and `QWEN_THINKING_PARAM` do not affect it. It needs the `openai` package and a Model Studio key as `QWEN_API_KEY` in `.env`.

Place exactly one `transcript*.json` file in each selected `<base>/<id>/Transcript/` directory. Each file must contain a nonempty list of segments with `text` (or `transcript`) and numeric `start`; `end` or `duration` supplies the final time. Filename suffixes `-cn`, `-zh`, `-en`, `-hi`, and `-ro` select the output language; otherwise English is used. `--data-root` can select another directory containing the playlist folders.

First preview one transcript. A dry run validates input and shows expected pair counts and output paths without calling the model or writing files:

```bash
python SingleQA/run_bad_qa_local.py --bases Master --ids 1 --versions v3 --dry-run
```

Generate a small smoke run:

```bash
python SingleQA/run_bad_qa_local.py --bases Master --ids 1 --versions v3 --output-root ../negative-qa
```

Run all defaults: Master IDs 1–10, Teepa IDs 1–15, and prompt versions v1 and v3 (50 jobs). Pass `--bases` to include FTD, GEMS, ODJ, or SUP:

```bash
python SingleQA/run_bad_qa_local.py --output-root ../negative-qa
```

`--output-root` defaults to the repository. Use a separate directory, as above, to keep negatives outside evaluation tools that automatically discover QA folders under the playlist folders. Existing final results are skipped when their saved settings, prompt, and transcript match. A mismatch requires `--force` to regenerate. Failed jobs are reported individually and the process exits with status 1 if any job fails. A missing API key stops the batch immediately.

## Prompt and comparison settings

Sampling follows `SingleQA/config/settings.py`: temperature 0.3, top-p 0.9, repetition penalty 1.05, and up to 8,192 new tokens. The seed defaults to `QWEN_SEED` or 42 and is sent with each request; `--seed` overrides it, and `--seed ""` omits it. Hosted seeds are best-effort, so a rerun may not reproduce the same text. Thinking is disabled with `enable_thinking=false`.

The requested count remains `max(4, round(transcript_word_count / 250))`. Language, transcript-wide topic coverage, source timestamps, and the v1/v2/v3 standalone wording are retained. The default `--format filled` shows content placeholders after each structural label to reduce empty-question outputs; `--format labels` reproduces the original bare-label instruction. Both parse into the same JSON schema.

Every pair is asked to fail all four criteria at once, and is recorded with the intended failure `all`. Failures can appear in the question as well as the answer:

| Criterion | Intended failure |
| --- | --- |
| Trustworthiness | contradict, misinterpret, or materially misrepresent the source |
| Clarity | ambiguous wording, jargon, or confusing organization |
| Usefulness | trivial questions or uninformative guidance |
| Care safety | blaming, judgmental, or dismissive language |

Bad-QA folders generated before this change used a rotation instead: each pair had one primary failure, repeating by position in the order above. Care-safety failures target disrespectful language, excluding invented medication doses, restraint instructions, and physically harmful procedures. Pair text contains no defect labels, explanations, disclaimers, or corrections, so a discriminator must assess the content.

Transcripts are never truncated. A transcript over SingleQA's 400,000-character budget fails before any request.

This model differs from the open-weight Qwen3.5-9B and Qwen3-14B bad-QA runs. `qwen3.5-plus` is a floating alias, and Alibaba does not report which dated snapshot served a request, so runs on different dates may not come from the same weights. For a study intended to isolate prompt effects, generate positive and negative arms with the same model, endpoint, format, and settings.

## Outputs and quality checks

Each job writes under `<output-root>/<base>/<id>/SingleAgent-qwen3.5-plus-bad-<version>/`:

- `QA results/finalQA.json`: a list of objects containing `question`, `answer`, `timestamp`, `time_start_sec`, and `time_end_sec`, with no inline quality labels.
- `intermediate/Intermediate.json`: raw response, complete messages, transcript path and SHA-256, intended failure per pair, model/endpoint/provider, sampling settings, seed, token counts, finish reason, and timing.

Run metadata is saved at `<output-root>/<base>/SingleAgent-qwen3.5-plus-bad-<version>_generation_meta.json`. Metadata marks these examples with `intended_quality="bad"` and `quality_verified=false`.

Metadata preserves prior video entries and records generation settings for each video separately from the latest invocation. If a forced retry fails, the previous successful final and intermediate files remain intact; the new raw response is saved as `intermediate/Intermediate.failed.json`.

Validation requires the exact requested pair count, nonempty questions and answers, and valid timestamp ranges within the transcript duration. Reaching the output token budget fails the job; the intermediate response remains available for inspection. These checks verify structure, not whether a pair actually fails all four quality criteria or whether its timestamp supports the topic. Review or independently score the generated examples before treating them as confirmed negative labels.
