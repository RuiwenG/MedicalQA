# Widened timestamps: SingleAgent-qwen3-14b-v2

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 594 kept, 503 grown (of 1097).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 1097 | 10s | 22s | 50% | 12% | 287s | 0.371 | 0.416 | 12% |
| Master | 99 | 11s | 24s | 44% | 2% | 88s | 0.406 | 0.435 | 12% |
| Teepa | 127 | 23s | 35s | 20% | 0% | 287s | 0.445 | 0.453 | 19% |
| FTD | 52 | 13s | 28s | 37% | 0% | 112s | 0.469 | 0.529 | 8% |
| GEMS | 22 | 10s | 24s | 45% | 0% | 95s | 0.341 | 0.393 | 9% |
| ODJ | 237 | 17s | 30s | 26% | 5% | 273s | 0.435 | 0.466 | 14% |
| SUP | 560 | 7s | 17s | 69% | 21% | 247s | 0.313 | 0.373 | 11% |

Span length after, percentiles: p10 9s, p25 14s, p50 22s, p75 39s, p90 70s
