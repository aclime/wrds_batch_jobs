# wrds_batch_jobs

Run Python batch jobs on the **WRDS Cloud** from your own computer, and send the results to **Dropbox**.

The example in [`sample_jobs/`](sample_jobs/) pulls the end-of-interval NBBO from TAQ for a date range. One local command copies the code to WRDS, submits it to Grid Engine with `qsub`, and later uploads the output to Dropbox:

```bash
python sample_jobs/submit_wrds_job.py setup             # one time
python sample_jobs/submit_wrds_job.py submit --wait     # copy code, qsub, wait, upload to Dropbox
```

### Repository layout

| File | Purpose |
|---|---|
| [`sample_jobs/submit_wrds_job.py`](sample_jobs/submit_wrds_job.py) | Runs **on your computer**. Handles SSH, copies files, runs `qsub`, `qstat`, reads logs, uploads to Dropbox |
| [`sample_jobs/nbbo_batch_job.sh`](sample_jobs/nbbo_batch_job.sh) | Grid Engine **wrapper script**: what actually gets `qsub`'d on WRDS |
| [`sample_jobs/nbbo_batch_main.py`](sample_jobs/nbbo_batch_main.py) | The research program. Runs **on a WRDS compute node** |
| [`sample_jobs/SAS/`](sample_jobs/SAS/) | The same job written in SAS, submitted with the same Python script (no local SAS license needed). See its README |
| [`wrds_queries.py`](wrds_queries.py) | Query functions (`bulk_data_pull`, `nbbo_quotes_bulk_pull`, ...). Opens the WRDS database connection on import |
| [`wrds_creds.py`](wrds_creds.py) | Reads your username/password from `~/.pgpass` |
| [`blacklist_files/`](blacklist_files/) | Dates with missing TAQ data, skipped by the queries |
| [`reference_materials/`](reference_materials/) | WRDS documentation PDFs (batch jobs, SSH, TAQ) |

### How the pieces fit together

```
 your Mac                         WRDS login (head) node                WRDS compute node
 ─────────                        ──────────────────────                ─────────────────
 submit_wrds_job.py submit  ──►   ~/wrds_batch_jobs/  (scp'd code)
                                  qsub nbbo_batch_job.sh  ───────────►  python3 nbbo_batch_main.py
                                                                          └─► /scratch/<group>/<user>/nbbo_batch/<JOB_ID>/
 submit_wrds_job.py upload  ──►   rclone copy  scratch ──► Dropbox
```

WRDS compute nodes have **no internet access**, so the Dropbox upload always runs from the login node, never from inside the job.

### Local requirements

* Python 3.9+ (the submit script uses only the standard library)
* `ssh` and `scp` (built into macOS)
* `rclone` (only to link Dropbox once; see [Section 4](#4-linking-your-dropbox-account))
* A WRDS account with Duo two-factor authentication set up

`requirements.txt` lists what the job itself needs (`wrds`, `pandas`, ...). WRDS's `python3` already includes these; `setup` checks that for you. Install them locally only if you also want to run queries on your own machine:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 1. Setting up `.pgpass` and authenticating your WRDS account

Logging in has two separate parts:

1. **SSH login** to the WRDS Cloud (password + Duo push). Used to copy files and run `qsub`.
2. **Database login** to the WRDS PostgreSQL server (`wrds-pgdata.wharton.upenn.edu`). Used by the Python job to run queries. A batch job can't answer a password prompt, so this login reads the credentials from `~/.pgpass`.

### 1a. Create `~/.pgpass` on your computer

`.pgpass` is a one-line text file in the standard PostgreSQL format:

```
hostname:port:database:username:password
```

For WRDS:

```
wrds-pgdata.wharton.upenn.edu:9737:wrds:your_wrds_username:your_wrds_password
```

You can create it by hand:

```bash
echo 'wrds-pgdata.wharton.upenn.edu:9737:wrds:your_wrds_username:your_wrds_password' > ~/.pgpass
chmod 600 ~/.pgpass      # required: PostgreSQL ignores the file if others can read it
```

Or let the `wrds` package make it. Its first connection asks for your credentials and offers to save them:

```python
>>> import wrds
>>> db = wrds.Connection()
Enter your WRDS username [joe]: joe
Enter your password: ********
WRDS recommends setting up a .pgpass file.
Create .pgpass file now [y/n]?: y
Created .pgpass file successfully.
```

> **Note:** [`wrds_creds.read_pgpass()`](wrds_creds.py) uses the **last** non-comment line of `~/.pgpass` as the username and password. If the file has entries for other databases, put the WRDS line last.

The submit script also gets your **SSH username** from this file, so you don't need to set it anywhere else.

### 1b. Copy `.pgpass` to WRDS

The job runs on WRDS, so it needs its own `~/.pgpass` in your WRDS home directory. The `setup` command checks for one and offers to copy your local file (it never overwrites an existing one and sets permissions to `600`):

```
$ python sample_jobs/submit_wrds_job.py setup
Connecting to wrds-cloud.wharton.upenn.edu: enter your WRDS password and approve the Duo prompt.
joe@wrds-cloud.wharton.upenn.edu's password:

[1/3] Checking Python on WRDS...
  python 3.11.x | wrds 3.x.x | pandas 2.x.x

[2/3] Checking ~/.pgpass on WRDS (the job uses it to log in to the database)...
  Not found. Copy your local /Users/joe/.pgpass to WRDS (made readable only by you)? [y/N] y
  copied.

[3/3] Linking Dropbox (rclone remote 'dropbox:' on WRDS)...
```

To do it manually instead, use the same SSH login as in the WRDS docs:

```bash
my-laptop:~ joe$ ssh joe@wrds-cloud.wharton.upenn.edu
joe@wrds-cloud.wharton.upenn.edu's password:
[joe@wrds-cloud-login1-h ~]$ nano ~/.pgpass          # paste the line from 1a
[joe@wrds-cloud-login1-h ~]$ chmod 600 ~/.pgpass
```

### 1c. SSH, Duo, and the shared connection

WRDS needs your password **and** a Duo approval for every new SSH login. To avoid a Duo push for each `scp`/`ssh` call, `submit_wrds_job.py` opens one shared ("master") SSH connection and reuses it:

```python
# sample_jobs/submit_wrds_job.py
CONTROL_PERSIST = "8h"   # how long the shared connection stays open after the last use

def ssh_opts() -> list:
    return ["-o", "ControlMaster=auto",
            "-o", f"ControlPath={CONTROL_PATH}",       # ~/.ssh/wrds-cm-%C
            "-o", f"ControlPersist={CONTROL_PERSIST}",
            "-o", "ServerAliveInterval=60"]
```

In practice you approve Duo once, and every command for the next 8 hours (after the last activity) runs without prompting. To close the connection early:

```bash
python sample_jobs/submit_wrds_job.py disconnect
```

---

## 2. Configuring the `.sh` wrapper and the submit script

### 2a. The wrapper script (`nbbo_batch_job.sh`)

Grid Engine doesn't run your Python file directly. It runs a short **wrapper script** that tells it how to run the job. The simplest Python wrapper, from the WRDS docs, is:

```bash
#!/bin/bash
#$ -cwd
python3 PyProgram.py &> PyProgram.out
```

* `#!/bin/bash`: the shebang, required as the first line
* `#$ -cwd`: run in (and write output to) the directory you submitted from
* the last line runs the program and sends its output to a file

Lines starting with `#$` are Grid Engine options. Lines starting with `##$` are commented out. The wrapper in this repo, [`nbbo_batch_job.sh`](sample_jobs/nbbo_batch_job.sh), builds on that:

```bash
#!/bin/bash
#$ -cwd                # run from the directory qsub was called in (~/wrds_batch_jobs)
#$ -N nbbo_batch       # job name; must match JOB_NAME in submit_wrds_job.py
#$ -j y                # merge stderr into stdout: one log file per job
#$ -o logs/            # write that log to ~/wrds_batch_jobs/logs/
##$ -pe onenode 2      # cores      (remove one '#' to turn on)
##$ -l m_mem_free=8G   # RAM per core

set -uo pipefail
OUT_ROOT="${OUT_ROOT:-/scratch/$(id -gn)/${USER}/nbbo_batch}"
OUT_DIR="${OUT_ROOT}/${JOB_ID}"
mkdir -p "$OUT_DIR"

python3 -u sample_jobs/nbbo_batch_main.py --output-dir "$OUT_DIR" "$@"
status=$?
if (( status != 0 )); then exit "$status"; fi
touch "$OUT_DIR/_SUCCESS"
```

* `"$@"` forwards any arguments given after the script name in `qsub` to `nbbo_batch_main.py`.
* `-u` turns off Python's output buffering so `print()` lines appear in the log as they happen.
* `$JOB_ID` is set by Grid Engine, so each run gets its own output folder.

#### Requesting more CPU / RAM

WRDS batch jobs get **2 cores and 8 GB per core (16 GB)** by default. The hard limit is **8 cores and 48 GB total per job**. RAM is requested *per core*, so these two settings multiply:

| Cores (`-pe onenode`) | RAM per core (`-l m_mem_free`) | Total RAM |
|---|---|---|
| 2 (default) | 8G (default) | 16 GB |
| 2 | 24G | 48 GB (max) |
| 4 | 12G | 48 GB (max) |
| 8 | 6G | 48 GB (max) |

For example, to use 4 cores and 48 GB, change the two lines to:

```bash
#$ -pe onenode 4
#$ -l m_mem_free=12G
```

A job that uses more RAM than it requested is killed automatically. High-frequency pulls (`--freq 1s` or finer) over long date ranges use a lot of memory, so request more RAM or use a smaller `--days-per-chunk`.

#### Email notifications

Grid Engine can email you when the job starts (`b`), ends (`e`), or aborts (`a`). In a wrapper script that looks like:

```bash
#$ -m abe
#$ -M you@school.edu
```

Here you don't need to edit the `.sh`: set `NOTIFY_EMAIL` in `submit_wrds_job.py` (below), and the script adds `-m abe -M <email>` to the `qsub` call.

### 2b. Configuring `submit_wrds_job.py`

All settings are at the top of [`sample_jobs/submit_wrds_job.py`](sample_jobs/submit_wrds_job.py):

```python
WRDS_HOST = "wrds-cloud.wharton.upenn.edu"
REMOTE_DIR = "wrds_batch_jobs"            # under your WRDS home directory
DROPBOX_DIR = "/wrds_batch_jobs/nbbo"     # Dropbox folder for the results
RCLONE_REMOTE = "dropbox"                 # name of the rclone remote on WRDS (created by `setup`)
NOTIFY_EMAIL = "you@school.edu"           # Grid Engine emails; "" or None to turn off
JOB_SCRIPT = "sample_jobs/nbbo_batch_job.sh"   # relative to the repo root
JOB_NAME = "nbbo_batch"                   # must match '#$ -N' in JOB_SCRIPT
SCRATCH_SUBDIR = "nbbo_batch"             # results go to /scratch/<group>/<user>/<this>/<job_id>
CONTROL_PERSIST = "8h"                    # SSH shared-connection lifetime
POLL_SECONDS = 120                        # how often `submit --wait` checks qstat

FILES_TO_SYNC = [                         # copied to ~/REMOTE_DIR before every submit
    "sample_jobs/nbbo_batch_main.py",
    "wrds_queries.py",
    "wrds_creds.py",
    JOB_SCRIPT,
    "blacklist_files/missing_days_nbbom.txt",
]
```

| Setting | Change it when... |
|---|---|
| `NOTIFY_EMAIL` | you want start/end/abort emails, or want them turned off |
| `DROPBOX_DIR` | you want the results in a different Dropbox folder |
| `FILES_TO_SYNC` | your job imports or reads another local file. Anything not listed here won't exist on WRDS |
| `JOB_SCRIPT`, `JOB_NAME`, `SCRATCH_SUBDIR` | you make a new job (see [2d](#2d-adapting-this-for-your-own-job)) |

`submit` copies every file in `FILES_TO_SYNC` on every run, so local edits are always picked up. You don't need to copy anything by hand.

### 2c. Choosing what the job pulls

The job's parameters have defaults at the top of [`nbbo_batch_main.py`](sample_jobs/nbbo_batch_main.py):

```python
START_DATE = date(2025, 1, 1)
END_DATE = date(2025, 1, 10)
SYMBOLS = ('IVV', 'SPY')
FREQ = '5m'               # 30m, 5m, 1s, 100ms, 10ms, 1ms
DAYS_PER_CHUNK = 5        # calendar days per SQL query
```

Edit them there, or override them per run by putting arguments **after `--`**:

```bash
# Use the defaults
python sample_jobs/submit_wrds_job.py submit

# Override the date range, frequency and tickers for this run only
python sample_jobs/submit_wrds_job.py submit -- --start 2025-01-01 --end 2025-03-31 --freq 1s --symbols SPY IVV VOO

# Submit, wait for the job to finish, then upload to Dropbox automatically
python sample_jobs/submit_wrds_job.py submit --wait -- --start 2025-02-01 --end 2025-02-28
```

Behind the scenes `submit` runs this on the WRDS login node:

```bash
cd ~/wrds_batch_jobs && qsub -v OUT_ROOT=/scratch/<group>/<user>/nbbo_batch \
    -m abe -M you@school.edu \
    sample_jobs/nbbo_batch_job.sh --start 2025-01-01 --end 2025-03-31 --freq 1s
```

and prints the job ID:

```
Copied 5 files to ~/wrds_batch_jobs on WRDS.
Submitted job 1234567.
  status: python sample_jobs/submit_wrds_job.py status
  logs:   python sample_jobs/submit_wrds_job.py logs 1234567
```

#### Monitoring

```bash
python sample_jobs/submit_wrds_job.py status          # qstat: your queued/running jobs
python sample_jobs/submit_wrds_job.py logs 1234567    # print the job's log
```

`status` prints the `qstat` table. State `qw` = waiting in the queue, `r` = running. Once the job finishes it disappears from the table:

```
job-ID     prior   name       user  state submit/start at     queue
------------------------------------------------------------------------------------
   1234567 0.55476 nbbo_batch joe   r     01/15/2026 16:57:44 all.q@wrds-sas26-h.wharton.pri
```

#### Testing a small range first

Before submitting a long job, you can run a few days in an interactive session on a compute node (`qrsh`). Don't run jobs on the login node itself; WRDS doesn't allow CPU-heavy work there.

```bash
[joe@wrds-cloud-login1-h ~]$ qrsh
[joe@wrds-sas5-h ~]$ cd ~/wrds_batch_jobs
[joe@wrds-sas5-h wrds_batch_jobs]$ python3 sample_jobs/nbbo_batch_main.py --start 2025-01-02 --end 2025-01-03 --output-dir test_out
```

You can check the arguments locally without connecting to WRDS (`--help` doesn't open a database connection):

```bash
python sample_jobs/nbbo_batch_main.py --help
```

### 2d. Adapting this for your own job

1. Copy `nbbo_batch_main.py` to e.g. `sample_jobs/my_job_main.py`. Keep the `--output-dir` argument and write your results there.
2. Copy `nbbo_batch_job.sh` to `sample_jobs/my_job.sh`. Change `#$ -N`, the default `OUT_ROOT` folder name, and the `python3 ...` line.
3. In `submit_wrds_job.py`, set `JOB_SCRIPT = "sample_jobs/my_job.sh"`, `JOB_NAME` (same as `-N`), `SCRATCH_SUBDIR`, `DROPBOX_DIR`, and add your files to `FILES_TO_SYNC`.

---

## 3. Job output and where it goes

A finished job produces three kinds of output, in three places:

| What | Where | Lifetime |
|---|---|---|
| **Log** (stdout + stderr) | WRDS: `~/wrds_batch_jobs/logs/nbbo_batch.o<JOB_ID>` | Until you delete it (home directory) |
| **Data file** | WRDS scratch: `/scratch/<group>/<user>/nbbo_batch/<JOB_ID>/` | **Deleted automatically after 7 days** |
| **Copy of the data file** | Dropbox: `/wrds_batch_jobs/nbbo/` | Permanent |

### The log

Because of `#$ -j y` and `#$ -o logs/`, each job writes a single log named after the job name and ID. View it with the `logs` command:

```
$ python sample_jobs/submit_wrds_job.py logs 1234567
Job 1234567 starting on wrds-sas26-h at Thu Jan 15 16:57:44 EST 2026
Output directory: /scratch/<group>/joe/nbbo_batch/1234567
NBBO pull: 2025-01-01 -> 2025-01-10 | freq=5m | symbols=('IVV', 'SPY') | days_per_chunk=5
Wrote 1,234 rows to /scratch/<group>/joe/nbbo_batch/1234567/nbbo_5m_20250101_20250110.csv.gz (0.1 MB) in 42s
To send results to Dropbox: python sample_jobs/submit_wrds_job.py upload 1234567
Job 1234567 finished at Thu Jan 15 16:58:31 EST 2026
```

If the job fails, the Python traceback and `nbbo_batch_main.py failed with exit code ...` appear here.

### The data file

`nbbo_batch_main.py` writes one gzip-compressed CSV named after the frequency and date range:

```
/scratch/<group>/<user>/nbbo_batch/<JOB_ID>/
├── nbbo_5m_20250101_20250110.csv.gz    # the results
├── _SUCCESS                            # the Python job finished without errors
└── _UPLOADED                           # the results were copied to Dropbox
```

The `_SUCCESS` and `_UPLOADED` files are empty markers. `upload` refuses to run without `_SUCCESS` (so a failed job is never uploaded), and skips jobs that already have `_UPLOADED` unless you pass `--force`.

Output goes to **scratch** rather than your home directory because scratch has much more space. It is temporary, so upload (or download) your results within 7 days.

> WRDS notes that result files can take up to about 2 minutes to appear after `qstat` shows the job is finished. `upload` waits and retries for up to 3 minutes.

### Sending the output to Dropbox

Either let `submit` do it:

```bash
python sample_jobs/submit_wrds_job.py submit --wait
```

`--wait` checks `qstat` every `POLL_SECONDS` until the job leaves the queue, then uploads. Ctrl-C stops the waiting, but the job keeps running on WRDS.

Or upload later by job ID (for example after getting the "job ended" email):

```bash
python sample_jobs/submit_wrds_job.py upload 1234567
python sample_jobs/submit_wrds_job.py upload 1234567 --force    # upload again
```

The upload runs this on the login node (marker files are skipped with `--exclude '_*'`):

```bash
rclone copy /scratch/<group>/<user>/nbbo_batch/1234567 dropbox:wrds_batch_jobs/nbbo --exclude '_*' -P \
  && touch /scratch/<group>/<user>/nbbo_batch/1234567/_UPLOADED
```

All jobs upload into the same Dropbox folder. Different date ranges or frequencies get different filenames, but **re-running the same arguments overwrites the earlier file in Dropbox**.

### Downloading directly instead of using Dropbox

```bash
scp joe@wrds-cloud.wharton.upenn.edu:/scratch/<group>/joe/nbbo_batch/1234567/*.csv.gz .
```

---

## 4. Linking your Dropbox account

Uploads use [rclone](https://rclone.org), a command-line tool that copies files to cloud storage. rclone runs on the **WRDS login node** and needs a Dropbox access token. Getting the token requires a web browser, which WRDS doesn't have, so you authorize Dropbox **on your computer** and the token is copied to WRDS.

### 4a. Automatic (recommended)

```bash
brew install rclone                              # local, one time
python sample_jobs/submit_wrds_job.py setup
```

Step 3 of `setup`:

1. runs `rclone authorize dropbox` locally, which opens your browser. Sign in to Dropbox and click **Allow**;
2. copies the token to WRDS through stdin (so it never shows up in the process list of the shared login node) and adds it to your rclone config there, readable only by you;
3. creates `DROPBOX_DIR` and checks the token works.

```
[3/3] Linking Dropbox (rclone remote 'dropbox:' on WRDS)...
  Opening your browser: sign in to Dropbox and click Allow...
  token saved on WRDS.
  OK, results will go to Dropbox folder /wrds_batch_jobs/nbbo
```

The relevant code, if you want to reuse it elsewhere:

```python
# Get the token locally (opens a browser)
r = subprocess.run(["rclone", "authorize", "dropbox"], stdout=subprocess.PIPE, text=True)
token = re.search(r"--->\s*(\{.*?\})\s*<---", r.stdout, re.DOTALL).group(1)

# Append it to the rclone config on WRDS, readable only by you
remote("""
umask 077
conf="$(rclone config file | tail -1)"
{ printf '\\n[dropbox]\\ntype = dropbox\\ntoken = '; cat; printf '\\n'; } >> "$conf"
chmod 600 "$conf"
""", input_text=token)
```

### 4b. Manual

If you'd rather do it by hand, use rclone's built-in "remote setup" flow.

**On your computer**, get a token:

```bash
$ rclone authorize "dropbox"
# a browser opens -> sign in -> Allow
Paste the following into your remote machine --->
{"access_token":"sl.XXXX","token_type":"bearer","refresh_token":"XXXX","expiry":"2026-01-15T18:00:00Z"}
<---End paste
```

**On WRDS**, create the remote and paste the token when asked:

```bash
[joe@wrds-cloud-login1-h ~]$ rclone config
n) New remote
name> dropbox
Storage> dropbox
client_id>                      # leave blank
client_secret>                  # leave blank
Edit advanced config? n
Use web browser to automatically authenticate rclone with remote? n
config_token> {"access_token":"sl.XXXX", ...}     # paste the JSON from your computer
Keep this "dropbox" remote? y
q) Quit config
```

The name you choose (`dropbox` here) must match `RCLONE_REMOTE` in `submit_wrds_job.py`.

### 4c. Checking the link

On WRDS (or via `ssh joe@wrds-cloud.wharton.upenn.edu '<command>'`):

```bash
rclone listremotes                                 # should print: dropbox:
rclone lsd dropbox:                                # list top-level Dropbox folders
rclone mkdir dropbox:wrds_batch_jobs/nbbo          # create the results folder
rclone ls  dropbox:wrds_batch_jobs/nbbo            # list uploaded files
rclone copy ~/some_file.csv dropbox:wrds_batch_jobs/nbbo -P    # manual upload with progress
```

### Troubleshooting

* **`rclone: command not found` on WRDS**: install it in your home directory and make sure `~/bin` is on your `PATH`:

  ```bash
  curl -O https://downloads.rclone.org/rclone-current-linux-amd64.zip
  unzip rclone-current-linux-amd64.zip
  mkdir -p ~/bin && cp rclone-*-linux-amd64/rclone ~/bin/
  echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bash_profile
  ```

* **`a [dropbox] section already exists`**: `setup` won't overwrite an existing remote. Run `rclone config` on WRDS and delete or rename the old `dropbox` remote, then run `setup` again.
* **Token expired / `invalid_access_token`**: run `rclone config reconnect dropbox:` on WRDS and repeat 4b with a new token from `rclone authorize "dropbox"`.
* **Uploads from inside the job fail with "network is unreachable"**: expected. Compute nodes have no internet; upload from the login node with `submit_wrds_job.py upload` as described above.

---

## 5. Windows users

This example was built and tested on macOS. WRDS itself is Linux, so the `.sh` wrapper, `nbbo_batch_main.py` and the rclone commands are the same for everyone. What differs is **your computer's side**: `submit_wrds_job.py`, SSH, and `.pgpass`.

### Option A (recommended): WSL

[Windows Subsystem for Linux](https://learn.microsoft.com/windows/wsl/install) gives you a real Linux shell, and every instruction in this README works there unchanged.

```powershell
# PowerShell (as Administrator), one time; restart afterwards
wsl --install
```

Then, in the Ubuntu terminal:

```bash
sudo apt update && sudo apt install -y python3 python3-venv git rclone openssh-client
git clone <this repo> && cd wrds_batch_jobs

# create ~/.pgpass inside WSL (WSL has its own home directory, separate from C:\Users\<you>)
echo 'wrds-pgdata.wharton.upenn.edu:9737:wrds:your_wrds_username:your_wrds_password' > ~/.pgpass
chmod 600 ~/.pgpass

python3 sample_jobs/submit_wrds_job.py setup
python3 sample_jobs/submit_wrds_job.py submit --wait
```

If `rclone authorize dropbox` can't open a browser from WSL, it prints a `http://127.0.0.1:53682/auth?...` link. Open it in your Windows browser.

### Option B: native Windows (PowerShell)

The script mostly works, but with these differences:

| Topic | macOS | Windows |
|---|---|---|
| **Shared SSH connection** | `ControlMaster` works: one Duo approval | Windows' built-in OpenSSH **doesn't support `ControlMaster`** (or `ssh -f`). Expect `ensure_connected()` to fail, or a password + Duo prompt for **every** `ssh`/`scp` call (`submit` makes about 10). Use WSL, or the manual workflow below |
| **Python command** | `python3` | `py` or `python` |
| **Installing rclone** | `brew install rclone` | `winget install Rclone.Rclone` (or `scoop install rclone` / `choco install rclone`) |
| **`.pgpass` location** | `~/.pgpass` | This repo reads `~/.pgpass` = `C:\Users\<you>\.pgpass`. The `wrds` package and PostgreSQL on Windows use `%APPDATA%\postgresql\pgpass.conf` instead. Create **both** with the same line |
| **File permissions** | `chmod 600 ~/.pgpass` | `chmod` doesn't exist; Windows user-profile permissions apply. On WRDS, `setup` still sets `600` |
| **Line endings** | LF | Editors and git may save files with CRLF. A `.sh` with CRLF fails on WRDS with `/bin/bash^M: bad interpreter`. See below |

Creating both `.pgpass` files in PowerShell:

```powershell
$line = "wrds-pgdata.wharton.upenn.edu:9737:wrds:your_wrds_username:your_wrds_password"
Set-Content -Path "$HOME\.pgpass" -Value $line -NoNewline
New-Item -ItemType Directory -Force "$env:APPDATA\postgresql" | Out-Null
Set-Content -Path "$env:APPDATA\postgresql\pgpass.conf" -Value $line -NoNewline
```

#### Line endings

Make git keep LF line endings for shell scripts by adding a `.gitattributes` file at the repo root:

```gitattributes
*.sh text eol=lf
```

If a `.sh` file already has CRLF endings, fix it on WRDS:

```bash
sed -i 's/\r$//' ~/wrds_batch_jobs/sample_jobs/nbbo_batch_job.sh
```

In VS Code you can also click `CRLF` in the bottom status bar and switch it to `LF` before saving.

#### Manual workflow (no `submit_wrds_job.py`)

Without `ControlMaster`, it's often easier to do the steps by hand in one SSH session (one Duo approval). Connect with PowerShell's `ssh`, or with PuTTY / MobaXterm (host `wrds-cloud.wharton.upenn.edu`, port `22`). Copy files with `scp` or WinSCP.

```powershell
# 1. From PowerShell, in the repo folder: copy the code to WRDS
ssh joe@wrds-cloud.wharton.upenn.edu "mkdir -p wrds_batch_jobs/sample_jobs wrds_batch_jobs/blacklist_files wrds_batch_jobs/logs"
scp sample_jobs\nbbo_batch_main.py sample_jobs\nbbo_batch_job.sh joe@wrds-cloud.wharton.upenn.edu:wrds_batch_jobs/sample_jobs/
scp wrds_queries.py wrds_creds.py joe@wrds-cloud.wharton.upenn.edu:wrds_batch_jobs/
scp blacklist_files\missing_days_nbbom.txt joe@wrds-cloud.wharton.upenn.edu:wrds_batch_jobs/blacklist_files/
```

```bash
# 2. Log in and submit
my-laptop> ssh joe@wrds-cloud.wharton.upenn.edu
[joe@wrds-cloud-login1-h ~]$ cd ~/wrds_batch_jobs
[joe@wrds-cloud-login1-h wrds_batch_jobs]$ qsub -m abe -M you@school.edu sample_jobs/nbbo_batch_job.sh --start 2025-01-01 --end 2025-01-31
Your job 1234567 ("nbbo_batch") has been submitted

# 3. Monitor
[joe@wrds-cloud-login1-h wrds_batch_jobs]$ qstat
[joe@wrds-cloud-login1-h wrds_batch_jobs]$ cat logs/nbbo_batch.o1234567

# 4. When qstat is empty and _SUCCESS exists, upload to Dropbox from the login node
[joe@wrds-cloud-login1-h wrds_batch_jobs]$ OUT=/scratch/$(id -gn)/$USER/nbbo_batch/1234567
[joe@wrds-cloud-login1-h wrds_batch_jobs]$ ls $OUT
_SUCCESS  nbbo_5m_20250101_20250131.csv.gz
[joe@wrds-cloud-login1-h wrds_batch_jobs]$ rclone copy $OUT dropbox:wrds_batch_jobs/nbbo --exclude '_*' -P
```

Linking Dropbox ([Section 4b](#4b-manual)) works the same way: run `rclone authorize "dropbox"` in PowerShell, then paste the token into `rclone config` on WRDS.

---

## 6. The SAS version

[`sample_jobs/SAS/`](sample_jobs/SAS/) has the same NBBO job written in SAS. **You don't need SAS on your computer.** SAS runs on WRDS, which licenses it for every account. Your computer only runs [`submit_sas_job.py`](sample_jobs/SAS/submit_sas_job.py), which reuses `submit_wrds_job.py` to copy the `.sas` file, run `qsub`, and upload the result to Dropbox. The commands are the same as for the Python job.

| | Python job | SAS job |
|---|---|---|
| Program | `sample_jobs/nbbo_batch_main.py` | [`sample_jobs/SAS/nbbo_batch_main.sas`](sample_jobs/SAS/nbbo_batch_main.sas) |
| Reads the data from | WRDS PostgreSQL server (PostgreSQL SQL, `~/.pgpass`) | WRDS's SAS datasets (`taqmsec.complete_nbbo_YYYYMMDD`, SAS code, no `.pgpass` on WRDS) |
| Submit from your computer | `python sample_jobs/submit_wrds_job.py ...` | `python sample_jobs/SAS/submit_sas_job.py ...` |
| Job arguments after `--` | `--start 2025-01-01 --end 2025-01-31 --freq 1s` | `start=2025-01-01 end=2025-01-31 freq=1s symbols=SPY,IVV` |
| Log | `logs/nbbo_batch.o<JOB_ID>` | `logs/nbbo_sas.o<JOB_ID>` (includes the SAS log) |
| Results | `/scratch/<group>/<user>/nbbo_batch/<JOB_ID>/` | `/scratch/<group>/<user>/nbbo_sas/<JOB_ID>/` |
| Dropbox folder | `/wrds_batch_jobs/nbbo` | `/wrds_batch_jobs/nbbo_sas` |

### 6a. Running it

Run these **on your computer**, from the repo folder, not in an SSH session on WRDS:

```bash
python sample_jobs/SAS/submit_sas_job.py setup          # one time: checks sas/qsas on WRDS, links Dropbox
python sample_jobs/SAS/submit_sas_job.py submit --wait -- start=2025-01-02 end=2025-01-03
```

Output from a successful run:

```
Copied 3 files to ~/wrds_batch_jobs on WRDS.
Submitted job 40729612.
Waiting for job 40729612 (checking every 120s). Ctrl-C stops waiting; the job keeps running.
Job has left the queue.
Uploaded results of job 40729612 to Dropbox:/wrds_batch_jobs/nbbo_sas
```

`status`, `logs JOB_ID`, `upload JOB_ID` and `disconnect` work the same as in [Section 2c](#2c-choosing-what-the-job-pulls) and [Section 3](#3-job-output-and-where-it-goes).

### 6b. Running SAS by hand on WRDS (`qsas`)

`qsas` is WRDS's shortcut for submitting a `.sas` file without a wrapper script. It exists **only on WRDS**, so copy the file there first, then log in:

```bash
# on your computer
ssh you@wrds-cloud.wharton.upenn.edu "mkdir -p wrds_batch_jobs/sample_jobs/SAS"
scp sample_jobs/SAS/check_taqmsec.sas you@wrds-cloud.wharton.upenn.edu:wrds_batch_jobs/sample_jobs/SAS/
ssh you@wrds-cloud.wharton.upenn.edu
```

```bash
# on WRDS
[you@wrds-cloud-login1-h ~]$ cd ~/wrds_batch_jobs
[you@wrds-cloud-login1-h wrds_batch_jobs]$ qsas sample_jobs/SAS/check_taqmsec.sas
Your job 40729561 ("check_taqmsec.sas") has been submitted
[you@wrds-cloud-login1-h wrds_batch_jobs]$ qstat        # qw = waiting, r = running; empty = finished
[you@wrds-cloud-login1-h wrds_batch_jobs]$ ls check_taqmsec.*
check_taqmsec.log  check_taqmsec.lst
```

`qsas` writes `<program>.log` and `<program>.lst` to the directory you ran it from, a minute or two after `qstat` comes back empty. Delete or rename them before running the same program again. [`check_taqmsec.sas`](sample_jobs/SAS/check_taqmsec.sas) is a good first program: it lists the TAQ datasets and their variables, so you can confirm the library name before the real job.

`qsas` takes no arguments or Grid Engine options (no email, no `-pe`/`m_mem_free`), so for the real job use the wrapper through `submit_sas_job.py`.

See [`sample_jobs/SAS/README.md`](sample_jobs/SAS/README.md) for what can and can't be done in SAS, how SAS's SQL differs from PostgreSQL, and how the output compares with the Python job.

---

## References

* [`reference_materials/Batch Jobs on the WRDS Cloud.pdf`](reference_materials/): wrapper scripts, `qsub`, resource limits
* [`reference_materials/Using SSH to Connect to the WRDS Cloud.pdf`](reference_materials/): SSH clients for macOS and Windows
* [`reference_materials/WRDS Overview of TAQ.pdf`](reference_materials/): the TAQ data used by the example
* [rclone Dropbox docs](https://rclone.org/dropbox/) and [rclone remote setup](https://rclone.org/remote_setup/)
