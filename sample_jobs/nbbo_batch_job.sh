#!/bin/bash
#$ -cwd
#$ -N nbbo_batch
#$ -j y
#$ -o logs/
# Default resources are 2 cores and 8GB per core. Remove one '#' to change them
# (WRDS limits: 8 cores and 48GB total per job).
##$ -pe onenode 2
##$ -l m_mem_free=8G
#
# Grid Engine wrapper for nbbo_batch_main.py. sample_jobs/submit_wrds_job.py submits it, from the
# repo root on WRDS (~/wrds_batch_jobs), as:
#   qsub -v OUT_ROOT=... sample_jobs/nbbo_batch_job.sh [nbbo_batch_main.py args]
# Any arguments after the script name go straight to nbbo_batch_main.py.
#
# Output goes to $OUT_ROOT/$JOB_ID/ (WRDS scratch by default; scratch is wiped
# after 7 days). Markers written there:
#   _SUCCESS   the Python job finished cleanly
#   _UPLOADED  the files were copied to Dropbox (written by `submit_wrds_job.py upload`)

set -uo pipefail

echo "Job ${JOB_ID} starting on $(hostname) at $(date)"

OUT_ROOT="${OUT_ROOT:-/scratch/$(id -gn)/${USER}/nbbo_batch}"
OUT_DIR="${OUT_ROOT}/${JOB_ID}"
mkdir -p "$OUT_DIR"
echo "Output directory: $OUT_DIR"

python3 -u sample_jobs/nbbo_batch_main.py --output-dir "$OUT_DIR" "$@"
status=$?
if (( status != 0 )); then
    echo "nbbo_batch_main.py failed with exit code $status at $(date)"
    exit "$status"
fi
touch "$OUT_DIR/_SUCCESS"

# No Dropbox upload here: WRDS compute nodes have no internet access ("network is
# unreachable"). The upload runs from the login node instead, either automatically
# with `submit --wait` or by hand with:
echo "To send results to Dropbox: python sample_jobs/submit_wrds_job.py upload ${JOB_ID}"

echo "Job ${JOB_ID} finished at $(date)"
