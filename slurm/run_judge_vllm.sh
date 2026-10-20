#!/bin/bash
#SBATCH --job-name=judgeQA
#SBATCH --output=slurm/judgeQA-%j.out
#SBATCH --error=slurm/judgeQA-%j.err
#SBATCH --partition=oignat_lab
#SBATCH --nodelist=oignat01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --gres=gpu:2
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --mail-user=cawad@scu.edu
#SBATCH --mail-type=BEGIN,END,FAIL,REQUEUE

# LLM judge on a local model: starts a vLLM server on this job's two GPUs, waits
# for it, then runs eval/llm_judge/judge.py against it RUNS times. Submit from the
# repository root; arguments after the run count go to judge.py, e.g.
#   sbatch slurm/run_judge_vllm.sh 3 --qa-data eval/llm_judge/qa_data_qwen3-14b.json
#   TAG=qwen3-14b-new69 sbatch slurm/run_judge_vllm.sh 3 --qa-data eval/llm_judge/qa_data_qwen3-14b-new69.json
# Run r is tagged <TAG>-run<r> (TAG defaults to qwen3-14b), as the API judges' runs were.
# Needs a vLLM environment (python -m pip install vllm requests) at VLLM_ENV, outside the
# lab's project space, whose 500 GB group quota is nearly full.

set -eo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"

RUNS="${1:-3}"; shift || true
TAG="${TAG:-qwen3-14b}"
MODEL_DIR="${MODEL_DIR:-/WAVE/datasets/oignat_lab/Qwen2.5-VL-72B-AWQ}"
SERVED="${SERVED:-qwen2.5-vl-72b-awq}"
# The longest new-video prompts (SUP) run to about 18k tokens; Qwen2.5-VL handles 32k.
MAX_LEN="${MAX_LEN:-32768}"
VLLM_ENV="${VLLM_ENV:-/WAVE/workarea/users/cawad/vllm-env}"
PORT=$((8000 + ${SLURM_JOB_ID:-0} % 1000))
export VLLM_URL="http://127.0.0.1:${PORT}/v1/chat/completions"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# oignat01's L40S pairs have no NVLink and NCCL's peer-to-peer path hangs at start-up
# (workers spin at 100% with nothing loaded), so all-reduce goes through host memory.
export NCCL_P2P_DISABLE=1
# FlashInfer's sampler JIT-compiles with nvcc, which the node lacks; use torch's.
export VLLM_USE_FLASHINFER_SAMPLER=0
# vLLM, Triton and torch.compile cache to $HOME by default; keep them beside the env.
export XDG_CACHE_HOME="$VLLM_ENV/cache" VLLM_CACHE_ROOT="$VLLM_ENV/cache/vllm" \
       TRITON_CACHE_DIR="$VLLM_ENV/cache/triton" TORCHINDUCTOR_CACHE_DIR="$VLLM_ENV/cache/inductor"

echo "Host: $(hostname)  Job: ${SLURM_JOB_ID:-manual}  Model: ${MODEL_DIR}  Runs: ${RUNS}  Started: $(date)"
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv

# Text only: no image slots, so the vision tower's memory goes to the KV cache.
"$VLLM_ENV/bin/vllm" serve "$MODEL_DIR" --served-model-name "$SERVED" --port "$PORT" \
    --tensor-parallel-size 2 --max-model-len "$MAX_LEN" --gpu-memory-utilization 0.90 \
    --limit-mm-per-prompt '{"image": 0, "video": 0}' --enable-prefix-caching --disable-custom-all-reduce \
    --seed 0 > "slurm/judgeQA-${SLURM_JOB_ID:-manual}.vllm.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT

for i in $(seq 1 120); do
    curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null && break
    kill -0 $SERVER 2>/dev/null || { echo "vLLM exited during start-up; see the .vllm.log"; exit 1; }
    sleep 15
done
curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null || { echo "vLLM not healthy after 30 min"; exit 1; }
echo "vLLM ready: $(date)"

for r in $(seq 1 "$RUNS"); do
    echo ">>> run $r of $RUNS at $(date)"
    "$VLLM_ENV/bin/python" -u eval/llm_judge/judge.py --mode full --model "$SERVED" \
        --tag "${TAG}-run${r}" --workers 8 "$@"
done
echo "Finished: $(date)"
