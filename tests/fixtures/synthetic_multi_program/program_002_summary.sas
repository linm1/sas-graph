%let domain = secondary;

%dynamic_split(inds=refdata.source_table, outds=work.derived_secondary);

%simple_copy(inds=work.derived_secondary, outds=work.summary_out);

%gmApplyLabels(inds=work.summary_out, outds=work.summary_labeled);
