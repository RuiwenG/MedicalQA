#!/bin/bash
#SBATCH --job-name=singleQA-qwen35-9b
#SBATCH --output=slurm/singleQA-qwen35-9b-%j.out
#SBATCH --error=slurm/singleQA-qwen35-9b-%j.err
#SBATCH --partition=oignat_lab
#SBATCH --nodelist=oignat01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --gres=gpu:1
#SBATCH --time=7-00:00:00
#SBATCH --requeue
#SBATCH --mail-user=rguan@scu.edu
#SBATCH --mail-type=END,FAIL

# Submit from the repository root. Default: all six playlists, current v3 prompt.
#   sbatch slurm/run_qwen35_9b_local.sh
# Smoke run:
#   sbatch slurm/run_qwen35_9b_local.sh --videos Master/1
# All prompt versions:
#   sbatch slurm/run_qwen35_9b_local.sh --versions v1 v2 v3

set -eo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
module load Anaconda3
source .mvenv/bin/activate

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

echo "Host: $(hostname); job: ${SLURM_JOB_ID:-manual}; started: $(date)"
nvidia-smi
python -u SingleQA/run_qwen35_9b_local.py \
    --model-path /WAVE/datasets/oignat_lab/QWEN3.5_9B "$@"
echo "Finished: $(date)"
