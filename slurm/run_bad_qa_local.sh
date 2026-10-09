#!/bin/bash
#SBATCH --job-name=negativeQA
#SBATCH --output=slurm/negativeQA-%j.out
#SBATCH --error=slurm/negativeQA-%j.err
#SBATCH --partition=oignat_lab
#SBATCH --nodelist=oignat01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --gres=gpu:1
#SBATCH --time=7-00:00:00

# Submit from the repository root:
#   sbatch slurm/run_bad_qa_local.sh
# Defaults: all 25 videos in Master/Teepa, prompt versions v1 and v3.
# Extra arguments are passed directly to the Python runner, for example:
#   sbatch slurm/run_bad_qa_local.sh --bases Master --ids 1 --versions v3
#   sbatch slurm/run_bad_qa_local.sh --versions v3 --output-root ../negative-qa

set -eo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"

module load Anaconda3
source .mvenv/bin/activate

# Use the downloaded checkpoint and keep inference fully offline.
export QWEN_BACKEND=local
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
QWEN_MODEL_DIR="/WAVE/datasets/oignat_lab/QWEN3"

echo "Host: $(hostname)"
echo "Job ID: ${SLURM_JOB_ID:-manual}"
echo "Model: ${QWEN_MODEL_DIR}"
echo "Started: $(date)"
nvidia-smi

python -u SingleQA/run_bad_qa_local.py --model-path "$QWEN_MODEL_DIR" "$@"

echo "Finished: $(date)"
