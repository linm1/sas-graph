proc sql;
  create table work.tlf_flag as
  select a.anl01fl
  from adam.adlb as a
  where a.anl01fl = 'Y';

  create table work.tlf_category as
  select l.lbnrind
  from sdtm.lb as l
  where l.lbnrind = 'ABNORMAL';
quit;
