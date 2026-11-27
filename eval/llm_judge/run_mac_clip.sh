#!/usr/bin/env bash
# Clip-only judge runs of one generator's pairs on the lab Mac Studio, one judge after
# another (they share one server). Settings match the earlier Mac Studio runs.
#   eval/llm_judge/run_mac_clip.sh qwen3.5-4b-v3                 # all three judges
#   eval/llm_judge/run_mac_clip.sh qwen3.5-9b-v3 gptoss gemma    # some of them
# Reads eval/llm_judge/qa_data_<GEN>.json and eval/widened_timestamps/SingleAgent-<GEN>.json;
# set QA_DATA and SPANS for sets named otherwise:
#   QA_DATA=eval/llm_judge/qa_data_qwen35plus-v3.json SPANS=eval/widened_timestamps/qwen35plus-v3.json \
#     eval/llm_judge/run_mac_clip.sh qwen35plus-v3 gptoss gemma
set -u
cd "$(dirname "$0")/../.."
GEN=$1; shift
JUDGES=("$@")
[ ${#JUDGES[@]} -eq 0 ] && JUDGES=(glm gptoss gemma)
COMMON=(--mode full --context clip --workers 8
        --qa-data "${QA_DATA:-eval/llm_judge/qa_data_$GEN.json}"
        --spans "${SPANS:-eval/widened_timestamps/SingleAgent-$GEN.json}")
run() {  # MODEL SHORTNAME [extra args]
  local model=$1 name=$2; shift 2
  echo "=== $model start $(date)"
  python3 eval/llm_judge/judge.py "${COMMON[@]}" --model "$model" --tag "$GEN-$name-clip-run1" "$@"
  echo "=== $model end $(date) exit $?"
}
for j in "${JUDGES[@]}"; do
  case $j in
    glm)    run glm-5.3-flash low --reasoning-effort low ;;
    gptoss) run gpt-oss-120b-mxfp4-bf16 gptoss-medium --reasoning-effort medium ;;
    gemma)  run gemma-4-31b-it-8bit gemma-think ;;
    *) echo "unknown judge $j (glm, gptoss, gemma)"; exit 2 ;;
  esac
done
echo "=== all done $(date)"
