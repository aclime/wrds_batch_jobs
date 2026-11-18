#!/bin/bash
#$ -cwd
#$ -N nbbo_sas
#$ -j y
#$ -o logs/
# Default resources are 2 cores and 8GB per core. Remove one '#' to change them
# (WRDS limits: 8 cores and 48GB total per job).
##$ -pe onenode 2
##$ -l m_mem_free=8G
#
# Grid Engine wrapper for nbbo_batch_main.sas. sample_jobs/SAS/submit_sas_job.py
# submits it, from the repo root on WRDS (~/wrds_batch_jobs), as:
#   qsub -v OUT_ROOT=... sample_jobs/SAS/nbbo_batch_job.sh start=2025-01-01 end=2025-01-31 freq=1s
# Arguments after the script name are handed to SAS as -sysparm.
#
# Output goes to $OUT_ROOT/$JOB_ID/ (WRDS scratch; wiped after 7 days):
#   nbbo_<freq>_<start>_<end>.csv.gz   the results
#   _SUCCESS                           SAS finished without errors
#   _UPLOADED                          copied to Dropbox (written by `submit_sas_job.py upload`)
# The SAS log and listing go to logs/nbbo_sas_<JOB_ID>.log / .lst, and the log is
# also copied into this job's Grid Engine log so `submit_sas_job.py logs` shows it.

set -uo pipefail

echo "Job ${JOB_ID} starting on $(hostname) at $(date)"

OUT_ROOT="${OUT_ROOT:-/scratch/$(id -gn)/${USER}/nbbo_sas}"
export OUT_DIR="${OUT_ROOT}/${JOB_ID}"      # read in SAS with %sysget(OUT_DIR)
mkdir -p "$OUT_DIR" logs
echo "Output directory: $OUT_DIR"

SAS_LOG="logs/nbbo_sas_${JOB_ID}.log"
sas -noterminal sample_jobs/SAS/nbbo_batch_main.sas \
    -sysparm "$*" \
    -log "$SAS_LOG" \
    -print "logs/nbbo_sas_${JOB_ID}.lst"
status=$?

echo "----- SAS log ($SAS_LOG) -----"
cat "$SAS_LOG"
echo "----- end of SAS log -----"

# SAS exit codes: 0 = OK, 1 = warnings, 2+ = errors (3 = our own abort).
if (( status > 1 )); then
    echo "nbbo_batch_main.sas failed with exit code $status at $(date)"
    exit "$status"
fi
touch "$OUT_DIR/_SUCCESS"

# No Dropbox upload here: compute nodes have no internet access. Upload from the
# login node with `submit_sas_job.py upload`, or `submit --wait`.
echo "To send results to Dropbox: python sample_jobs/SAS/submit_sas_job.py upload ${JOB_ID}"
echo "Job ${JOB_ID} finished at $(date)"
