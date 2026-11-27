#!/usr/bin/env bash
# Usage: resume_mac.sh MODEL SHORTNAME FIRST_FOLD [extra judge.py args...]
# Finishes FIRST_FOLD from its newest raw JSONL (successful pairs are skipped),
# then runs the remaining folds fresh. For when the Mac Studio dropped off mid-run.
set -u
cd "$(dirname "$0")/../../.."
MODEL=$1; NAME=$2; FIRST=$3; shift 3
SLUG=$(echo "$MODEL" | tr / -)
for k in $(seq "$FIRST" 5); do
  TAG=qwen35plus-mix-fold$k-$NAME-clip-run1
  RESUME=()
  if [ "$k" = "$FIRST" ]; then
    prev=$(ls -t eval/llm_judge/runs/${SLUG}_${TAG}_full_*.jsonl 2>/dev/null | head -1)
    [ -n "$prev" ] && RESUME=(--resume "$prev")
  fi
  echo "=== $NAME fold $k start $(date) ${RESUME[*]:-}"
  python3 eval/llm_judge/judge.py --mode full --model "$MODEL" --context clip \
    --qa-data eval/llm_judge/mix_qwen35plus/qa_data_qwen35plus-mix-fold$k.json \
    --spans eval/widened_timestamps/qwen35plus-v3.json eval/widened_timestamps/SingleAgent-qwen3.5-plus-bad-v3.json \
    --keep-order --workers 8 --tag "$TAG" "${RESUME[@]}" "$@"
done
echo "=== $NAME all done $(date)"
