"""PROC SORT rules (dev plan section 13, 21 Phase 4).

Writes `ctx.sort_by_of[dataset_node_id] = by_vars` on every sort output --
the exact key format (a Dataset node id, e.g. `dataset:work.ae_srt`) that
`rules_data_step._check_merge_sort_prefix` reads for section 12.3's
merge-by-prefix rule. This is the one piece of cross-rule state the phase 4
handoff calls out: statement order alone guarantees this module always runs
before a later MERGE reads the same dataset, so "last write wins" is correct
without a second ordering mechanism.
"""

import re

from .macro_state import resolve_text
from .rules_data_step import by_vars as _normalize_by_vars

# `[\w.&]` (not just `\w.`) so `data=work.&domain.;` captures the macro
# reference whole -- resolve_text needs the literal `&name.` text to report
# it as unresolved; a truncated capture would silently drop the `&`.
_DATA_OPT_RE = re.compile(r"data\s*=\s*([\w.&]+)", re.IGNORECASE)
_OUT_OPT_RE = re.compile(r"\bout\s*=\s*([\w.&]+)", re.IGNORECASE)
_DUPOUT_OPT_RE = re.compile(r"\bdupout\s*=\s*([\w.&]+)", re.IGNORECASE)
_NODUPKEY_RE = re.compile(r"\bnodupkey\b", re.IGNORECASE)
_BY_RE = re.compile(r"^by\s+(.+?);?$", re.IGNORECASE)


def _dataset_id(raw, statement, ctx, let_events, rule):
    """Resolve one DATA=/OUT= token, reporting an unresolved macro variable
    as UnknownDataset (section 15.6) instead of baking the literal `&ref.`
    into a Dataset node id -- the same posture rules_data_step and
    rules_proc_sql already apply to their own dataset-name tokens."""
    resolved, unresolved = resolve_text(
        raw, statement.statement_order, let_events, return_unresolved=True
    )
    if not unresolved:
        return ctx.add_dataset(resolved)

    source = statement.as_source(rule)
    node_id = ctx.add_unknown_dataset(raw, source)
    ctx.add_finding(
        "unresolved_macro_variable_in_data_source",
        "UNRESOLVED_MACRO_VARIABLE",
        "WARNING",
        raw,
        f"Macro variable in `{raw}` has no value at this statement.",
        "Define the macro variable earlier, or confirm it is created at "
        "runtime and therefore out of static scope.",
        source,
        affected_nodes=[node_id],
    )
    return node_id


def apply(block, ctx, let_events, source_order=None):
    """Apply section 13 rules to one PROC SORT block. Mutates ctx."""
    opener = block.opener

    data_match = _DATA_OPT_RE.search(opener.text)
    out_match = _OUT_OPT_RE.search(opener.text)
    if data_match is None:
        return  # No DATA= to resolve; nothing this rule can attach an id to.

    input_id = _dataset_id(data_match.group(1), opener, ctx, let_events, "proc_sort_data")

    # Section 13.3: no OUT= means implicit in-place sort -- output is the
    # same dataset as input.
    output_raw = out_match.group(1) if out_match else data_match.group(1)
    output_id = _dataset_id(output_raw, opener, ctx, let_events, "proc_sort_out")

    sort_by = None
    for statement in block.statements[1:]:
        by_match = _BY_RE.match(statement.text)
        if by_match:
            sort_by = _normalize_by_vars(by_match.group(1))
            break

    step_id = ctx.next_step_id()
    ctx.add_node(
        step_id,
        "Step",
        f"PROC SORT {step_id.split(':')[1]}",
        step_kind="PROC_SORT",
        source=block.as_source("proc_sort_out" if out_match else "proc_sort_implicit"),
    )

    ctx.add_edge("reads_dataset", input_id, step_id, block.as_source("proc_sort_data"))
    ctx.add_edge("writes_dataset", step_id, output_id, block.as_source("proc_sort_out"))

    dupout_match = _DUPOUT_OPT_RE.search(opener.text)
    if dupout_match:
        dupout_id = _dataset_id(dupout_match.group(1), opener, ctx, let_events, "proc_sort_dupout")
        ctx.add_edge("writes_dataset", step_id, dupout_id, block.as_source("proc_sort_dupout"))
        if dupout_id != input_id:
            ctx.add_edge("depends_on", dupout_id, input_id, block.as_source("proc_sort_dupout"))

    patterns = []
    if output_id == input_id:
        patterns.append(
            "IN_PLACE_SORT_OVERWRITE" if out_match else "IMPLICIT_IN_PLACE_SORT_OVERWRITE"
        )
    else:
        ctx.add_edge(
            "depends_on", output_id, input_id, block.as_source("proc_sort_out")
        )

    if _NODUPKEY_RE.search(opener.text):
        patterns.append("ROW_FILTERING_SORT")
        ctx.add_finding(
            "row_filtering_sort",
            "SUPPORTED",
            "INFORMATION",
            output_id,
            "PROC SORT NODUPKEY may reduce row count; data content was not checked.",
            None,
            block.as_source("row_filtering_sort"),
            affected_nodes=[output_id],
        )

    node = next(n for n in ctx.nodes if n["id"] == step_id)
    node["patterns"] = patterns
    if output_id == input_id:
        node["severity"] = "INFORMATION"
    if _NODUPKEY_RE.search(opener.text):
        node["sort_option"] = "NODUPKEY"
        node["cardinality_effect"] = "possible_row_reduction"
        node["data_content_checked"] = False
        node["severity"] = "INFORMATION"
    if sort_by is not None:
        node["by_vars"] = sort_by
        effective_order = (
            source_order if source_order is not None
            else block.statements[-1].statement_order
        )
        ctx.sort_by_of[output_id] = sort_by
        ctx.sort_by_at.setdefault(output_id, []).append((effective_order, sort_by))
        # DUPOUT is only populated under NODUPKEY/NODUPRECS, and the rejected
        # duplicates it holds come out in the same sorted order -- without
        # nodupkey, dupout= writes nothing, so no sort-order claim is safe.
        if dupout_match and _NODUPKEY_RE.search(opener.text):
            ctx.sort_by_of[dupout_id] = sort_by
            ctx.sort_by_at.setdefault(dupout_id, []).append((effective_order, sort_by))
