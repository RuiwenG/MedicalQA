#!/bin/bash
# GLM-5.3-Flash on the lab Mac Studio, clip only, low effort: one run per mixed deck.
cd "$(dirname "$0")/../../.."
for k in 1 2 3 4 5; do
  echo "=== fold $k  $(date)"
  python3 eval/llm_judge/judge.py --mode full --model glm-5.3-flash --context clip \
    --qa-data eval/llm_judge/mix_qwen35plus/qa_data_qwen35plus-mix-fold$k.json \
    --spans eval/widened_timestamps/qwen35plus-v3.json eval/widened_timestamps/SingleAgent-qwen3.5-plus-bad-v3.json \
    --keep-order --reasoning-effort low --workers 8 --tag qwen35plus-mix-fold$k-low-clip-run1
done
echo "=== all done $(date)"
