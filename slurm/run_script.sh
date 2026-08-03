#!/bin/bash
#SBATCH --job-name=medicalQA
#SBATCH --output=slurm/medicalQA-%j.out
#SBATCH --error=slurm/medicalQA-%j.err
#SBATCH --partition=oignat_lab
#SBATCH --nodelist=oignat01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --gres=gpu:1
#SBATCH --time=7-00:00:00
#SBATCH --mail-user=rguan@scu.edu
#SBATCH --mail-type=END,FAIL

# Submit from the repo root:  sbatch slurm/run_script.sh
# Override the video id / approach on the command line, e.g.:
#   sbatch slurm/run_script.sh 1 3     # video 1, approach 3 (multi-agent)
#   sbatch slurm/run_script.sh 1 1     # video 1, approach 1 (single agent)

set -eo pipefail

# --- Run from the directory the job was submitted from (repo root) ---
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"

# --- Activate the virtual environment ---
module load Anaconda3
source .mvenv/bin/activate

# --- Job parameters (positional args, with defaults) ---
# VIDEO_ID="${1:-1}"     # which video index in test_dataset.csv
APP="${1:-3}"          # approach: 1=Single, 2=DualAgent(LLMChunking), 3=MultiAgent, 4=RAG

echo "===================================================="
echo "Host:      $(hostname)"
echo "Job ID:    ${SLURM_JOB_ID}"
# echo "Video ID:  ${VIDEO_ID}"
echo "Approach:  ${APP}"
echo "Started:   $(date)"
echo "===================================================="
nvidia-smi

# python run.py --v test_dataset.csv --only "${VIDEO_ID}" --app "${APP}"

python run.py --v test_dataset.csv --app "${APP}"

echo "Finished: $(date)"
