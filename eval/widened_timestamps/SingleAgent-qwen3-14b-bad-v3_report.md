# Widened timestamps: SingleAgent-qwen3-14b-bad-v3

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 515 kept, 470 grown (of 985).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 985 | 8s | 21s | 58% | 14% | 193s | 0.329 | 0.386 | 11% |
| Master | 99 | 10s | 22s | 45% | 7% | 160s | 0.349 | 0.373 | 12% |
| Teepa | 127 | 12s | 25s | 42% | 6% | 180s | 0.339 | 0.378 | 14% |
| FTD | 52 | 11s | 26s | 44% | 8% | 193s | 0.420 | 0.455 | 10% |
| GEMS | 22 | 10s | 18s | 41% | 9% | 134s | 0.294 | 0.377 | 5% |
| ODJ | 237 | 12s | 25s | 40% | 5% | 152s | 0.384 | 0.435 | 12% |
| SUP | 448 | 6s | 19s | 78% | 23% | 119s | 0.283 | 0.357 | 10% |

Span length after, percentiles: p10 8s, p25 14s, p50 21s, p75 36s, p90 60s
