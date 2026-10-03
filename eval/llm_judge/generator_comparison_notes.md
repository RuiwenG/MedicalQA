# What three generators tell us about the LLM judges

Summary notes, 2026-10-03. The SingleAgent pairs were generated three times,
each time from the v1, v2 and v3 prompts over all 25 videos:

| Set | Generator | Pairs | Judge runs |
| --- | --- | ---: | --- |
| Plus | `qwen3.5-plus` (v2, v3 via OpenRouter `qwen/qwen3.5-plus-20260420`; v1 via DashScope) | 677 | DeepSeek 3, gpt-oss 3, Gemma 4 3 |
| 14B | `qwen/qwen3-14b` (OpenRouter, Alibaba, reasoning off) | 678 | DeepSeek 3, gpt-oss 3, Gemma 4 3 |
| 7B | `qwen/qwen-2.5-7b-instruct` (OpenRouter, Phala) | 678 | DeepSeek 2, gpt-oss 3, Gemma 4 3 |

Judges: DeepSeek V4 Pro (`deepseek-v4-pro`), gpt-oss-120b (bf16) and Gemma 4
31B (fp8, reasoning on), all on the same five-metric rubric with the
transcript in the prompt. Pass rates are means over each judge's runs.

## 1. The judges track generator quality, in the expected order

Every judge rates the pairs Plus > 14B > 7B on every quality measure.

| DeepSeek, v3 prompt | Plus | 14B | 7B |
| --- | ---: | ---: | ---: |
| Recommended without edits | 86% | 80% | 58% |
| Needs major edits, or No | 2% | 6% | 17% |
| Usefulness, mean on 1–4 (all versions) | 3.49 | 3.19 | 2.97 |
| Trustworthiness pass | 91.0% | 87.0% | 82.7% |

| Recommended without edits, v3 | Plus | 14B | 7B |
| --- | ---: | ---: | ---: |
| DeepSeek | 86% | 80% | 58% |
| gpt-oss | 96% | 93% | 85% |
| Gemma 4 | 96% | 86% | 63% |

Judges of quite different strictness all move in the same direction and order
when generation quality drops. For a paper about the evaluation method, this
is evidence that the judges respond to real quality differences.

## 2. More spread raises κ and lowers AC1: the kappa paradox in the data

Pass/fail Fleiss' κ is the mean over every combination of one run per judge;
AC1 is multi-rater Gwet's AC1 on the 4-level ratings, all versions.

| | Plus | 14B | 7B |
| --- | ---: | ---: | ---: |
| Trustworthiness Fleiss κ | 0.23 | 0.40 | 0.42 |
| Trustworthiness AC1 | 0.84 | 0.77 | 0.69 |
| Usefulness Fleiss κ | 0.11 | 0.23 | 0.40 |
| Usefulness AC1 | 0.64 | 0.45 | 0.37 |

The judges do not agree more on the weaker sets. Those pairs use more of the
scale, which κ rewards and AC1 does not. Consequences:

- A low κ on high-quality data is not by itself evidence of unreliable raters.
  This applies to the human annotators' pooled κ of 0.088 on the Plus v3 pairs.
- Report κ and AC1 together, with the rating distribution.
- Meaningful agreement measurement needs pairs that span the quality range,
  which is a reason to include weaker generators in the evaluation set.

## 3. The Standalone prompt helps only a generator that can follow it

| Standalone pass, v1 / v2 / v3 | Plus | 14B | 7B |
| --- | --- | --- | --- |
| DeepSeek | 79.9 / 91.4 / 98.1 | 92.2 / 94.1 / 97.1 | 87.4 / 81.9 / 84.3 |
| gpt-oss | 94.8 / 97.5 / 99.4 | 97.1 / 98.5 / 99.4 | 97.2 / 95.1 / 95.9 |
| Gemma 4 | 81.6 / 91.6 / 98.7 | 92.3 / 93.8 / 97.9 | 83.3 / 79.4 / 82.6 |

- Plus: large gain, about +18 points from v1 to v3 for DeepSeek and Gemma.
- 14B: smaller gain, about +5 points, and v3 costs Trustworthiness: DeepSeek
  90.3 → 87.0%, Gemma 93.1 → 88.5%, with hallucination flags rising from 7.2%
  to 10.8% (DeepSeek) and 3.5% to 7.8% (Gemma). Asking this model to name the
  subject in every pair seems to push it to add detail the video does not
  support.
- 7B: no gain. All three judges rate v2 below v1 (run ranges do not overlap)
  and none rates v3 above v1.

If the paper presents v3 as an improvement, it should say the improvement
depends on the generator.

Caveat: the Plus v1 pairs were generated through DashScope's floating
`qwen3.5-plus` alias, unlike v2 and v3, so part of the Plus v1 → v2 change may
come from the endpoint rather than the prompt.

## 4. Each judge behaves the same way across all three sets

- gpt-oss is lenient everywhere: 95–99% Standalone pass in every set, and its
  Standalone agreement with either other judge stays at κ 0.27–0.40. One
  lenient judge lowers any three-judge Fleiss' κ.
- DeepSeek and Gemma agree with each other on Standalone (κ 0.85 Plus, 0.88
  14B, 0.74 7B), close to DeepSeek's agreement with its own repeat runs.
- Gemma rates at the extremes: 95–100% top-level Clarity in every set, yet the
  largest share of 7B pairs at the lowest level (10–12%).
- DeepSeek is strictest on the 4-level scales, most of all Usefulness, where
  it gives the top level to 14–51% of v3 pairs against 68–87% for gpt-oss.
- Agreement between judges is always below agreement within a judge. Judge
  choice matters more than run-to-run noise, so a single LLM judge is not a
  neutral instrument: use a panel, or report per judge.

| Pass/fail κ | between judges (ds–go, ds–gm, go–gm) | within judge (ds, go, gm) |
| --- | --- | --- |
| Standalone, Plus | 0.32, 0.85, 0.35 | 0.84, 0.57, 0.98 |
| Standalone, 14B | 0.40, 0.88, 0.39 | 0.87, 0.74, 0.93 |
| Standalone, 7B | 0.28, 0.74, 0.27 | 0.86, 0.57, 0.84 |
| Trustworthiness, Plus | 0.15, 0.34, 0.22 | 0.57, 0.57, 0.77 |
| Trustworthiness, 14B | 0.27, 0.57, 0.31 | 0.74, 0.66, 0.83 |
| Trustworthiness, 7B | 0.38, 0.54, 0.34 | 0.66, 0.73, 0.83 |

## 5. Which metrics actually separate pairs

- Trustworthiness has the weakest agreement between judges in every set
  (κ 0.15–0.57) while each judge is self-consistent (0.57–0.83). The judges
  apply different standards, which points to an underspecified definition and
  matches the human disagreement on this metric.
- Care Safety passes at 98.5–100% for every judge and generator, so it does
  not discriminate on this corpus.
- Clarity's lowest level ("Difficult") is never used, by any judge on any
  generator. Only DeepSeek on the 7B pairs fails a meaningful share (about
  9–11%).
- The 7B set reaches all four levels on Trustworthiness, Usefulness,
  Standalone and the recommendation (up to 10–12% at the lowest level), which
  was the reason for adding weaker generators.

| Highest share at the lowest level (any judge, any version) | Plus | 14B | 7B |
| --- | ---: | ---: | ---: |
| Trustworthiness | 3% | 6% | 12% |
| Clarity | 0% | 0% | 0% |
| Usefulness | 0% | 3% | 10% |
| Recommendation | 1% | 4% | 12% |
| Standalone | 12% | 5% | 12% |

## Caveats

- Prompts: Plus used the git prompts verbatim; 14B and 7B used one changed
  format line ("Question 1: <question text>") because both models otherwise
  dropped the question. The quality criteria are identical.
- DeepSeek has two runs on the 7B set; a third waits on API credit.
- The 7B ran on Phala at unpublished precision, with the 1.05 repetition
  penalty applied; the Alibaba-served models ignored it. Gemma ran at fp8.
- Agreement is not accuracy. Without human ratings on the 14B and 7B sets we
  know how the judges relate to each other, not which is right.

## Suggested next steps

1. Have the human annotators rate a stratified sample drawn from all three
   generators, so human ratings also span the scale; then measure human–judge
   agreement and each judge's accuracy against it.
2. Tighten the Trustworthiness definition, for example separating "not
   supported by the video" from "contradicts the video".
3. Rework or drop Care Safety; make Clarity's lower levels more concrete.
4. Report κ, AC1 and the rating distributions together.

## Sources

| Set | Statistics | Page | Runs |
| --- | --- | --- | --- |
| Plus | `kappa_paradox.json`, `fleiss_judges.json` | `judge_agreement.html` | listed in `fleiss_judges.py` |
| 14B | `judges_qwen3-14b.json`, `kappa_paradox_qwen3-14b.json` | `judge_agreement_qwen3-14b.html` | `judges_qwen3-14b_manifest.json` |
| 7B | `judges_qwen2.5-7b.json`, `kappa_paradox_qwen2.5-7b.json` | `judge_agreement_qwen2.5-7b.html` | `judges_qwen2.5-7b_manifest.json` |

All files are in `eval/llm_judge/`; the judge CSVs are in `eval/results/`.
The Plus three-judge statistics are reproduced with `compare_judges.py` (no
manifest, Gemma CSVs as arguments).
