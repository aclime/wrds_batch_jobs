import io
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
#from itertools import combinations
import pandas as pd
import numpy as np
#from io import StringIO
from datetime import timedelta, date
import pytz
import datetime

#from psycopg2 import errors
from psycopg2.errors import UndefinedTable
from sqlalchemy.exc import ProgrammingError


from wrds_creds import read_pgpass
wrds_username,wrds_password=read_pgpass()
import wrds
db = wrds.Connection(wrds_host="wrds-pgdata.wharton.upenn.edu",
                     wrds_username=wrds_username,
                     wrds_password=wrds_password)


def nbbo_file_query(day:datetime.date) -> pd.DataFrame:

    req = f"""
        SELECT *
        FROM taqm_{int(day.year)}.nbbom_{day.strftime('%Y%m%d')}
        FETCH FIRST 1 ROW ONLY;
        """
    df = db.raw_sql(req, date_cols=['date'])
    return df

def cqm_file_query(day:datetime.date) -> pd.DataFrame:

    req = f"""
        SELECT *
        FROM taqm_{int(day.year)}.cqm_{day.strftime('%Y%m%d')}
        FETCH FIRST 1 ROW ONLY;
        """
    df = db.raw_sql(req, date_cols=['date'])
    return df

def ctm_file_query(day:datetime.date) -> pd.DataFrame:

    req = f"""
        SELECT *
        FROM taqm_{int(day.year)}.ctm_{day.strftime('%Y%m%d')}
        FETCH FIRST 1 ROW ONLY;
        """
    df = db.raw_sql(req, date_cols=['date'])
    return df


def update_nbbo_blacklist(start_date,end_date):

    delta = timedelta(days=1)
    #blacklist_file = "missing_days_ctm.txt" 
    blacklist_file = "blacklist_files/missing_days_nbbom.txt"
    
    with open(blacklist_file, 'a+', encoding='utf-8') as file:
        file.seek(0)
        #blacklist_days = {line.strip() for line in file if line.strip()}
        
        while start_date <= end_date:
            #trade_file_query(start_date)
            #"""
            date_str = start_date.strftime("%Y-%m-%d")

            try:
                _ = nbbo_file_query(start_date)
                #print(start_date)
            
            except ProgrammingError as e:
            #except UndefinedTable as e:
                #print(e)
                #"""
                if "UndefinedTable" in str(e) or "does not exist" in str(e):
                    #print(f"no table for {start_date.strftime('%Y-%m-%d')}")
                    file.write(f"{date_str}\n")
                    file.flush()
                    #blacklist_days.add(date_str)
                    start_date += delta
                    continue
                #"""
            #"""
            start_date += delta

def update_ctm_blacklist(start_date,end_date):

    delta = timedelta(days=1)
    #blacklist_files
    blacklist_file = "blacklist_files/missing_days_ctm.txt"
    
    with open(blacklist_file, 'a+', encoding='utf-8') as file:
        file.seek(0)
        
        while start_date <= end_date:
            date_str = start_date.strftime("%Y-%m-%d")
            try:
                _ = ctm_file_query(start_date)
            
            except ProgrammingError as e:
                if "UndefinedTable" in str(e) or "does not exist" in str(e):
                    #print(f"no table for {start_date.strftime('%Y-%m-%d')}")
                    file.write(f"{date_str}\n")
                    file.flush()
                    #blacklist_days.add(date_str)
                    start_date += delta
                    continue

            start_date += delta

def update_cqm_blacklist(start_date,end_date):

    delta = timedelta(days=1)
    blacklist_file = "blacklist_files/missing_days_cqm.txt"
    
    with open(blacklist_file, 'a+', encoding='utf-8') as file:
        file.seek(0)
        
        while start_date <= end_date:
            date_str = start_date.strftime("%Y-%m-%d")
            try:
                _ = cqm_file_query(start_date)
            
            except ProgrammingError as e:
                if "UndefinedTable" in str(e) or "does not exist" in str(e):
                    #print(f"no table for {start_date.strftime('%Y-%m-%d')}")
                    file.write(f"{date_str}\n")
                    file.flush()
                    #blacklist_days.add(date_str)
                    start_date += delta
                    continue

            start_date += delta



if __name__ == "__main__":
    #start_date = date(2026, 12, 23)
    start_date = date(2015, 1, 1)
    end_date = date(2026, 12, 31)
    print(f'Updating blacklist files for days between {start_date.strftime("%Y-%m-%d")} and {end_date.strftime("%Y-%m-%d")}')
    print('nbbo')
    update_nbbo_blacklist(start_date,end_date)
    print('trades')
    update_ctm_blacklist(start_date,end_date)
    print('quotes')
    update_cqm_blacklist(start_date,end_date)