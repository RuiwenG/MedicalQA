# Claude Opus 5 judge in Google Colab

This workflow evaluates the current SingleAgent prompt revisions with Claude
Opus 5 and produces an offline HTML/Markdown report in the style of the earlier
DeepSeek comparison artifact.

The v3 default covers **226 Q&A pairs from 25 videos**. The optional full
prompt-ablation scope covers **677 pair-version records**: v1=226, v2=225 and
v3=226.

## What the runner measures

- Trustworthiness, Clarity, Usefulness and Care Safety are evaluated with the
  source transcript.
- Standalone is binary-only and is evaluated in a separate call that never
  receives the transcript.
- The transcript-free Standalone result is supplied to the second call only so
  the final caregiver recommendation can consider all five criteria.
- The judge never receives the approach or prompt-version label.
- Results are checkpointed after every completed Q&A and can be resumed.

## Files

- `Claude_Opus_Judge_Colab.ipynb`: notebook to upload to Colab.
- `Claude_Opus_Judge_Colab_Bundle.zip`: compact data/code bundle to upload when
  the notebook requests it.
- `claude_judge.py`: resumable API runner.
- `build_claude_report.py`: deterministic HTML/Markdown report generator.

## One-time setup

1. Create or open your Anthropic Console account and enable API billing.
2. Create an API key. Do not add the key to this repository, the ZIP, a notebook
   cell or Google Drive.
3. Open <https://colab.research.google.com/>.
4. Choose **File → Upload notebook** and upload
   `Claude_Opus_Judge_Colab.ipynb`.
5. In Colab, open the **Secrets** panel using the key icon on the left.
6. Add a secret named exactly `ANTHROPIC_API_KEY`, paste the key as its value,
   and enable notebook access.
7. Run the notebook from the top. When its upload box opens, select
   `Claude_Opus_Judge_Colab_Bundle.zip`.
8. Allow the notebook to mount Google Drive. Checkpoints and reports are stored
   under `MyDrive/MedicalQA_Claude_Judge`.

A GPU runtime is not required because Colab is calling the Claude API; the CPU
runtime is sufficient.

## Choose the run

The configuration cell starts with:

```python
SCOPE = "v3"
FIRST_N = 0
```

Use these settings as follows:

| Goal | `SCOPE` | `FIRST_N` | Expected records |
|---|---|---:|---:|
| Small latest-v3 calibration | `v3` | `40` | 40 |
| Every generated v3 Q&A | `v3` | `0` | 226 |
| Full v1/v2/v3 artifact comparison | `v1-v2-v3` | `0` | 677 |

The free validation cell prints the selection before any API call. Do not run
the paid cell if those counts are different.

### Important corpus warning

The checked-in `eval-pilot-netlify-review/qa_data.json` currently contains 225
Q&As that exactly match the repository's `SingleAgent-v2` snapshot. It has zero
exact question-and-answer matches with the latest 226-pair `SingleAgent-v3`
snapshot used by this notebook. Therefore, do **not** describe the current
Supabase human rows as human ratings of this latest v3 corpus and do not compare
the two by row number. The report builder will compare only exact matching Q&A
text (or a matching QA ID), so an accidental cross-corpus comparison produces
zero shared pairs instead of a misleading agreement score.

## Recommended sequence

1. Run the free validation cell.
2. Run the three-pair smoke test. Confirm that three rows and a JSONL checkpoint
   appear in Google Drive.
3. Run the full paid cell. If Colab disconnects, reconnect, rerun the setup and
   configuration cells, and rerun the same paid cell. The stable run name causes
   the script to reuse completed records.
4. Run the report cell.
5. Preview the HTML report in Colab and retain the HTML, Markdown, CSV, JSONL and
   metadata JSON together.

The main outputs are:

```text
MyDrive/MedicalQA_Claude_Judge/
  claude_opus5_v3_full.jsonl       # append-only checkpoint and raw judgments
  claude_opus5_v3_full.csv         # human-UI-compatible ratings
  claude_opus5_v3_full.meta.json   # model, usage, measured cost + corpus hashes
  claude_opus5_v3_full_report.html
  claude_opus5_v3_full_report.md
```

## Add the DeepSeek and human comparisons

In the report cell, set either optional path when the corresponding CSV is
available in Google Drive:

```python
DEEPSEEK_CSV = "/content/drive/MyDrive/path/to/deepseek_results.csv"
HUMAN_CSV = "/content/drive/MyDrive/path/to/human_or_supabase_export.csv"
```

The human file may be:

- a CSV downloaded from the evaluation website; or
- a CSV export of the Supabase `ratings_v3_final` view.

When multiple humans rated a pair, the report uses per-field majority consensus
and excludes tied fields from model-versus-human agreement. It reports exact
binary agreement and Cohen's kappa for every metric. Rows are matched only when
the QA ID agrees or the normalized question and answer text are exactly the
same within the same prompt version; the report never position-matches different
Q&As.

## Interpretation

The report is a deterministic summary of the saved ratings; it does not ask
Claude to invent a narrative after the run. Human ratings remain the primary
reference. Manually review all Trustworthiness failures against their
transcripts before reporting hallucination prevalence in a paper.

Keep the JSONL checkpoint and metadata beside the report. Resume checks reject
records whose Q&A text, model, effort or rubric differs, and the metadata stores
SHA-256 fingerprints for both the full Q&A file and the selected corpus.
