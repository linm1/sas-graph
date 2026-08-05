"""PROC EXPORT rules (external-file-sources ticket 04)."""

import re

from .rules_external_file import blank_span, block_text, dataset_id, external_file_id

_DATA_RE = re.compile(r"\bdata\s*=\s*([\w.&]+)", re.IGNORECASE)
_OUTFILE_RE = re.compile(r'\boutfile\s*=\s*(["\'])(.*?)\1', re.IGNORECASE)
_DBMS_RE = re.compile(r"\bdbms\s*=\s*([\w.&]+)", re.IGNORECASE)


def apply(block, ctx, let_events):
    """Record DATA= written to OUTFILE= by one PROC EXPORT block."""
    opener = block.opener
    text = block_text(block)

    outfile_match = _OUTFILE_RE.search(text)
    if outfile_match is None:
        return

    remaining_text = blank_span(text, outfile_match)
    data_match = _DATA_RE.search(remaining_text)
    if data_match is None:
        return

    output_id = external_file_id(
        outfile_match.group(2), opener, ctx, let_events, "proc_export_outfile"
    )
    input_id = dataset_id(
        data_match.group(1), opener, ctx, let_events, "proc_export_data",
        "unresolved_macro_variable_in_export_data",
    )
    edge_attrs = {}
    dbms_match = _DBMS_RE.search(remaining_text)
    if dbms_match:
        edge_attrs["dbms"] = dbms_match.group(1).lower()
    ctx.add_edge(
        "writes_external_file", input_id, output_id,
        block.as_source("proc_export_outfile"), **edge_attrs,
    )
