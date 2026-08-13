data adam.adlb;
  set sdtm.lb;
  anl01fl = 'Y';
run;

data adam.adlb;
  set adam.adlb;
  imputed_dt = lbdat;
  anldt = imputed_dt;
run;
