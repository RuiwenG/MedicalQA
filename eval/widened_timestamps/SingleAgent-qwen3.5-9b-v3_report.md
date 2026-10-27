# Widened timestamps: SingleAgent-qwen3.5-9b-v3

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 704 kept, 347 grown (of 1051).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 1051 | 29s | 38s | 8% | 1% | 416s | 0.473 | 0.484 | 18% |
| Master | 99 | 32s | 43s | 5% | 0% | 250s | 0.477 | 0.470 | 26% |
| Teepa | 127 | 32s | 45s | 2% | 0% | 227s | 0.490 | 0.490 | 24% |
| FTD | 52 | 38s | 48s | 0% | 0% | 416s | 0.579 | 0.590 | 10% |
| GEMS | 22 | 24s | 32s | 9% | 0% | 247s | 0.429 | 0.468 | 23% |
| ODJ | 226 | 36s | 42s | 8% | 1% | 215s | 0.504 | 0.522 | 16% |
| SUP | 525 | 24s | 34s | 11% | 2% | 227s | 0.445 | 0.460 | 16% |

Span length after, percentiles: p10 18s, p25 24s, p50 38s, p75 61s, p90 90s
