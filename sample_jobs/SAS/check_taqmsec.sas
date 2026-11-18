/*****************************************************************************
 check_taqmsec.sas

 Quick check that the TAQ millisecond library is visible from SAS on WRDS and
 that complete_nbbo has the variables nbbo_batch_main.sas expects. Run it once
 before the real job, from ~/wrds_batch_jobs on the WRDS login node:

     qsas sample_jobs/SAS/check_taqmsec.sas
     # wait for qstat to come back empty, then
     less check_taqmsec.lst

 (qsas writes check_taqmsec.log and check_taqmsec.lst to the directory you ran
 it from. Delete or rename them before running it again.)
*****************************************************************************/

%let day = 20250102;

/* Where does the libref point? */
%put NOTE: taqmsec = %sysfunc(pathname(taqmsec));

/* Which complete_nbbo days exist in early January 2025? */
proc sql;
    title "complete_nbbo datasets for 2025-01";
    select memname
    from dictionary.tables
    where libname = 'TAQMSEC' and memname like 'COMPLETE_NBBO_202501%'
    order by memname;
quit;

/* Variables, types and formats for one day */
title "Variables in taqmsec.complete_nbbo_&day";
proc contents data=taqmsec.complete_nbbo_&day varnum; run;

/* A few rows */
title "First 10 SPY quotes after the open, &day";
proc print data=taqmsec.complete_nbbo_&day(obs=10
           where=(sym_root = 'SPY' and time_m >= '09:30:00't));
    var sym_root sym_suffix date time_m best_bid best_ask
        best_bidsizeshares best_asksizeshares qu_cond;
run;
