#!/usr/bin/env python3
"""
Run nbbo_batch_main.py as a batch job on the WRDS Cloud from your local machine,
and send the results to Dropbox.

One-time setup (checks the remote Python, ~/.pgpass, and links Dropbox via rclone):
    brew install rclone                     # needed locally once, to authorize Dropbox in your browser
    python sample_jobs/submit_wrds_job.py setup

Submit:
    python sample_jobs/submit_wrds_job.py submit                  # defaults set in nbbo_batch_main.py
    python sample_jobs/submit_wrds_job.py submit --wait           # wait for the job, then upload to Dropbox
    python sample_jobs/submit_wrds_job.py submit -- --start 2025-01-01 --end 2025-03-31 --freq 1s

Monitor and collect:
    python sample_jobs/submit_wrds_job.py status                  # qstat
    python sample_jobs/submit_wrds_job.py logs JOB_ID             # job stdout/stderr
    python sample_jobs/submit_wrds_job.py upload JOB_ID           # rclone copy to Dropbox, run on the login node
    python sample_jobs/submit_wrds_job.py disconnect              # close the shared SSH connection

About SSH: WRDS needs your password plus Duo two-factor approval. This script
opens one shared ("master") SSH connection; you log in once and every later
ssh/scp call reuses it until CONTROL_PERSIST has passed with no activity.
"""
import argparse
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WRDS_HOST = "wrds-cloud.wharton.upenn.edu"
REMOTE_DIR = "wrds_batch_jobs"            # under your WRDS home directory
DROPBOX_DIR = "/wrds_batch_jobs/nbbo"     # Dropbox folder for the results
RCLONE_REMOTE = "dropbox"                 # name of the rclone remote on WRDS (created by `setup`)
NOTIFY_EMAIL = None                       # e.g. "you@school.edu" to get Grid Engine emails
JOB_SCRIPT = "sample_jobs/nbbo_batch_job.sh"   # relative to the repo root
JOB_NAME = "nbbo_batch"                   # must match '#$ -N' in JOB_SCRIPT
SCRATCH_SUBDIR = "nbbo_batch"             # results go to /scratch/<group>/<user>/<this>/<job_id>
CONTROL_PERSIST = "8h"
POLL_SECONDS = 120

# Local files copied to REMOTE_DIR before each submit (paths relative to the repo root;
# the same layout is recreated on WRDS so the imports and blacklist paths still work)
FILES_TO_SYNC = [
    "sample_jobs/nbbo_batch_main.py",
    "wrds_queries.py",
    "wrds_creds.py",
    JOB_SCRIPT,
    "blacklist_files/missing_days_nbbom.txt",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # repo root
SELF = Path(__file__).resolve().relative_to(PROJECT_ROOT)  # sample_jobs/submit_wrds_job.py
CONTROL_PATH = Path.home() / ".ssh" / "wrds-cm-%C"


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------
def wrds_username() -> str:
    sys.path.insert(0, str(PROJECT_ROOT))
    from wrds_creds import read_pgpass
    return read_pgpass()[0]


def target() -> str:
    return f"{wrds_username()}@{WRDS_HOST}"


def ssh_opts() -> list:
    return ["-o", "ControlMaster=auto",
            "-o", f"ControlPath={CONTROL_PATH}",
            "-o", f"ControlPersist={CONTROL_PERSIST}",
            "-o", "ServerAliveInterval=60"]


def ensure_connected():
    """Open the shared SSH connection if it isn't already up (prompts for password + Duo)."""
    alive = subprocess.run(["ssh", *ssh_opts(), "-O", "check", target()],
                           capture_output=True).returncode == 0
    if alive:
        return
    CONTROL_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    print(f"Connecting to {WRDS_HOST}: enter your WRDS password and approve the Duo prompt.")
    r = subprocess.run(["ssh", *ssh_opts(), "-N", "-f", target()])
    if r.returncode != 0:
        sys.exit("Couldn't open an SSH connection to WRDS.")


def remote(cmd: str, *, capture=True, check=True, tty=False, stdin=None, input_text=None):
    """Run `cmd` in a login shell on the WRDS head node (so qsub etc. are on PATH)."""
    ensure_connected()
    args = ["ssh", *ssh_opts(), *(["-t"] if tty else []), target(), f"bash -lc {shlex.quote(cmd)}"]
    r = subprocess.run(args, text=True, capture_output=capture, stdin=stdin, input=input_text)
    if r.returncode == 255:
        sys.exit(f"SSH connection error while running: {cmd}\n{r.stderr or ''}")
    if check and r.returncode != 0:
        sys.exit(f"Remote command failed (exit {r.returncode}): {cmd}\n{r.stdout or ''}{r.stderr or ''}")
    return r


def last_line(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def scratch_root() -> str:
    """Absolute path of this project's folder in WRDS scratch, e.g. /scratch/<group>/<user>/nbbo_batch."""
    r = remote(f'echo "/scratch/$(id -gn)/$USER/{SCRATCH_SUBDIR}"')
    return last_line(r.stdout)


def job_out_dir(job_id: str) -> str:
    return f"{scratch_root()}/{job_id}"


def remote_test(path: str) -> bool:
    # Double quotes (not shlex.quote) so $HOME etc. expand on the remote side.
    return remote(f'test -e "{path}"', check=False).returncode == 0


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_setup(_args):
    ensure_connected()

    print("\n[1/3] Checking Python on WRDS...")
    r = remote('python3 -c "import sys, wrds, pandas; from importlib.metadata import version; '
               'print(\'python\', sys.version.split()[0], \'| wrds\', version(\'wrds\'), '
               '\'| pandas\', pandas.__version__)"',
               check=False)
    print("  " + (last_line(r.stdout) if r.returncode == 0
                  else f"problem importing wrds/pandas:\n{r.stderr}"))

    print("\n[2/3] Checking ~/.pgpass on WRDS (the job uses it to log in to the database)...")
    if remote_test("$HOME/.pgpass"):
        print("  found.")
    else:
        local_pgpass = Path.home() / ".pgpass"
        answer = input(f"  Not found. Copy your local {local_pgpass} to WRDS (made readable only by you)? [y/N] ")
        if answer.strip().lower() == "y":
            with open(local_pgpass) as f:
                # set -C (noclobber): never overwrite an existing .pgpass
                remote("set -C && umask 077 && cat > ~/.pgpass && chmod 600 ~/.pgpass", stdin=f)
            print("  copied.")
        else:
            print("  skipped. The job can't connect to the database without it.")

    print(f"\n[3/3] Linking Dropbox (rclone remote '{RCLONE_REMOTE}:' on WRDS)...")
    remotes = remote("rclone listremotes 2>/dev/null", check=False).stdout.split()
    if f"{RCLONE_REMOTE}:" in remotes:
        print("  already configured.")
    else:
        link_dropbox()
    dest = dropbox_dest()
    remote(f"rclone mkdir {shlex.quote(dest)}")
    remote(f"rclone lsd {shlex.quote(dest)}", check=True)  # fails if the token doesn't work
    print(f"  OK, results will go to Dropbox folder {DROPBOX_DIR}")


def dropbox_dest() -> str:
    return f"{RCLONE_REMOTE}:{DROPBOX_DIR.lstrip('/')}"


def link_dropbox():
    """Authorize Dropbox in a browser on this machine, then save the token in WRDS's rclone config."""
    if shutil.which("rclone") is None:
        sys.exit("  rclone isn't installed on this Mac. Run `brew install rclone`, then rerun setup.")
    print("  Opening your browser: sign in to Dropbox and click Allow...")
    r = subprocess.run(["rclone", "authorize", "dropbox"], stdout=subprocess.PIPE, text=True)
    m = re.search(r"--->\s*(\{.*?\})\s*<---", r.stdout, re.DOTALL) or re.search(r"^(\{.*\})\s*$", r.stdout, re.M)
    if r.returncode != 0 or not m:
        sys.exit(f"  rclone authorize didn't return a token:\n{r.stdout}")
    token = m.group(1).strip()

    # The token arrives on stdin (not the command line, so it never shows in `ps` on the
    # shared login node), and is appended to a config file only you can read.
    name = shlex.quote(RCLONE_REMOTE)
    remote(f"""
set -e
umask 077
conf="$(rclone config file | tail -1)"
mkdir -p "$(dirname "$conf")"
if grep -q "^\\[{RCLONE_REMOTE}\\]" "$conf" 2>/dev/null; then
    echo "a [{RCLONE_REMOTE}] section already exists in $conf; not overwriting" >&2; exit 3
fi
{{ printf '\\n[%s]\\ntype = dropbox\\ntoken = ' {name}; cat; printf '\\n'; }} >> "$conf"
chmod 600 "$conf"
""", input_text=token)
    print("  token saved on WRDS.")


def sync_files():
    dirs = sorted({str(Path(REMOTE_DIR, f).parent) for f in FILES_TO_SYNC} | {f"{REMOTE_DIR}/logs"})
    remote("mkdir -p " + " ".join(shlex.quote(d) for d in dirs))
    for rel in FILES_TO_SYNC:
        src = PROJECT_ROOT / rel
        if not src.exists():
            sys.exit(f"Missing local file: {src}")
        subprocess.run(["scp", "-q", *ssh_opts(), str(src), f"{target()}:{REMOTE_DIR}/{rel}"], check=True)
    print(f"Copied {len(FILES_TO_SYNC)} files to ~/{REMOTE_DIR} on WRDS.")


def cmd_submit(args):
    job_args = args.job_args[1:] if args.job_args[:1] == ["--"] else args.job_args
    sync_files()

    # Dropbox settings aren't passed: compute nodes have no internet, so uploads run from the login node.
    qsub = ["qsub", "-v", f"OUT_ROOT={scratch_root()}"]
    if NOTIFY_EMAIL:
        qsub += ["-m", "abe", "-M", NOTIFY_EMAIL]
    qsub += [JOB_SCRIPT, *job_args]

    r = remote(f"cd {shlex.quote(REMOTE_DIR)} && {shlex.join(qsub)}")
    m = re.search(r"Your job (\d+)", r.stdout)
    if not m:
        sys.exit(f"Couldn't find the job ID in the qsub output:\n{r.stdout}{r.stderr}")
    job_id = m.group(1)
    print(f"Submitted job {job_id}.")
    print(f"  status: python {SELF} status")
    print(f"  logs:   python {SELF} logs {job_id}")

    if args.wait:
        wait_for_job(job_id)
        upload(job_id, force=False)


def wait_for_job(job_id: str):
    print(f"Waiting for job {job_id} (checking every {POLL_SECONDS}s). "
          "Ctrl-C stops waiting; the job keeps running.")
    while remote(f"qstat -j {job_id}", check=False).returncode == 0:
        time.sleep(POLL_SECONDS)
    print("Job has left the queue.")


def upload(job_id: str, force: bool):
    if not DROPBOX_DIR:
        sys.exit(f"DROPBOX_DIR is not set in {SELF}.")
    out_dir = job_out_dir(job_id)

    # WRDS says result files can take up to ~2 minutes to appear after a job ends.
    for _ in range(6):
        if remote_test(f"{out_dir}/_SUCCESS"):
            break
        time.sleep(30)
    else:
        sys.exit(f"No _SUCCESS marker in {out_dir}; the job may have failed. "
                 f"Check: python {SELF} logs {job_id}")

    if not force and remote_test(f"{out_dir}/_UPLOADED"):
        print(f"Job {job_id} already uploaded its results to Dropbox:{DROPBOX_DIR} (use --force to upload again).")
        return

    q_out = shlex.quote(out_dir)
    remote(f"rclone copy {q_out} {shlex.quote(dropbox_dest())} --exclude '_*' -P && touch {q_out}/_UPLOADED",
           capture=False)
    print(f"Uploaded results of job {job_id} to Dropbox:{DROPBOX_DIR}")


def cmd_upload(args):
    upload(args.job_id, force=args.force)


def cmd_status(_args):
    r = remote("qstat", check=False)
    print(r.stdout.strip() or "No jobs queued or running.")


def cmd_logs(args):
    log = f"{REMOTE_DIR}/logs/{JOB_NAME}.o{args.job_id}"
    r = remote(f"cat {shlex.quote(log)}", check=False)
    print(r.stdout if r.returncode == 0 else f"No log yet at ~/{log}")


def cmd_disconnect(_args):
    subprocess.run(["ssh", *ssh_opts(), "-O", "exit", target()])


def main():
    p = argparse.ArgumentParser(description="Submit and manage WRDS Cloud batch jobs from your machine.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="one-time checks: python/wrds, ~/.pgpass, Dropbox link").set_defaults(func=cmd_setup)

    s = sub.add_parser("submit", help="copy the code to WRDS and qsub the job")
    s.add_argument("--wait", action="store_true", help="wait for the job to finish, then upload to Dropbox")
    s.add_argument("job_args", nargs=argparse.REMAINDER,
                   help="arguments for nbbo_batch_main.py, after '--'")
    s.set_defaults(func=cmd_submit)

    sub.add_parser("status", help="show your queued and running jobs").set_defaults(func=cmd_status)

    s = sub.add_parser("logs", help="print a job's log")
    s.add_argument("job_id")
    s.set_defaults(func=cmd_logs)

    s = sub.add_parser("upload", help="copy a finished job's output to Dropbox (from the login node)")
    s.add_argument("job_id")
    s.add_argument("--force", action="store_true", help="upload even if the job already uploaded")
    s.set_defaults(func=cmd_upload)

    sub.add_parser("disconnect", help="close the shared SSH connection").set_defaults(func=cmd_disconnect)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
