# Widened timestamps: SingleAgent-qwen3.5-4b-v3

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 692 kept, 383 grown (of 1075).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 1075 | 31s | 41s | 7% | 1% | 758s | 0.506 | 0.514 | 18% |
| Master | 99 | 36s | 46s | 4% | 0% | 584s | 0.486 | 0.486 | 14% |
| Teepa | 116 | 34s | 43s | 3% | 0% | 338s | 0.502 | 0.497 | 26% |
| FTD | 37 | 49s | 55s | 0% | 0% | 191s | 0.565 | 0.559 | 22% |
| GEMS | 22 | 38s | 47s | 0% | 0% | 148s | 0.443 | 0.447 | 5% |
| ODJ | 237 | 34s | 44s | 5% | 1% | 199s | 0.515 | 0.524 | 19% |
| SUP | 564 | 26s | 36s | 11% | 1% | 758s | 0.506 | 0.519 | 17% |

Span length after, percentiles: p10 19s, p25 26s, p50 41s, p75 61s, p90 92s
