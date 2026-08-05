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
# By default this runs approach 1 (single agent) and approach 3 (multi-agent).
# Override the approaches on the command line, e.g.:
#   sbatch slurm/run_script.sh 3       # only approach 3
#   sbatch slurm/run_script.sh 1 2 4   # approaches 1, 2 and 4

set -eo pipefail

# --- Run from the directory the job was submitted from (repo root) ---
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"

# --- Activate the virtual environment ---
module load Anaconda3
source .mvenv/bin/activate

# --- Job parameters (positional args, with defaults) ---
# approach: 1=Single, 2=DualAgent(LLMChunking), 3=MultiAgent, 4=RAG
APPS=("$@")
if [ ${#APPS[@]} -eq 0 ]; then
    APPS=(1 3)
fi

echo "===================================================="
echo "Host:      $(hostname)"
echo "Job ID:    ${SLURM_JOB_ID}"
echo "Approaches: ${APPS[*]}"
echo "Started:   $(date)"
echo "===================================================="
nvidia-smi

for APP in "${APPS[@]}"; do
    echo "----------------------------------------------------"
    echo ">>> Running approach ${APP} at $(date)"
    echo "----------------------------------------------------"
    python run.py --v test_dataset.csv --app "${APP}"
    echo ">>> Finished approach ${APP} at $(date)"
done

echo "Finished: $(date)"
