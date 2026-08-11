%macro simple_copy(inds=, outds=);
    data &outds;
        set &inds;
    run;
%mend simple_copy;
