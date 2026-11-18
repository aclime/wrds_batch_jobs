/*****************************************************************************
 nbbo_batch_main.sas

 SAS version of sample_jobs/nbbo_batch_main.py: the NBBO at the end of each
 interval, for a date range and a list of tickers, from TAQ millisecond
 (complete_nbbo_YYYYMMDD). Meant to run on a WRDS Cloud compute node.

 Three ways to run it on WRDS (all from ~/wrds_batch_jobs):

   1. Through the wrapper (what submit_sas_job.py does):
        qsub sample_jobs/SAS/nbbo_batch_job.sh start=2025-01-01 end=2025-01-31 freq=1s
   2. With qsas (no wrapper, no arguments: uses the defaults below):
        qsas sample_jobs/SAS/nbbo_batch_main.sas
   3. Interactively on a compute node, to test a small range:
        qrsh
        sas -nodms -sysparm "start=2025-01-02 end=2025-01-03" sample_jobs/SAS/nbbo_batch_main.sas

 Arguments arrive in -sysparm as space-separated key=value pairs:
   start=YYYY-MM-DD  end=YYYY-MM-DD  freq=5m  symbols=IVV,SPY
 Output folder: the OUT_DIR environment variable (set by the wrapper), else
 ./output_sas under the directory SAS was started from.

 Differences from the Python version
   * Data source: WRDS's native SAS datasets (libref TAQMSEC), not the
     PostgreSQL server. No ~/.pgpass needed.
   * SAS PROC SQL has no window functions (no ROW_NUMBER() OVER ...), so the
     "last quote per interval" step is a sort + DATA step BY group instead.
   * Missing days (weekends, holidays) are skipped by checking that the
     day's dataset exists, so the blacklist file isn't needed.
   * Timestamps are written as ISO 8601 (2025-01-02T09:35:00.000).
*****************************************************************************/

options nonotes nosource2 mprint;   /* quieter log: one line per day instead of a page */

/* ---------------------------------------------------------------------------
   Job defaults (override with -sysparm)
   ------------------------------------------------------------------------- */
%let start   = 2025-01-01;
%let end     = 2025-01-10;
%let symbols = IVV,SPY;
%let freq    = 5m;              /* 30m, 5m, 1s, 100ms, 10ms, 1ms */
%let taqlib  = taqmsec;         /* WRDS libref for TAQ millisecond */


/* ---------------------------------------------------------------------------
   Parse -sysparm "key=value key=value ..."
   ------------------------------------------------------------------------- */
%macro parse_sysparm;
    %local i pair key val;
    %let i = 1;
    /* %qscan, not %scan: symbols=IVV,SPY contains a comma */
    %let pair = %qscan(%superq(sysparm), &i, %str( ));
    %do %while(%length(&pair) > 0);
        %let key = %lowcase(%qscan(&pair, 1, =));
        %let val = %qscan(&pair, 2, =);
        %if %sysfunc(indexw(start end symbols freq, &key)) %then %let &key = %unquote(&val);
        %else %put WARNING: ignoring unknown argument "&pair" (expected start= end= symbols= freq=);
        %let i = %eval(&i + 1);
        %let pair = %qscan(%superq(sysparm), &i, %str( ));
    %end;
%mend parse_sysparm;
%parse_sysparm


/* ---------------------------------------------------------------------------
   Validate arguments and set up the output folder
   ------------------------------------------------------------------------- */
%macro fail(msg, code=3);
    %put ERROR: &msg;
    data _null_; abort return &code; run;   /* non-zero exit so the wrapper skips _SUCCESS */
%mend fail;

%macro setup;
    %global start_d end_d secs sym_list out_dir out_file;

    %let start_d = %sysfunc(inputn(&start, yymmdd10.));
    %let end_d   = %sysfunc(inputn(&end,   yymmdd10.));
    %if &start_d = . or &end_d = . %then %fail(start/end must be YYYY-MM-DD (got start=&start end=&end));
    %if &end_d < &start_d %then %fail(end (&end) is before start (&start));

    /* interval length in seconds; the same buckets as freq_dict in wrds_queries.py */
    %let freq = %lowcase(&freq);
    %if      &freq = 30m   %then %let secs = 1800;
    %else %if &freq = 5m    %then %let secs = 300;
    %else %if &freq = 1s    %then %let secs = 1;
    %else %if &freq = 100ms %then %let secs = 0.1;
    %else %if &freq = 10ms  %then %let secs = 0.01;
    %else %if &freq = 1ms   %then %let secs = 0.001;
    %else %fail(freq must be one of 30m 5m 1s 100ms 10ms 1ms (got &freq));

    /* IVV,SPY  ->  "IVV" "SPY" for a WHERE ... IN () list */
    data _null_;
        length s $2000;
        s = upcase(compress(symget('symbols')));
        call symputx('sym_list', cats('"', tranwrd(s, ',', '" "'), '"'), 'G');
    run;

    %if %sysfunc(envlen(OUT_DIR)) > 0 %then %let out_dir = %sysget(OUT_DIR);
    %else %let out_dir = %sysget(PWD)/output_sas;
    options dlcreatedir;
    libname _mkdir "&out_dir";
    libname _mkdir clear;

    %let out_file = &out_dir/nbbo_&freq._%sysfunc(compress(&start, -))_%sysfunc(compress(&end, -)).csv.gz;

    %put NOTE: NBBO pull: &start -> &end | freq=&freq | symbols=&sym_list | lib=&taqlib;
    %put NOTE: Output: &out_file;
%mend setup;
%setup

%let t0 = %sysfunc(datetime());


/* ---------------------------------------------------------------------------
   One day: filter quotes, bucket into intervals, keep the last quote per
   (symbol, interval). Mirrors the SQL in wrds_queries.nbbo_quotes_bulk_pull.
   ------------------------------------------------------------------------- */
%macro pull_day(d);
    %local ymd ds;
    %let ymd = %sysfunc(putn(&d, yymmddn8.));
    %let ds  = &taqlib..complete_nbbo_&ymd;

    %if not %sysfunc(exist(&ds)) %then %return;   /* weekend, holiday, or not loaded yet */

    data _day;
        set &ds(keep=sym_root date time_m best_bid best_ask best_bidsizeshares
                      best_asksizeshares qu_cond
                where=('09:30:00't <= time_m <= '16:00:00't
                       and best_bid > 0
                       and best_ask > best_bid
                       and sym_root in (&sym_list)
                       and qu_cond in ('R' 'W' 'O' 'A' 'B' 'H' 'Y')));
        ts = dhms(date, 0, 0, 0) + time_m;
        /* floor(epoch / secs) * secs, rounded first so 0.1-style steps don't drift */
        interval_end = dhms(date, 0, 0, 0) + floor(round(time_m / &secs, 1e-6)) * &secs;
        midquote = (best_bid + best_ask) / 2;
        if best_asksizeshares + best_bidsizeshares > 0 then
            weighted_midquote = (best_bid * best_asksizeshares + best_ask * best_bidsizeshares)
                                / (best_asksizeshares + best_bidsizeshares);
        keep sym_root date interval_end ts midquote weighted_midquote;
    run;

    proc sort data=_day; by sym_root interval_end ts; run;

    data _day;
        set _day;
        by sym_root interval_end;
        if last.interval_end;       /* = ROW_NUMBER() ... ORDER BY ts DESC, rn = 1 */
        rn = 1;
    run;

    proc append base=nbbo data=_day; run;
    %put NOTE: &ymd done;
%mend pull_day;

%macro pull_all;
    %local d;
    proc datasets lib=work nolist nowarn; delete nbbo; quit;
    %do d = &start_d %to &end_d;
        %pull_day(&d)
        %if &syscc > 4 %then %fail(stopped at %sysfunc(putn(&d, yymmdd10.)) after an error, code=2);
    %end;
%mend pull_all;
%pull_all


/* ---------------------------------------------------------------------------
   Write a gzip-compressed CSV with the same columns as the Python job
   ------------------------------------------------------------------------- */
%macro write_output;
    %local nrows elapsed;
    %if not %sysfunc(exist(work.nbbo)) %then
        %fail(no rows returned between &start and &end);

    proc sort data=nbbo; by sym_root interval_end; run;

    options missing=' ';            /* empty field, like pandas, instead of '.' */
    filename gzout zip "&out_file" gzip;   /* needs SAS 9.4M5 or later */
    data _null_;
        set nbbo;
        file gzout dsd dlm=',' lrecl=1000;
        if _n_ = 1 then put 'sym_root,date,interval_end,ts,midquote,weighted_midquote,rn';
        put sym_root
            date              yymmdd10.
            interval_end      e8601dt23.3
            ts                e8601dt26.6
            midquote          best16.
            weighted_midquote best16.
            rn;
    run;
    filename gzout clear;

    %if &syscc > 4 %then %fail(writing &out_file failed, code=2);

    proc sql noprint; select count(*) into :nrows trimmed from nbbo; quit;
    %let elapsed = %sysfunc(round(%sysevalf(%sysfunc(datetime()) - &t0)));
    %put NOTE: Wrote %sysfunc(putn(&nrows, comma20.)) rows to &out_file in &elapsed.s;
%mend write_output;
%write_output
