# Widened timestamps: SingleAgent-qwen3.5-9b-bad-v3

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 329 kept, 208 grown (of 537).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 537 | 16s | 26s | 23% | 1% | 421s | 0.417 | 0.432 | 18% |
| Master | 99 | 15s | 27s | 19% | 0% | 307s | 0.387 | 0.396 | 20% |
| Teepa | 127 | 18s | 29s | 22% | 0% | 241s | 0.420 | 0.429 | 27% |
| FTD | 52 | 11s | 23s | 46% | 2% | 421s | 0.475 | 0.489 | 19% |
| GEMS | 22 | 20s | 28s | 0% | 0% | 142s | 0.422 | 0.434 | 9% |
| ODJ | 237 | 16s | 25s | 21% | 3% | 123s | 0.416 | 0.436 | 14% |

Span length after, percentiles: p10 15s, p25 19s, p50 26s, p75 39s, p90 53s
