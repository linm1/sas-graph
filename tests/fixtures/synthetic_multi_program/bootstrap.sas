%macro env_bootstrap;
    %let region = east;
    %let period = current;

    libname refdata "/synthetic/reference" access=readonly;

    %os_fvars(mvar=work_path, projpath=&period.:data:extract)
    %os_fvars(mvar=ref_path, projpath=&region.:data:reference)
%mend env_bootstrap;

%env_bootstrap;
