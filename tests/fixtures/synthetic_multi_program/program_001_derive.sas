%let domain = primary;

proc import datafile="inputs/sample.csv" out=work.sample_raw dbms=csv replace;
    getnames=yes;
run;

data work.sample_clean;
    set work.sample_raw;
run;

%dynamic_split(inds=refdata.source_table, outds=work.derived_primary);
