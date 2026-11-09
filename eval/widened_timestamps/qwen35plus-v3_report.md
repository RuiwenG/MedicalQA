# Widened timestamps: qwen35plus-v3

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 772 kept, 327 grown (of 1099).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 1099 | 35s | 43s | 6% | 1% | 347s | 0.491 | 0.500 | 14% |
| Master | 99 | 53s | 62s | 1% | 0% | 175s | 0.501 | 0.499 | 10% |
| Teepa | 126 | 56s | 68s | 0% | 0% | 215s | 0.497 | 0.505 | 12% |
| FTD | 52 | 46s | 56s | 0% | 0% | 174s | 0.571 | 0.597 | 8% |
| GEMS | 22 | 27s | 36s | 0% | 0% | 97s | 0.474 | 0.497 | 5% |
| ODJ | 237 | 42s | 50s | 3% | 0% | 233s | 0.544 | 0.543 | 16% |
| SUP | 563 | 24s | 32s | 10% | 2% | 347s | 0.458 | 0.473 | 15% |

Span length after, percentiles: p10 19s, p25 28s, p50 43s, p75 67s, p90 97s
