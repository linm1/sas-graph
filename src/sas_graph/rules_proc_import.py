"""PROC IMPORT rules (external-file-sources ticket 01).

`ExternalFile` node id is the resolved (or raw-unresolved) `datafile=` text
alone -- `sheet=` is an edge attribute, not part of the node id, so multiple
sheet imports of one spreadsheet collapse to one shared node (`ctx.add_node`
is already idempotent by id, section 10.1's own posture for `Dataset`).

`datafile=`/`out=`/`dbms=`/`sheet=`/`getnames=` are each matched by an
independent, order-agnostic `re.search` over every statement in the block --
the same pattern `rules_proc_sort.py`'s `_DATA_OPT_RE`/`_OUT_OPT_RE` use for
DATA=/OUT=. Unlike PROC SORT, `sheet=`/`getnames=` can appear on their own
statement lines inside the
block body, not just the opener, so the search runs over the whole block's
text rather than `opener.text` alone.
"""

import re

from .rules_external_file import blank_span, block_text, dataset_id, external_file_id

_DATAFILE_RE = re.compile(r'\bdatafile\s*=\s*(["\'])(.*?)\1', re.IGNORECASE)
_OUT_RE = re.compile(r"\bout\s*=\s*([\w.&]+)", re.IGNORECASE)
_DBMS_RE = re.compile(r"\bdbms\s*=\s*([\w.&]+)", re.IGNORECASE)
_SHEET_RE = re.compile(r'\bsheet\s*=\s*(?:(["\'])(.*?)\1|([\w.&$]+))', re.IGNORECASE)
_GETNAMES_RE = re.compile(r"\bgetnames\s*=\s*([\w.&]+)", re.IGNORECASE)


def apply(block, ctx, let_events):
    """Apply ticket 01 rules to one PROC IMPORT block. Mutates ctx."""
    opener = block.opener
    text = block_text(block)

    datafile_match = _DATAFILE_RE.search(text)
    if datafile_match is None:
        return  # Nothing to attach an ExternalFile id to.

    # Every other option is searched over the DATAFILE= span blanked out --
    # a raw quoted path can legitimately contain "out="/"dbms="/etc. as
    # literal filename text, which must never be misread as PROC IMPORT's
    # own options.
    remaining_text = blank_span(text, datafile_match)

    out_match = _OUT_RE.search(remaining_text)
    if out_match is None:
        return  # Nothing to attach a Dataset id to.

    external_id = external_file_id(
        datafile_match.group(2), opener, ctx, let_events, "proc_import_datafile"
    )
    output_id = dataset_id(
        out_match.group(1), opener, ctx, let_events, "proc_import_out",
        "unresolved_macro_variable_in_import_out",
    )

    edge_attrs = {}
    dbms_match = _DBMS_RE.search(remaining_text)
    if dbms_match:
        edge_attrs["dbms"] = dbms_match.group(1).lower()
    sheet_match = _SHEET_RE.search(remaining_text)
    if sheet_match:
        edge_attrs["sheet"] = (
            sheet_match.group(2) if sheet_match.group(2) is not None else sheet_match.group(3)
        )
    getnames_match = _GETNAMES_RE.search(remaining_text)
    if getnames_match:
        edge_attrs["getnames"] = getnames_match.group(1).lower()

    ctx.add_edge(
        "reads_external_file", external_id, output_id,
        block.as_source("proc_import_datafile"), **edge_attrs,
    )
