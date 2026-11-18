import io
import os
import sys
from pathlib import Path

from sqlalchemy import values
sys.path.insert(0, str(Path(__file__).parent.parent))
import csv
import random
from itertools import combinations
import pandas as pd
import numpy as np
#from io import StringIO
from datetime import timedelta, date
import pytz
import datetime

#from psycopg2 import errors
from sqlalchemy.exc import ProgrammingError
import traceback
import time


from wrds_creds import read_pgpass
wrds_username,wrds_password=read_pgpass()
import wrds
db = wrds.Connection(wrds_host="wrds-pgdata.wharton.upenn.edu",
                     wrds_username=wrds_username,
                     wrds_password=wrds_password)



def bulk_data_pull(function,start_date,end_date,days_per_chunk,*args,**kwargs):
    """Run `function(chunk_start, chunk_end, *args, **kwargs)` over consecutive
    calendar-day chunks and concatenate the results. Mirrors
    high_frequency_correlation.helpers.bulk_data_pull; kept as a local copy
    (rather than imported) so this sanity-check script stays self-contained and
    doesn't open a second WRDS connection just to borrow a chunking loop.

    Chunking by whole calendar days (via list-slicing, not start_date + i*step)
    avoids the overlapping-chunk bug that comes from dividing a date range into
    equal-size pieces -- see the call sites this replaces for that history."""

    all_days = []
    d = start_date
    while d <= end_date:
        all_days.append(d)
        d += timedelta(days=1)

    dfs = []
    start_time = time.perf_counter()
    for i in range(0, len(all_days), days_per_chunk):
        chunk = all_days[i:i + days_per_chunk]
        sub_df = function(chunk[0], chunk[-1], *args, **kwargs)
        if sub_df is None or sub_df.empty:
            continue
        dfs.append(sub_df)
    end_time = time.perf_counter()

    if not dfs:
        raise RuntimeError(f"no rows returned between {start_date.strftime('%Y-%m-%d')} "
                           f"and {end_date.strftime('%Y-%m-%d')}")
    print('Bulk df generated')
    print(f'{(end_time-start_time):.2f} seconds')
    return pd.concat(dfs, ignore_index=True)


def nbbo_quotes_bulk_pull(start_date:datetime.date,end_date:datetime.date,symbols:tuple,freq:str,adjust_splits:bool=False) -> pd.DataFrame:

    freq_dict ={
            '30m': "to_timestamp(floor(extract(epoch FROM t.date + t.time_m) / 1800) * 1800)",
            '5m': "to_timestamp(floor(extract(epoch FROM t.date + t.time_m) / 300) * 300)",
            '1s':"date_trunc('second', t.date + t.time_m)",
            '100ms':"to_timestamp(floor(extract(epoch FROM t.date + t.time_m) / 0.100) * 0.100)",
            '10ms':"to_timestamp(floor(extract(epoch FROM t.date + t.time_m) / 0.01) * 0.01)",
            '1ms':"to_timestamp(floor(extract(epoch FROM t.date + t.time_m) / 0.001) * 0.001)",
            }

    delta = timedelta(days=1)
    current_date=start_date
    quote_blocks=[]

    blacklist_file = "blacklist_files/missing_days_nbbom.txt"
    with open(blacklist_file, 'a+', encoding='utf-8') as file:
        file.seek(0)
        blacklist_days = {line.strip() for line in file if line.strip()}
    
        while current_date <= end_date:

            date_str=current_date.strftime("%Y-%m-%d")

            if date_str not in blacklist_days:
                block= f"""SELECT t.sym_root AS sym_root,
                                 t.date AS date,
                                {freq_dict[freq]}::timestamp AS interval_end,
                                    t.date + t.time_m AS ts,
                                    (t.best_bid + t.best_ask) / 2.0 AS midquote,
                                    (t.best_bid*t.best_asksizeshares + t.best_ask*t.best_bidsizeshares ) / (t.best_asksizeshares+t.best_bidsizeshares) AS weighted_midquote,
                                    ROW_NUMBER() OVER (
                                        PARTITION BY
                                        t.sym_root,
                                        {freq_dict[freq]}::timestamp
                                    ORDER BY t.date + t.time_m DESC
                                        ) AS rn
                    FROM taqm_{int(current_date.year)}.complete_nbbo_{current_date.strftime('%Y%m%d')} t
                    WHERE   t.time_m >= TIME '09:30:00' 
                            AND t.time_m <= TIME '16:00:00'
                            AND t.best_bid > 0
                            AND t.best_ask > t.best_bid
                            AND sym_root in ({', '.join(f"'{s}'" for s in symbols)})
                            AND qu_cond in ('R', 'W', 'O', 'A', 'B', 'H', 'Y')
                        """
                quote_blocks.append(block)
            current_date += delta

    if not quote_blocks:
        # every day in this chunk is blacklisted (weekend/holiday); avoid sending empty SQL
        return pd.DataFrame()

    req="WITH combined AS ("
    req+="\nUNION ALL\n".join(quote_blocks)
    req+='\n)'

    req+=f"""
    SELECT *
    FROM combined c
    WHERE rn=1
    ORDER BY c.sym_root, c.interval_end
    """
    
    df = db.raw_sql(req, date_cols=['date'])
    return df