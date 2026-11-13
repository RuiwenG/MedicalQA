# Widened timestamps: SingleAgent-qwen3.5-plus-bad-v3

Encoder `sentence-transformers/all-mpnet-base-v2`; context ±7.5s per line; growth while similarity ≥ max(0.3, 0.85 × anchor) and span ≤ 120s; no minimum length.

Methods: 677 kept, 424 grown (of 1101).

| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after | Clip similarity before | after | Similarity dropped >0.05 |
|---|---|---|---|---|---|---|---|---|---|
| All | 1101 | 21s | 30s | 15% | 2% | 395s | 0.453 | 0.468 | 16% |
| Master | 99 | 22s | 32s | 13% | 0% | 331s | 0.424 | 0.450 | 13% |
| Teepa | 127 | 27s | 36s | 7% | 0% | 158s | 0.448 | 0.455 | 17% |
| FTD | 52 | 22s | 33s | 6% | 0% | 118s | 0.502 | 0.514 | 13% |
| GEMS | 22 | 19s | 30s | 23% | 5% | 61s | 0.375 | 0.416 | 14% |
| ODJ | 237 | 29s | 38s | 3% | 0% | 395s | 0.483 | 0.500 | 11% |
| SUP | 564 | 16s | 26s | 23% | 3% | 208s | 0.446 | 0.458 | 20% |

Span length after, percentiles: p10 15s, p25 21s, p50 30s, p75 45s, p90 72s
