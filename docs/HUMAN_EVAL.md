# Human Evaluation of Generated QAs

_How to run the human evaluation of the dementia QA pairs, what the metrics
mean, and how results are collected._
_Last updated: 2026-08-03._

There are two ways to run the evaluation. Both use the same eight metrics, the
same 1–5 Likert scale, and the same QA pairs, and their outputs aggregate
together.

| | Use it for | Results land in |
| --- | --- | --- |
| **[`eval/web/`](../eval/web/README.md)** — public website | **External annotators.** Send a link, no setup on their side. | A Supabase table, written after every pair |
| [`eval/QA_Educational_Eval_UI.ipynb`](../eval/QA_Educational_Eval_UI.ipynb) — notebook | The research team slicing the data locally | `eval/results/*.xlsx` |

**Use the website when sharing with anyone outside the team.** Deployment,
Supabase setup, and how batches are assigned are documented in
[`eval/web/README.md`](../eval/web/README.md). The rest of this file describes
the notebook; sections 1 and 3 (what is evaluated, the metrics) apply to both.

> The older notebook, `eval/Final_Human_Eval_UI.ipynb`, is the previous
> mentorship-focused evaluation (Colab + Google Drive + a hand-built Excel).
> It is kept for reference only; use one of the two tools above for this project.

---

## 1. What is being evaluated

The notebook auto-discovers every `finalQA.json` in the repo:

```
<dataset>/<video>/<approach>/QA results/finalQA.json
```

| Dataset   | Videos | Approaches | QA pairs |
| --------- | ------ | ---------- | -------- |
| `Master/` | 10     | SingleAgent, MultiAgent-LLMChunking | ~400 |
| `Teepa/`  | 15     | SingleAgent, DualAgent, MultiAgent-LLMChunking, RAG | ~1,190 |

(Counts are approximate because a few runs produced fewer than 20 pairs per
video; the notebook prints the exact per-approach counts when it loads.)

Only the `question` and `answer` fields are used. Extra per-approach metadata
(topics, timestamps, source segments) is ignored so all approaches are judged
on the same footing.

## 2. Running the evaluation

### Locally (Jupyter / VS Code)

Open `eval/QA_Educational_Eval_UI.ipynb` from anywhere inside the repo and run
all cells. The notebook finds the repo root on its own (it looks for `Master/`
or `Teepa/` in the working directory and its parents). Requires `ipywidgets`,
`pandas`, `openpyxl` — the first cell installs anything missing.

### On Google Colab

Upload or sync the repo to Google Drive, open the notebook in Colab, and set
`COLAB_BASE_DIR` in the second cell to the repo's Drive path (default
`/content/drive/MyDrive/MedicalQA`). The notebook mounts Drive and, on save,
also triggers a browser download of the result file.

### The four selection steps

1. **Dataset** — `Master`, `Teepa`, or both.
2. **Videos** — multi-select, all selected by default.
3. **Approaches + options** — multi-select, plus:
   - **Blind mode** (default on): hides the approach name while rating. The
     approach is still recorded in the output. Keep this on to avoid bias.
   - **Shuffle** (default on): randomizes QA order to avoid order effects.
   - **Seed** (default 42): drives both shuffling and sampling. Annotators
     using the same seed and sample size rate the **same subset in the same
     order**, which is what you want for inter-annotator agreement.
   - **Sample**: N QA pairs per approach per video (0 = all). Rating
     everything is ~1,600 pairs, so a realistic session uses sampling — e.g.
     Teepa + all 4 approaches + sample 5 = 15 × 4 × 5 = 300 pairs.
4. **Metrics + annotator name** — deselect metrics you don't want to rate;
   the name is used for the checkpoint and the output filename.

## 3. Metrics

All metrics are statements rated on a 1–5 Likert scale
(1 = Strongly Disagree … 5 = Strongly Agree). No default rating is
pre-selected; every selected metric must be rated before moving on.

| Metric | Judged on | Statement (abridged) |
| --- | --- | --- |
| Question Fluency | Q | Grammatically correct, no language errors |
| Answer Fluency | A | Grammatically correct, no language errors |
| Question Clarity | Q | Easy to comprehend, specific, not ambiguous |
| Answer Clarity | A | Easy to comprehend, explanation straightforward |
| QA-Alignment | Q+A | The answer directly addresses what the question asks |
| Question Educational Value | Q | Targets meaningful dementia/dementia-care knowledge a learner would benefit from |
| Answer Educational Value | A | Teaches something meaningful about dementia care, accurately and applicably |
| Standalone Quality | Q+A | Makes sense without the video — no unresolved "the transcript" / "the speaker" references |

The first five are structural metrics carried over from the earlier mentorship
evaluation. The two **Educational Value** metrics replace the mentorship
metrics. **Standalone Quality** is new: several pipelines produce answers that
open with "Based on the transcript provided…", which is exactly the failure
mode this metric captures.

## 4. Checkpointing and resuming

After every *Submit & Next* (and *Previous* / *Save & Exit*), all scores are
written to `eval/results/checkpoint_<annotator>.json`. If a session dies or the
annotator stops midway:

1. Re-run the notebook, make the **same selections** (dataset, videos,
   approaches, seed, sample size), and enter the **same annotator name**.
2. The notebook restores all saved scores and jumps to the first unrated pair.

Checkpoints are keyed by QA id (`<dataset>_v<video>_<approach>_q<n>`), so
restoring is robust even if the selection changes — only the pairs present in
both are restored.

## 5. Outputs

*Save & Exit* or finishing the last pair writes an Excel file:

```
eval/results/<datasets>_<annotator>_Eval_<timestamp>.xlsx
```

One row per QA pair in the session: `Dataset`, `Video Index`, `Approach`,
`QA ID`, `Question`, `Answer`, one column per metric (blank = not rated), and
`Annotator`. The notebook then prints mean scores per approach for the rated
pairs.

The last cell of the notebook aggregates **all** result files in
`eval/results/` across annotators and prints mean scores per approach, per
dataset × approach, and agreement on any pairs rated by more than one person.
It reads both the `.xlsx` files this notebook writes and the `.csv` files
downloaded from the website — the column names are identical.

`eval/results/` is in `.gitignore` — checkpoints and spreadsheets stay local.
Collect annotators' files by hand (email, Drive, etc.), drop them into
`eval/results/`, and run the aggregation cell. Ratings collected through the
website do not need collecting at all; query Supabase directly, or export its
`ratings_final` view as CSV into the same folder.

## 6. Known data quirks

- `Master/*/SingleAgent` has 199 pairs (one video produced 19 instead of 20);
  `Teepa/*/SingleAgent` has 288 (see the reproducibility note in
  [TECHNIQUE_NOTES.md](TECHNIQUE_NOTES.md) §1 — pair counts varied before the
  seed was fixed).
- Some approaches emit Markdown in answers (`##` headings, `**bold**`,
  bullet lists). The UI shows answers as plain text with line breaks
  preserved; the Markdown syntax is visible to annotators. That is
  intentional — formatting noise is part of answer quality and should count
  against Fluency/Clarity if it hurts readability.
