#!/bin/bash
#SBATCH --job-name=labelQA
#SBATCH --output=logs/labelQA-%j.out
#SBATCH --error=logs/labelQA-%j.err
#SBATCH --partition=oignat_lab
#SBATCH --nodelist=oignat01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=0-06:00:00
#SBATCH --mail-user=cawad@scu.edu
#SBATCH --mail-type=END,FAIL

# Topic labelling with local models: starts one vLLM server per model on this job's
# GPUs, then runs eval/topics/llm_label.py (flat and subtopic passes) against each.
# Several small models can share one GPU; they split the GPU memory evenly.
#
#   MODELS is a space-separated list of <folder in /WAVE/datasets/oignat_lab>=<served name>,
#   with ",mm" appended for multimodal architectures (their image slots are switched off).
#   TP is the tensor-parallel size; request that many GPUs.
#
#   MODELS="Phi=phi-4" sbatch --gres=gpu:1 slurm/run_label_vllm.sh
#   MODELS="Meta-Llama-3.1-8B-Instruct=llama-3.1-8b Mistral=ministral-8b" sbatch --gres=gpu:1 slurm/run_label_vllm.sh
#   TP=2 MODELS="Qwen2.5-VL-72B-AWQ=qwen2.5-vl-72b-awq,mm" sbatch --gres=gpu:2 slurm/run_label_vllm.sh
#
# Submit from the directory holding eval/topics/ (scripts, pairs and codebook).

set -eo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
mkdir -p logs

TP="${TP:-1}"
MAX_LEN="${MAX_LEN:-8192}"
WORKERS="${WORKERS:-32}"
# Passes to run, in order (llm_label.py --level); round 2 is LEVELS=sub2.
LEVELS="${LEVELS:-flat sub}"
# Extra llm_label.py arguments, e.g. LABEL_ARGS="--pairs eval/topics/pairs_qwen3-14b-bad-v3.jsonl --out-dir eval/topics/runs/llm_labels_bad"
read -ra EXTRA <<< "${LABEL_ARGS:-}"
# LIMIT=20 labels only the first 20 pairs (smoke test); its CSVs need deleting before a full run.
LIMIT_ARG=(); [[ -n "$LIMIT" ]] && LIMIT_ARG=(--limit "$LIMIT")
VLLM_ENV="${VLLM_ENV:-/WAVE/workarea/users/cawad/vllm-env}"
MODEL_ROOT=/WAVE/datasets/oignat_lab
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# Same node workarounds as run_judge_vllm.sh: no NVLink between the L40S pairs, no nvcc.
export NCCL_P2P_DISABLE=1 VLLM_USE_FLASHINFER_SAMPLER=0
export XDG_CACHE_HOME="$VLLM_ENV/cache" VLLM_CACHE_ROOT="$VLLM_ENV/cache/vllm" \
       TRITON_CACHE_DIR="$VLLM_ENV/cache/triton" TORCHINDUCTOR_CACHE_DIR="$VLLM_ENV/cache/inductor"

read -ra SPECS <<< "$MODELS"
N=${#SPECS[@]}
UTIL=$(python3 -c "print(round(0.90 / $N, 2))")
echo "Host: $(hostname)  Job: ${SLURM_JOB_ID:-manual}  Models: ${MODELS}  TP: ${TP}  Memory share: ${UTIL}  Started: $(date)"
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv

PIDS=(); PORTS=(); NAMES=()
for i in "${!SPECS[@]}"; do
    spec="${SPECS[$i]}"; folder="${spec%%=*}"; rest="${spec#*=}"; served="${rest%%,*}"
    port=$((8000 + (${SLURM_JOB_ID:-0} % 500) * 2 + i))
    extra=()
    [[ "$rest" == *",mm" ]] && extra+=(--limit-mm-per-prompt '{"image": 0, "video": 0}')
    "$VLLM_ENV/bin/vllm" serve "$MODEL_ROOT/$folder" --served-model-name "$served" --port "$port" \
        --tensor-parallel-size "$TP" --max-model-len "$MAX_LEN" --gpu-memory-utilization "$UTIL" \
        --enable-prefix-caching --disable-custom-all-reduce --seed 0 "${extra[@]}" \
        > "logs/labelQA-${SLURM_JOB_ID:-manual}-${served}.vllm.log" 2>&1 &
    PIDS+=($!); PORTS+=("$port"); NAMES+=("$served")
    # Start servers one at a time so each measures free memory after the last has claimed its share.
    for t in $(seq 1 120); do
        curl -sf "http://127.0.0.1:${port}/health" >/dev/null && break
        kill -0 "${PIDS[$i]}" 2>/dev/null || { echo "vLLM for $served exited during start-up; see its .vllm.log"; exit 1; }
        sleep 15
    done
    curl -sf "http://127.0.0.1:${port}/health" >/dev/null || { echo "vLLM for $served not healthy after 30 min"; exit 1; }
    echo "vLLM ready for $served on port $port: $(date)"
done
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT

# Label with all models at once; each model runs its passes in LEVELS order.
JOBS=()
for i in "${!NAMES[@]}"; do
    (
        export VLLM_URL="http://127.0.0.1:${PORTS[$i]}/v1/chat/completions"
        for level in $LEVELS; do
            "$VLLM_ENV/bin/python" -u eval/topics/llm_label.py --model "${NAMES[$i]}" --level "$level" --workers "$WORKERS" "${LIMIT_ARG[@]}" "${EXTRA[@]}"
        done
    ) &
    JOBS+=($!)
done
status=0
for j in "${JOBS[@]}"; do wait "$j" || status=1; done
echo "Finished: $(date)"
exit $status
