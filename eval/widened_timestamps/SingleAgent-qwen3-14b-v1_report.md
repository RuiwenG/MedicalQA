# Widened timestamps: SingleAgent-qwen3-14b-v1

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 553 kept, 541 grown (of 1094).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 1094 | 11s | 24s | 44% | 6% | 265s | 0.410 | 0.456 | 13% |
| Master | 99 | 17s | 31s | 24% | 0% | 131s | 0.418 | 0.439 | 15% |
| Teepa | 127 | 21s | 31s | 24% | 1% | 186s | 0.443 | 0.468 | 16% |
| FTD | 51 | 11s | 25s | 47% | 0% | 91s | 0.421 | 0.510 | 4% |
| GEMS | 22 | 18s | 35s | 14% | 0% | 117s | 0.438 | 0.492 | 9% |
| ODJ | 237 | 13s | 24s | 36% | 4% | 167s | 0.445 | 0.479 | 18% |
| SUP | 558 | 8s | 21s | 57% | 9% | 265s | 0.384 | 0.441 | 11% |

Span length after, percentiles: p10 12s, p25 16s, p50 24s, p75 42s, p90 73s
