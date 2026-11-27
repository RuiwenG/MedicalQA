#!/bin/bash
#SBATCH --job-name=singleQA-qwen35-4b
#SBATCH --output=slurm/singleQA-qwen35-4b-%j.out
#SBATCH --error=slurm/singleQA-qwen35-4b-%j.err
#SBATCH --partition=oignat_lab
#SBATCH --nodelist=oignat01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --gres=gpu:1
#SBATCH --time=2-00:00:00
#SBATCH --requeue

# Qwen3.5-4B with the same prompt and sampling settings as the 9B run.
# Submit from the repository root. Default: all six playlists, current v3 prompt.
#   sbatch slurm/run_qwen35_4b_local.sh
# Smoke run:
#   sbatch slurm/run_qwen35_4b_local.sh --videos Master/1
# VENV is the environment to activate (default: .mvenv in the repository).

set -eo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
module load Anaconda3
source "${VENV:-.mvenv}/bin/activate"

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

echo "Host: $(hostname); job: ${SLURM_JOB_ID:-manual}; started: $(date)"
nvidia-smi
python -u SingleQA/run_qwen35_9b_local.py --model 4b "$@"
echo "Finished: $(date)"
