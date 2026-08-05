/* Macro source for the contract in ../contracts/gm_derive.md.

   Phase 1 only needs this directory to exist so `macro_roots` is honest.
   Phase 3 indexes it; Phase 4 binds the call in adae.sas to it. */

%macro gm_derive(inds=, outds=);

  data &outds.;
    set &inds.;
  run;

%mend gm_derive;
