data work.adae_pre;
  set sdtm.ae adam.adsl;
run;

proc sort data=work.adae_pre out=work.adae_srt;
  by usubjid aeseq;
run;

/* inactive evidence: an earlier draft read from sdtm.suppae
data work.adae_supp;
  set sdtm.suppae;
run;
*/

%gm_derive(inds=work.adae_srt, outds=adam.adae);

%gm_missing(inds=adam.adae, outds=&unknown_out.);
