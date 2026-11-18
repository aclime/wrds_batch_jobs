#!/usr/bin/env python3
"""
Run nbbo_batch_main.sas as a batch job on the WRDS Cloud from your local machine
(no local SAS license needed: SAS only runs on WRDS), then send the results to
Dropbox.

This reuses sample_jobs/submit_wrds_job.py and only changes which files are
copied and which wrapper is submitted. The commands are the same:

    python sample_jobs/SAS/submit_sas_job.py setup
    python sample_jobs/SAS/submit_sas_job.py submit --wait
    python sample_jobs/SAS/submit_sas_job.py submit -- start=2025-01-01 end=2025-03-31 freq=1s symbols=SPY,IVV
    python sample_jobs/SAS/submit_sas_job.py status
    python sample_jobs/SAS/submit_sas_job.py logs JOB_ID
    python sample_jobs/SAS/submit_sas_job.py upload JOB_ID
    python sample_jobs/SAS/submit_sas_job.py disconnect

Arguments after `--` are key=value pairs passed to SAS as -sysparm (see the top
of nbbo_batch_main.sas), not the --flags used by the Python job.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import submit_wrds_job as base   # noqa: E402

# ---------------------------------------------------------------------------
# Configuration (everything else, e.g. NOTIFY_EMAIL, RCLONE_REMOTE, comes from submit_wrds_job.py)
# ---------------------------------------------------------------------------
base.JOB_SCRIPT = "sample_jobs/SAS/nbbo_batch_job.sh"
base.JOB_NAME = "nbbo_sas"                    # must match '#$ -N' in JOB_SCRIPT
base.SCRATCH_SUBDIR = "nbbo_sas"              # results go to /scratch/<group>/<user>/nbbo_sas/<job_id>
base.DROPBOX_DIR = "/wrds_batch_jobs/nbbo_sas"
base.FILES_TO_SYNC = [
    "sample_jobs/SAS/nbbo_batch_main.sas",
    "sample_jobs/SAS/check_taqmsec.sas",
    base.JOB_SCRIPT,
]
base.SELF = Path(__file__).resolve().relative_to(base.PROJECT_ROOT)


def cmd_setup(_args):
    """SAS needs no Python packages or ~/.pgpass on WRDS, so only check SAS and link Dropbox."""
    base.ensure_connected()

    print("\n[1/2] Checking SAS on WRDS...")
    r = base.remote("command -v sas qsas", check=False)
    print("  " + (" | ".join(r.stdout.split()) if r.returncode == 0
                  else "sas/qsas not found on PATH. Is SAS included in your WRDS account?"))

    print(f"\n[2/2] Linking Dropbox (rclone remote '{base.RCLONE_REMOTE}:' on WRDS)...")
    remotes = base.remote("rclone listremotes 2>/dev/null", check=False).stdout.split()
    if f"{base.RCLONE_REMOTE}:" in remotes:
        print("  already configured.")
    else:
        base.link_dropbox()
    dest = base.dropbox_dest()
    base.remote(f"rclone mkdir {base.shlex.quote(dest)}")
    base.remote(f"rclone lsd {base.shlex.quote(dest)}")
    print(f"  OK, results will go to Dropbox folder {base.DROPBOX_DIR}")


base.cmd_setup = cmd_setup

if __name__ == "__main__":
    base.main()
