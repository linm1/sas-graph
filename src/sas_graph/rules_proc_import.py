"""PROC IMPORT rules (external-file-sources ticket 01).

`ExternalFile` node id is the resolved (or raw-unresolved) `datafile=` text
alone -- `sheet=` is an edge attribute, not part of the node id, so multiple
sheet imports of one spreadsheet collapse to one shared node (`ctx.add_node`
is already idempotent by id, section 10.1's own posture for `Dataset`).

`datafile=`/`out=`/`dbms=`/`sheet=`/`getnames=` are each matched by an
independent, order-agnostic `re.search` over every statement in the block --
the same pattern `rules_proc_sort.py`'s `_DATA_OPT_RE`/`_OUT_OPT_RE` use for
DATA=/OUT=. Unlike PROC SORT, real usage (tests/fixtures/qc_adae/qc_adae.sas
:141-146) puts `sheet=`/`getnames=` on their own statement lines inside the
block body, not just the opener, so the search runs over the whole block's
text rather than `opener.text` alone.
"""

import re

from .evidence import EvidenceKind, ResolutionStatus
from .rules_external_file import blank_span, block_text, dataset_id, external_file_id

_DATAFILE_RE = re.compile(r'\bdatafile\s*=\s*(["\'])(.*?)\1', re.IGNORECASE)
_OUT_RE = re.compile(r"\bout\s*=\s*([\w.&]+)", re.IGNORECASE)
_DBMS_RE = re.compile(r"\bdbms\s*=\s*([\w.&]+)", re.IGNORECASE)
_SHEET_RE = re.compile(r'\bsheet\s*=\s*(?:(["\'])(.*?)\1|([\w.&$]+))', re.IGNORECASE)
_GETNAMES_RE = re.compile(r"\bgetnames\s*=\s*([\w.&]+)", re.IGNORECASE)
_MACRO_REF_RE = re.compile(r"&[A-Za-z_]\w*\.?", re.IGNORECASE)
_UNKNOWN_ID_PREFIXES = ("unknowndataset:", "unknownvariable:", "unknownmacro:")
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*(?:'|$)", re.DOTALL)


def _has_macro_ref(text):
    return bool(_MACRO_REF_RE.search(_SINGLE_QUOTED_RE.sub("", str(text or ""))))


def _edge_evidence(ctx, source, *endpoint_ids):
    extractor = source.get("rule", "rules_proc_import")
    if any(
        str(node_id).lower().startswith(_UNKNOWN_ID_PREFIXES)
        or _MACRO_REF_RE.search(str(node_id))
        for node_id in endpoint_ids
    ):
        return ctx.make_evidence(
            EvidenceKind.UNKNOWN,
            ResolutionStatus.UNRESOLVED,
            extractor,
            source,
        )
    if _has_macro_ref(source.get("original_text", "")):
        return ctx.make_evidence(
            EvidenceKind.RESOLVED,
            ResolutionStatus.EXACT,
            extractor,
            source,
        )
    return ctx.make_evidence(
        EvidenceKind.OBSERVED,
        ResolutionStatus.EXACT,
        extractor,
        source,
    )


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

    source = block.as_source("proc_import_datafile")
    ctx.add_edge(
        "reads_external_file", external_id, output_id, source,
        evidence=_edge_evidence(ctx, source, external_id, output_id),
        **edge_attrs,
    )
