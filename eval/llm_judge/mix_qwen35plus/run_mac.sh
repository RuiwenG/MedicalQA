#!/usr/bin/env bash
# Usage: run_mac.sh MODEL SHORTNAME [extra judge.py args...]
# Runs the five mixed decks (clip only, shuffled order kept) on the Mac Studio.
set -u
cd "$(dirname "$0")/../../.."
MODEL=$1; NAME=$2; shift 2
for k in 1 2 3 4 5; do
  echo "=== $NAME fold $k start $(date)"
  python3 eval/llm_judge/judge.py --mode full --model "$MODEL" --context clip \
    --qa-data eval/llm_judge/mix_qwen35plus/qa_data_qwen35plus-mix-fold$k.json \
    --spans eval/widened_timestamps/qwen35plus-v3.json eval/widened_timestamps/SingleAgent-qwen3.5-plus-bad-v3.json \
    --keep-order --workers 8 --tag qwen35plus-mix-fold$k-$NAME-clip-run1 "$@"
done
echo "=== $NAME all done $(date)"
