#!/bin/bash
#SBATCH --job-name=singleQA
#SBATCH --output=slurm/singleQA-%j.out
#SBATCH --error=slurm/singleQA-%j.err
#SBATCH --partition=oignat_lab
#SBATCH --nodelist=oignat01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --gres=gpu:1
#SBATCH --time=2-00:00:00
#SBATCH --requeue
#SBATCH --mail-user=cawad@scu.edu
#SBATCH --mail-type=BEGIN,END,FAIL,REQUEUE

# SingleAgent QA pairs from the local Qwen3-14B weights (bf16, thinking off,
# seed 42), loaded once per job. Submit from the repository root; arguments go
# to SingleQA/run_prompt_versions.py, e.g.
#   sbatch slurm/run_prompt_versions_local.sh --bases SUP --versions v3
#   sbatch slurm/run_prompt_versions_local.sh --bases FTD GEMS ODJ --versions v3
# Videos that already have output are skipped, so a resubmit (or Slurm's own
# requeue after a node failure) resumes where the job stopped.

set -eo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"

module load Anaconda3
source .mvenv/bin/activate

export QWEN_BACKEND=local
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export QWEN_SEED=42
QWEN_MODEL_DIR="/WAVE/datasets/oignat_lab/QWEN3"

echo "Host: $(hostname)"
echo "Job ID: ${SLURM_JOB_ID:-manual}"
echo "Model: ${QWEN_MODEL_DIR}"
echo "Started: $(date)"
nvidia-smi

python -u SingleQA/run_prompt_versions.py --endpoint local --model Qwen/Qwen3-14B \
    --model-path "$QWEN_MODEL_DIR" --format filled "$@"

echo "Finished: $(date)"
