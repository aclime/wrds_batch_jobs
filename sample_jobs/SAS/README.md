# SAS version of the NBBO batch job

This folder does the same thing as the Python example in [`sample_jobs/`](../): it pulls the last NBBO quote in each interval from TAQ millisecond for a date range, writes a `.csv.gz` to WRDS scratch, and uploads it to Dropbox. The query itself is written in SAS.

**You don't need a SAS license on your computer.** SAS only runs on WRDS, which licenses it for every account. Your computer only uses SSH (through the same Python submit script as the Python job) to copy the `.sas` file, run `qsub`, and start the upload.

| File | Runs where | Purpose |
|---|---|---|
| [`nbbo_batch_main.sas`](nbbo_batch_main.sas) | WRDS compute node | The research program, in SAS |
| [`nbbo_batch_job.sh`](nbbo_batch_job.sh) | WRDS compute node | Grid Engine wrapper: sets `OUT_DIR`, runs `sas`, writes `_SUCCESS` |
| [`check_taqmsec.sas`](check_taqmsec.sas) | WRDS compute node | One-time check of the TAQ library and its variable names |
| [`submit_sas_job.py`](submit_sas_job.py) | your computer | Imports `../submit_wrds_job.py` and only changes the file list, job name and folders |

## What SAS can and can't do here

| Step | Python version | SAS version | Can it be done in SAS? |
|---|---|---|---|
| Query TAQ for each day | `wrds_queries.nbbo_quotes_bulk_pull` (SQL sent to PostgreSQL) | DATA step reading `taqmsec.complete_nbbo_YYYYMMDD` | **Yes** |
| Last quote per interval | `ROW_NUMBER() OVER (...)` | `PROC SORT` + `BY` group with `last.` | **Yes** (PROC SQL has no window functions, so it's a DATA step) |
| Skip missing days | `blacklist_files/missing_days_nbbom.txt` | `exist()` checks whether the day's dataset is there | **Yes**, no blacklist needed |
| Command-line arguments | `argparse` | `-sysparm "start=... end=..."` | **Yes** |
| Write `.csv.gz` | `pandas.to_csv(compression='gzip')` | `FILENAME ... ZIP GZIP` | **Yes** (SAS 9.4M5+) |
| Grid Engine wrapper | `nbbo_batch_job.sh` | `nbbo_batch_job.sh`, or skip it with `qsas` | Shell either way. `qsas` replaces the wrapper but takes no options |
| SSH, copy files, `qsub`, wait (from your computer) | `submit_wrds_job.py` | same, via `submit_sas_job.py` | **No.** Local SAS (with SAS/CONNECT) would need a local license |
| Upload to Dropbox | `rclone` on the login node | same | **No, in practice.** Compute nodes have no internet, and WRDS doesn't allow you to run programs on the login node. `PROC HTTP` could call the Dropbox API, but only from a machine with internet access |

So the research part (everything in `nbbo_batch_main.py` plus `wrds_queries.py`) moves to SAS. The work around the job (SSH, `qsub`, Dropbox) stays in Python and shell, and it's the same code as the Python job.

## Is WRDS SAS Oracle SQL?

No. The two languages read **different copies** of the WRDS data:

| | Python (`wrds` package) | SAS on the WRDS Cloud |
|---|---|---|
| Where the data lives | PostgreSQL server `wrds-pgdata.wharton.upenn.edu:9737`, database `wrds` (see [`wrds_queries.py`](../../wrds_queries.py)) | SAS datasets (`.sas7bdat` files) on the WRDS file system |
| How you name a table | schema.table: `taqm_2025.complete_nbbo_20250102` | libref.dataset: `taqmsec.complete_nbbo_20250102` |
| SQL dialect | PostgreSQL (`::timestamp`, `extract(epoch ...)`, `date_trunc`, window functions) | **SAS's own PROC SQL** (close to ANSI SQL; no window functions, no `::` casts; dates and times are numbers) |
| Login | `~/.pgpass` | none: you're already logged in to WRDS |

Oracle isn't involved in either one. The queries in `wrds_queries.py` won't run unchanged in SAS: casts, `extract(epoch ...)` and `ROW_NUMBER()` all need to be rewritten, which is why `nbbo_batch_main.sas` uses a DATA step for those parts.

SAS can also send PostgreSQL SQL to the WRDS PostgreSQL server unchanged, using `LIBNAME ... postgres` or a `PROC SQL` `CONNECT TO postgres` pass-through block. That only works if WRDS's SAS includes SAS/ACCESS to PostgreSQL, which I haven't confirmed. The native SAS datasets are the usual way to work in SAS on WRDS.

## Running it

### 1. One-time setup

```bash
python sample_jobs/SAS/submit_sas_job.py setup
```

This checks that `sas` and `qsas` exist on WRDS and links Dropbox (it reuses the `dropbox:` rclone remote if you already set it up for the Python job). The SSH username still comes from your local `~/.pgpass`, the same as in the Python job, but SAS on WRDS doesn't need a `.pgpass`.

### 2. Check the library (first time only)

`nbbo_batch_main.sas` assumes the libref `taqmsec` and the variable names used in `wrds_queries.py` (`sym_root`, `date`, `time_m`, `best_bid`, `best_ask`, `best_bidsizeshares`, `best_asksizeshares`, `qu_cond`). Check them once:

```bash
# on your computer, from the repo folder (qsas only exists on WRDS)
ssh <you>@wrds-cloud.wharton.upenn.edu "mkdir -p wrds_batch_jobs/sample_jobs/SAS"
scp sample_jobs/SAS/check_taqmsec.sas <you>@wrds-cloud.wharton.upenn.edu:wrds_batch_jobs/sample_jobs/SAS/
ssh <you>@wrds-cloud.wharton.upenn.edu
```

```bash
# on WRDS
cd ~/wrds_batch_jobs
qsas sample_jobs/SAS/check_taqmsec.sas
qstat                     # repeat until it prints nothing, then wait a minute
less check_taqmsec.lst    # written to the folder you ran qsas from
```

In the listing you should see `COMPLETE_NBBO_2025...` datasets stored in `/wrds/nyse/sasdata/wrds_taqms_nbbo/` and the eight variables above (SAS ignores case, so `Best_BidSizeShares` matches `best_bidsizeshares`). Days with no dataset, such as weekends, holidays and the 2025-01-09 market closure, are skipped automatically.

If the library has a different name for you, change `%let taqlib = taqmsec;` at the top of `nbbo_batch_main.sas`.

### 3. Submit

Run these **on your computer**, from the repo folder (log out of WRDS first if you're in an SSH session). Arguments go after `--` as `key=value` pairs (not the `--start` flags used by the Python job):

```bash
python sample_jobs/SAS/submit_sas_job.py submit                       # defaults at the top of the .sas file
python sample_jobs/SAS/submit_sas_job.py submit --wait                # wait, then upload to Dropbox
python sample_jobs/SAS/submit_sas_job.py submit -- start=2025-01-01 end=2025-03-31 freq=1s symbols=SPY,IVV,VOO
```

Separate tickers with commas and no spaces. `status`, `logs JOB_ID`, `upload JOB_ID [--force]` and `disconnect` work the same as for the Python job.

| What | Where |
|---|---|
| Grid Engine log (includes a copy of the SAS log) | `~/wrds_batch_jobs/logs/nbbo_sas.o<JOB_ID>` |
| SAS log / listing | `~/wrds_batch_jobs/logs/nbbo_sas_<JOB_ID>.log` / `.lst` |
| Data | `/scratch/<group>/<user>/nbbo_sas/<JOB_ID>/nbbo_<freq>_<start>_<end>.csv.gz` |
| Dropbox | `/wrds_batch_jobs/nbbo_sas/` |

SAS exits with code 0 (OK), 1 (warnings) or 2 or higher (errors). The wrapper writes `_SUCCESS` for 0 and 1, so `upload` refuses to send the output of a failed job.

### Alternative: `qsas` (no wrapper, no Python)

The WRDS batch-jobs guide ([`reference_materials/Batch Jobs on the WRDS Cloud.pdf`](../../reference_materials/)) describes `qsas`, which submits a `.sas` file without a wrapper:

```bash
[you@wrds-cloud-login1-h ~]$ cd ~/wrds_batch_jobs
[you@wrds-cloud-login1-h wrds_batch_jobs]$ qsas sample_jobs/SAS/nbbo_batch_main.sas
Your job 1234567 ("nbbo_batch_main.sas") has been submitted
```

With `qsas`:

* you can't pass arguments, so edit the `%let` defaults at the top of the file first;
* there's no `OUT_DIR`, so the CSV goes to `~/wrds_batch_jobs/output_sas/` (your home directory, which has much less space than scratch);
* the SAS log and listing appear as `nbbo_batch_main.log` / `.lst` in the directory you ran it from. Rename or delete them before running again;
* no `_SUCCESS` marker and no email notification. Upload by hand from the login node:

```bash
rclone copy ~/wrds_batch_jobs/output_sas dropbox:wrds_batch_jobs/nbbo_sas -P
```

`qsas` is fine for a quick run. The wrapper is better for anything you'll run repeatedly.

### Testing interactively

```bash
[you@wrds-cloud-login1-h ~]$ qrsh
[you@wrds-sas5-h ~]$ cd ~/wrds_batch_jobs
[you@wrds-sas5-h wrds_batch_jobs]$ sas -nodms -sysparm "start=2025-01-02 end=2025-01-03" sample_jobs/SAS/nbbo_batch_main.sas
[you@wrds-sas5-h wrds_batch_jobs]$ less nbbo_batch_main.log
```

## Differences from the Python output

The columns (`sym_root,date,interval_end,ts,midquote,weighted_midquote,rn`) and the rows should match the Python job's output. The formatting differs slightly:

* Timestamps are ISO 8601 with a `T` (`2025-01-02T09:35:00.000`). pandas writes a space.
* Numbers are written with up to 16 significant digits.
* When both quote sizes are 0, `weighted_midquote` is left empty. PostgreSQL would raise a division-by-zero error instead.
* Like the Python query, a single interval's last quote is taken across all `sym_suffix` values of a `sym_root`.
