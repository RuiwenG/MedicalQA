# Analysing the Human Evaluation Results

_How the annotators' text labels become scores, which statistics we run on them
and why, and what the data shows so far._
_Last updated: 2026-08-06._

[HUMAN_EVAL.md](HUMAN_EVAL.md) covers **collecting** ratings — the metrics, the
form, the two annotation tools. This file covers everything downstream of that:
the score map, the statistics, and the interim findings.

> **Annotation is still in progress.** Section 6 is a snapshot of the first 40
> ratings, kept here so the analysis pipeline can be checked against real data
> before the full study lands. Nothing in it is a result.

---

## 1. Running it

```
python eval/analyze_ratings.py                        # report to stdout
python eval/analyze_ratings.py --out eval/results/analysis
python eval/analyze_ratings.py --by dataset           # regroup the comparison
```

To generate the result figures after refreshing the tables:

```
python eval/plot_results.py
```

This writes five publication-ready plots (PNG and editable SVG) to
`eval/results/analysis/plots/`: full ordinal distributions, approach-level
Top-2 rates with Wilson intervals, the error taxonomy, annotator calibration,
and inter-annotator agreement.

[`eval/analyze_ratings.py`](../eval/analyze_ratings.py) reads **every** `.csv`
and `.xlsx` in `eval/results/` and concatenates them, so the website's Supabase
export and the notebook's spreadsheets analyse together — the column names are
identical by design. Requires `pandas`, `numpy`, `scipy`; no other dependency.

To refresh the website data: Supabase → Table Editor → `ratings_final` → Export
CSV → drop into `eval/results/`. Use `ratings_final`, not `ratings`: the raw
table is append-only, so a pair re-rated via the *Previous* button appears more
than once. The script also collapses duplicates on `(session_id, qa_uid)`
itself, keeping the latest `created_at`, in case exports get combined.

`--out` writes `ratings_scored.csv` (the tidy per-rating table with score
columns added, for any further analysis) plus one CSV per summary table.

## 2. Score mapping

Label order comes from the `METRICS` table in
[`eval/web/index.html`](../eval/web/index.html), so the map cannot drift from
what annotators actually saw. **Higher is always better.**

| Metric | 4 | 3 | 2 | 1 |
| --- | --- | --- | --- | --- |
| QA Alignment/Trustworthiness | Excellent | Good | Fair | Poor |
| QA Accessibility | Very easy to understand | Easy | Somewhat difficult | Difficult |
| QA Educational/Actionable Value | Highly actionable | Actionable | Limited usefulness | Not useful |
| QA Mental Health Value | Highly supportive | Supportive | Limited support | Unsupportive / potentially harmful |
| Caregiver recommendation | Yes | Yes, but with minor edits | Only after major revisions | No |

Per-metric binaries map `Yes → 1`, `No → 0`. Error labels are **not** scored —
they are multi-select and counted separately (§4).

Two rules about missing data:

- **Empty cells are ignored per metric.** An annotator can deselect a metric, so
  every metric carries its own denominator. Never assume a shared `n`.
- **Unmapped labels become `NaN` and are reported loudly.** A typo or a renamed
  option would otherwise shrink a denominator silently. If the script prints an
  "Unmapped labels" warning, fix the map before reading anything else.

The eight pre-pilot columns (`q_fluency`, `a_fluency`, `q_clarity`, `a_clarity`,
the old answer-only `qa_alignment`, `q_edu_value`, `a_edu_value`, `standalone`)
survive in the export but are never populated by the pilot form. They are
dropped when empty, so they cannot be mistaken for missing data.

## 3. Why there are no means

These are **ordinal** labels. "Excellent → Good" is not the same distance as
"Fair → Poor", so the 4/3/2/1 codes are ranks, not quantities, and their
average is not a real number about anything. A mean of 3.2 also hides the shape
that matters most here: a metric where most pairs are fine and a few are
actively harmful averages the same as one where everything is mediocre, and
those two call for completely different fixes.

What replaces it:

| Instead of | We report | Because |
| --- | --- | --- |
| mean | full label distribution (n and %) | the shape is the finding |
| mean | median, mode, IQR | rank statistics valid on ordinal data |
| mean | **% Top-2** (score ≥ 3) with a Wilson 95% CI | one honest headline number: how many pairs are good enough |
| t-test | **Mann-Whitney U** | rank-based, no interval assumption |
| difference in means | **Cliff's delta** | effect size as P(a>b) − P(a<b) |
| — | **Fisher's exact test** on Top-2 and Yes/No proportions | exact at small n, which is what we have |

Wilson intervals are used rather than the normal approximation because they stay
sane near 0% and 100%, where several of these metrics sit.

**Top-2 is a threshold, not a truth.** Score ≥ 3 means "Good/Easy/Actionable/
Supportive or better". It is defined once as `TOP2_MIN` in the script; if the
team prefers a stricter bar, change it there and every table follows.

## 4. What the report contains

0. **Coverage** — ratings, unique pairs, annotators, sessions, and the
   annotator × approach crosstab. Prints a small-sample warning under n=100.
1. **Ordinal distributions** — per metric, pooled: label counts, median/mode/IQR,
   % Top-2 with CI, worst-label share, and the binary Yes rate.
2. **By approach** (or any `--by` column) — the same distributions split, then
   Mann-Whitney U + Cliff's delta on the ordinal scores and Fisher exact on the
   Top-2 and binary proportions. Dataset (Master vs Teepa) is always reported as
   a secondary breakdown, since the two corpora are different source material.
3. **Error taxonomy** — multi-select, so the script splits on `"; "` and counts
   each label independently; one rating can raise several. Reported as % of all
   ratings for that metric, broken down by group. This is the most diagnostic
   section — it says *what is wrong*, not just how wrong.
4. **Per-annotator calibration** — median and % Top-2 per person per metric, plus
   median seconds/pair and a flag on any rating submitted in under 15 seconds.
   Leniency differences between annotators can easily exceed the effect being
   measured, so this is a precondition for reading §2, not an appendix.
5. **Inter-annotator agreement** — on pairs rated by more than one annotator (the
   anchor set): Krippendorff's **ordinal** α, plus exact and within-1 agreement
   rates. α ≥ 0.80 reliable, 0.67–0.80 tentative, below that unreliable.

α is implemented directly in the script (with the ordinal difference function
and the standard coincidence matrix) to avoid a dependency; it was verified to
match the `krippendorff` reference package to four decimals on this dataset and
on Krippendorff's published example. Exact and within-1 rates are reported
alongside it because α is unstable on few units and hard to sanity-check alone.

---

## 5. Interim findings — snapshot 2026-08-06

**n = 40 ratings, 34 unique QA pairs, 3 annotators, batches 7 and 8 of 8.**
Preliminary. Every comparison below is underpowered; the confidence intervals
are the honest part.

### Form is fine, substance is not

| Metric | % Top-2 (95% CI) | median | binary Yes |
| --- | --- | --- | --- |
| QA Alignment/Trustworthiness | 80% (65–90) | Excellent | 78% |
| QA Accessibility | 88% (74–95) | Easy | 80% |
| QA Educational/Actionable Value | 60% (45–74) | Actionable | 52% |
| QA Mental Health Value | 55% (40–69) | Supportive | 58% |

The pipelines produce readable, video-grounded text. What they do not reliably
produce is guidance a caregiver could act on, or language that offers support:
**only 52% of pairs were judged to give useful or actionable guidance**, and just
3 of 40 reached "Highly supportive". On the overall verdict, 45% of pairs need
major revisions or are unusable as-is (25% "Only after major revisions", 20%
"No").

Note the binaries run harsher than the attribute scales on the same metric —
annotators will grant "Actionable" as a label and then answer *No* to "does this
give useful or actionable guidance?". Worth resolving in the rubric; for now,
treat the binary as the stricter reading.

### Approach comparison: inconclusive, but directionally consistent

MultiAgent-LLMChunking leads SingleAgent on both substance metrics, and nothing
reaches significance at 20 ratings per arm:

| Metric | MultiAgent | SingleAgent | Mann-Whitney p | Cliff's δ |
| --- | --- | --- | --- | --- |
| Mental Health Value | 65% Top-2 | 45% | 0.058 | +0.33 (small) |
| Educational/Actionable | 70% | 50% | 0.191 | +0.23 (small) |
| Alignment | 85% | 75% | 0.794 | +0.04 (negligible) |
| Accessibility | 90% | 85% | 0.105 | −0.28 (small, favours SingleAgent) |

Accessibility is the one metric trending the other way — SingleAgent drew 65%
"Very easy to understand" against MultiAgent's 30%, which fits longer, more
structured multi-agent answers. Worth watching as n grows.

The sharpest signal is in the error taxonomy, not the scales:

- **"Not actionable": 8 SingleAgent vs 2 MultiAgent** ratings.
- **All 5 "Unsupportive / potentially harmful" ratings are SingleAgent.**
- "Neutral / limited support" is the single most common error overall (14/40,
  35% of ratings) — the dominant failure mode is flat, affect-free advice rather
  than anything unsafe.
- Alignment errors are rare and evenly split; only 2 hallucinations, both
  MultiAgent.

### Two caveats that outweigh any p-value here

1. **Annotators are not calibrated.** On Educational value, one annotator's
   Top-2 rate is 76% against another's 47%; on Mental Health, 76% against 35%.
   That gap is larger than any approach difference measured. Approach assignment
   is roughly balanced across annotators (9/8, 7/10, 4/2), so it is not
   confounding the comparison — but the absolute percentages depend heavily on
   who rated, and pooling across annotators with different thresholds is only
   defensible while that balance holds.

2. **Agreement cannot be assessed yet.** Only 6 overlapping pairs. Educational
   (α=0.88) and Mental Health (α=0.80) look fine; **Accessibility is α=−0.35
   with 17% exact agreement** — worse than chance. On 6 units that could be
   noise, or it could be a genuinely ambiguous rubric. Within-1 agreement is
   83–100% across all metrics, so annotators are not wildly apart; they are
   splitting adjacent labels.

### Before scaling up

- **Raise the anchor overlap.** `ANCHOR_SIZE: 20` in
  [`eval/web/config.js`](../eval/web/config.js) gives 20 shared pairs per
  annotator at full length; 6 have been reached so far. Agreement claims need
  the full anchor set completed, not just more ratings overall.
- **Check the Accessibility rubric.** A metric annotators cannot agree on will
  not produce a usable result at any sample size. Re-check α once the anchors
  are in; if it stays low, the "Very easy to understand" / "Easy" boundary needs
  an explicit definition.
- **Reconcile attribute labels with binaries**, which currently disagree on the
  same metric often enough to matter.
- **Coverage is 2 of 8 batches.** Everything above is drawn from batches 7 and 8
  and may not represent the corpus.
