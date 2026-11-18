"""
Pull the NBBO at the end of each interval for a date range. Meant to run as a
batch job on a WRDS Cloud compute node.

Normally you submit it from your local machine with sample_jobs/submit_wrds_job.py, which
uploads this file and runs it through nbbo_batch_job.sh. You can also run it by
hand on WRDS (for example inside `qrsh`) to test a small range:

    python3 sample_jobs/nbbo_batch_main.py --start 2025-01-02 --end 2025-01-03 --output-dir test_out

The defaults below are used when no command-line arguments are given.
"""
import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Job defaults (override any of these on the command line)
# ---------------------------------------------------------------------------
START_DATE = date(2025, 1, 1)
END_DATE = date(2025, 1, 10)
SYMBOLS = ('IVV', 'SPY')
FREQ = '5m'               # one of the keys in nbbo_quotes_bulk_pull's freq_dict
DAYS_PER_CHUNK = 5        # calendar days per SQL query in bulk_data_pull

FREQ_CHOICES = ['30m', '5m', '1s', '100ms', '10ms', '1ms']

# Repo root: wrds_queries.py, wrds_creds.py and blacklist_files/ live here.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="NBBO end-of-interval bulk pull (WRDS batch job)")
    p.add_argument('--start', type=date.fromisoformat, default=START_DATE,
                   help=f"first date, YYYY-MM-DD (default {START_DATE})")
    p.add_argument('--end', type=date.fromisoformat, default=END_DATE,
                   help=f"last date, YYYY-MM-DD (default {END_DATE})")
    p.add_argument('--symbols', nargs='+', default=list(SYMBOLS),
                   help=f"ticker roots (default {' '.join(SYMBOLS)})")
    p.add_argument('--freq', choices=FREQ_CHOICES, default=FREQ,
                   help=f"interval length (default {FREQ})")
    p.add_argument('--days-per-chunk', type=int, default=DAYS_PER_CHUNK,
                   help=f"calendar days per query (default {DAYS_PER_CHUNK})")
    p.add_argument('--output-dir', type=Path, default=Path('output'),
                   help="where to write the result file (default ./output)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.end < args.start:
        sys.exit(f"--end ({args.end}) is before --start ({args.start})")

    # Resolve the output path before changing directory. nbbo_quotes_bulk_pull
    # opens blacklist_files/... with a relative path, so run from the project root.
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))

    # Import here so `--help` works without opening a WRDS connection
    # (wrds_queries connects as soon as it is imported).
    from wrds_queries import bulk_data_pull, nbbo_quotes_bulk_pull

    symbols = tuple(s.upper() for s in args.symbols)
    print(f"NBBO pull: {args.start} -> {args.end} | freq={args.freq} | "
          f"symbols={symbols} | days_per_chunk={args.days_per_chunk}", flush=True)

    t0 = time.perf_counter()
    df = bulk_data_pull(nbbo_quotes_bulk_pull, args.start, args.end, args.days_per_chunk,
                        symbols=symbols, freq=args.freq)

    out_file = output_dir / f"nbbo_{args.freq}_{args.start:%Y%m%d}_{args.end:%Y%m%d}.csv.gz"
    df.to_csv(out_file, index=False, compression='gzip')

    print(f"Wrote {len(df):,} rows to {out_file} "
          f"({out_file.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
