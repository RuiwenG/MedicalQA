# Widened timestamps: SingleAgent-qwen3-14b-v3

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 591 kept, 504 grown (of 1095).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 1095 | 9s | 23s | 52% | 12% | 261s | 0.358 | 0.411 | 12% |
| Master | 99 | 12s | 23s | 46% | 2% | 128s | 0.397 | 0.424 | 17% |
| Teepa | 127 | 27s | 38s | 18% | 2% | 158s | 0.412 | 0.430 | 16% |
| FTD | 52 | 20s | 34s | 23% | 2% | 131s | 0.478 | 0.522 | 10% |
| GEMS | 21 | 24s | 25s | 29% | 14% | 118s | 0.344 | 0.387 | 10% |
| ODJ | 237 | 17s | 27s | 27% | 6% | 261s | 0.440 | 0.468 | 15% |
| SUP | 559 | 6s | 18s | 74% | 19% | 155s | 0.295 | 0.371 | 9% |

Span length after, percentiles: p10 9s, p25 14s, p50 23s, p75 37s, p90 61s
