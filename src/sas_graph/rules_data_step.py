"""DATA step rules (dev plan section 12, 21 Phase 4).

Each function takes one `Block` (kind == "DATA") and the shared `GraphContext`,
and does its own dataset-name resolution via `resolve_text` against the
caller-supplied `let_events` -- section 11.3 forbids caching a resolved value
across calls, so every dataset reference resolves at its own statement order.

Scope for this pass: 12.1 (SET), 12.2-12.4 (MERGE/BY + sort-prefix), 12.5
(multi-write is naturally supported, nothing special to add), 12.6-12.7
(multi-output DATA), 12.8 (DATA _NULL_), 12.9 (WHERE), 12.10 (KEEP/DROP/RENAME).
"""

import re

from .macro_state import resolve_text

_SET_RE = re.compile(r"^set\s+(.+?);?$", re.IGNORECASE)
_MERGE_RE = re.compile(r"^merge\s+(.+?);?$", re.IGNORECASE)
_BY_RE = re.compile(r"^by\s+(.+?);?$", re.IGNORECASE)
_OUTPUT_ACTION_RE = re.compile(
    r"^\s*(?:output(?:\s+[\w.&]+)?|(?:if\b.*\bthen|else)\s+output(?:\s+[\w.&]+)?)\s*;$",
    re.IGNORECASE,
)
_OUTPUT_BARE_RE = re.compile(
    r"^\s*(?:output|(?:if\b.*\bthen|else)\s+output)\s*;$", re.IGNORECASE,
)
_WHERE_RE = re.compile(r"\bwhere\s+(.+?);", re.IGNORECASE)
_KEEP_RE = re.compile(r"\bkeep\s+(.+?);", re.IGNORECASE)
_DROP_RE = re.compile(r"\bdrop\s+(.+?);", re.IGNORECASE)
_RENAME_RE = re.compile(r"\brename\s+(.+?);", re.IGNORECASE)
_RENAME_PAIR_RE = re.compile(r"(\w+)\s*=\s*(\w+)")
# Dataset options: `sdtm.ae (where=(aeser="Y"))` -- stripped before splitting
# on whitespace so `_resolve_dataset_list` never treats an option group as a
# dataset name of its own.
_DATASET_OPTIONS_RE = re.compile(r"\([^()]*\)")

# `data work.a work.b;` -- one or more space-separated dataset names, `_null_`
# excluded because it is a keyword, not a dataset (section 12.8).
_DATA_HEADER_RE = re.compile(r"^data\s+(.+?);?$", re.IGNORECASE)


def _data_targets(opener_text):
    match = _DATA_HEADER_RE.match(opener_text)
    if not match:
        return []
    return [name for name in match.group(1).split() if name.lower() != "_null_"]


def _is_null_data(opener_text):
    match = _DATA_HEADER_RE.match(opener_text)
    if not match:
        return False
    names = match.group(1).split()
    return len(names) == 1 and names[0].lower() == "_null_"


def _resolve_dataset_list(raw, statement_order, let_events, source, ctx):
    """Resolve a space-separated dataset list, reporting each unresolved name
    as an `UnknownDataset` (section 15.6) instead of guessing.

    Dataset options (`sdtm.ae (where=(aeser="Y"))`) are stripped first --
    section 12.9 captures WHERE as step-level metadata separately, so a
    dataset-level option group here is not read for its own content, only
    removed so it cannot masquerade as a second dataset name.
    """
    # One-level-nesting-aware strip: `(where=(x="Y"))` needs the inner group
    # gone before the outer `(...)` has no parens left to match non-greedily.
    while "(" in raw:
        stripped = _DATASET_OPTIONS_RE.sub("", raw)
        if stripped == raw:
            break
        raw = stripped
    resolved_ids = []
    for token in raw.split():
        text, unresolved = resolve_text(
            token, statement_order, let_events, return_unresolved=True
        )
        if unresolved:
            node_id = ctx.add_unknown_dataset(token, source)
            ctx.add_finding(
                "unresolved_macro_variable_in_data_source",
                "UNRESOLVED_MACRO_VARIABLE",
                "WARNING",
                token,
                f"Macro variable in `{token}` has no value at this statement.",
                "Define the macro variable earlier, or confirm it is created "
                "at runtime and therefore out of static scope.",
                source,
                affected_nodes=[node_id],
            )
            resolved_ids.append(node_id)
        else:
            resolved_ids.append(ctx.add_dataset(text))
    return resolved_ids


def by_vars(by_text):
    """Normalize BY variables per section 12.4: name/direction/position.

    `descending name` flips direction; a bare name is ascending. SAS also
    accepts `notsorted`/`groupformat` options, out of scope for v0 -- an
    unrecognized trailing token is treated as an additional bare variable
    name, which is the same "expose, don't guess" posture as the rest of the
    phase (a wrong-shaped by_vars entry is visible in graph.json, not hidden).
    """
    tokens = by_text.split()
    result = []
    position = 0
    pending_descending = False
    for token in tokens:
        if token.lower() == "descending":
            pending_descending = True
            continue
        position += 1
        result.append(
            {
                "name": token,
                "direction": "descending" if pending_descending else "ascending",
                "position": position,
            }
        )
        pending_descending = False
    return result


def _is_prefix(merge_by, sort_by):
    """Section 12.3: merge BY is supported when it is a leading prefix of the
    prior sort's BY, comparing names only (direction is not part of the
    prefix check -- section 12.3's example never varies it)."""
    merge_names = [v["name"].lower() for v in merge_by]
    sort_names = [v["name"].lower() for v in sort_by]
    return sort_names[: len(merge_names)] == merge_names


def apply(block, ctx, let_events, sort_by_fallback=None):
    """Apply section 12 rules to one DATA block. Returns nothing; mutates ctx."""
    opener = block.opener

    if _is_null_data(opener.text):
        _apply_null_data(block, ctx, let_events)
        return

    targets = _data_targets(opener.text)
    # Route through _resolve_dataset_list, not a bare ctx.add_dataset loop:
    # `data work.&domain._out;` must produce an UnknownDataset + finding, the
    # same posture SET/MERGE already have, not a Dataset node whose id bakes
    # in a literal unresolved `&domain.` reference.
    output_ids = _resolve_dataset_list(
        " ".join(targets), opener.statement_order, let_events,
        opener.as_source("data_step_output"), ctx,
    )

    step_id = ctx.next_step_id()
    ctx.add_node(step_id, "Step", f"DATA step {step_id.split(':')[1]}", step_kind="DATA",
                 source=block.as_source("data_step_set"))
    patterns = []

    set_inputs, merge_inputs, merge_by, merge_by_source = [], [], None, None
    for statement in block.statements[1:]:
        set_match = _SET_RE.match(statement.text)
        merge_match = _MERGE_RE.match(statement.text)
        by_match = _BY_RE.match(statement.text)

        if set_match:
            source = statement.as_source("data_step_set")
            set_inputs.extend((dataset_id, source) for dataset_id in _resolve_dataset_list(
                set_match.group(1), statement.statement_order, let_events, source, ctx
            ))
        elif merge_match:
            source = statement.as_source("data_step_merge")
            merge_inputs.extend(
                (dataset_id, source, statement.statement_order)
                for dataset_id in _resolve_dataset_list(
                    merge_match.group(1), statement.statement_order, let_events, source, ctx
                )
            )
        elif by_match:
            merge_by = by_vars(by_match.group(1))
            merge_by_source = statement.as_source("merge_by_is_prefix_of_sort_by")

    input_ids = [*set_inputs, *((dataset_id, source) for dataset_id, source, _ in merge_inputs)]

    for input_id, input_source in input_ids:
        ctx.add_edge("reads_dataset", step_id, input_id, input_source)

    for output_id in output_ids:
        ctx.add_edge(
            "writes_dataset", step_id, output_id, block.as_source("data_step_output")
        )
        for input_id, input_source in input_ids:
            ctx.add_edge(
                "depends_on", output_id, input_id, input_source
            )
            if input_id == output_id:
                patterns.append("IN_PLACE_OVERWRITE")

    if merge_inputs and merge_by is not None:
        _check_merge_sort_prefix(
            [(dataset_id, merge_order) for dataset_id, _source, merge_order in merge_inputs],
            merge_by,
            merge_by_source,
            ctx,
            sort_by_fallback,
        )

    if len(targets) > 1:
        patterns.append("MULTI_OUTPUT_DATA_STEP")
        node = next(n for n in ctx.nodes if n["id"] == step_id)
        node["branch_text"] = [
            statement.original_text.strip() for statement in block.statements[1:]
            if _OUTPUT_ACTION_RE.match(statement.text)
        ]
        node["row_allocation_inferred"] = False
        _check_output_targets(block, ctx, targets, output_ids)
        if any(_OUTPUT_BARE_RE.search(statement.text) for statement in block.statements[1:]):
            patterns.append("MULTI_OUTPUT_AMBIGUOUS")

    node = next(n for n in ctx.nodes if n["id"] == step_id)
    node["patterns"] = patterns
    if merge_by is not None:
        node["by_vars"] = merge_by

    _apply_where(block, ctx, step_id)
    _apply_shape_metadata(block, ctx, step_id)

    node["patterns"] = sorted(set(node["patterns"]))


def _check_merge_sort_prefix(
    merge_ids, merge_by, merge_by_source, ctx, sort_by_fallback=None
):
    """Section 12.3: allow merge BY as a leading prefix of the most recent
    PROC SORT evidence for *every* merged dataset. Absent evidence is not an
    error (section 12.2: "do not prove input sort correctness unless prior
    visible PROC SORT evidence exists") -- only a mismatch is reported."""
    merge_names = [v["name"] for v in merge_by]
    for dataset_id, merge_order in merge_ids:
        history = ctx.sort_by_at.get(dataset_id)
        if history is None:
            sort_by = ctx.sort_by_of.get(dataset_id)
        else:
            sort_by = next(
                (by_vars for order, by_vars in reversed(history) if order < merge_order),
                sort_by_fallback.get(dataset_id) if sort_by_fallback else None,
            )
        if sort_by is None:
            continue
        if not _is_prefix(merge_by, sort_by):
            ctx.add_finding(
                "merge_by_not_prefix_of_sort_by",
                "REQUIRES_DECISION",
                "WARNING",
                dataset_id,
                f"MERGE BY ({' '.join(merge_names)}) is not a leading prefix of "
                f"the prior PROC SORT BY for {dataset_id}.",
                "Re-sort the input with BY variables matching the MERGE BY "
                "prefix, or confirm the merge is intentional.",
                merge_by_source,
            )


def _check_output_targets(block, ctx, targets, output_ids):
    """Sections 12.6/12.7: multi-output DATA step. An `output <ds>;` names its
    target explicitly (12.6, fully supported); a bare `output;` is ambiguous
    per 12.7 and must not guess which target received the row."""
    if any(_OUTPUT_BARE_RE.search(statement.text) for statement in block.statements[1:]):
        ctx.add_finding(
            "AMBIGUOUS_OUTPUT_TARGET",
            "REQUIRES_DECISION",
            "WARNING",
            block.opener.text,
            "Bare `output;` in a multi-output DATA step does not name its "
            "target; row allocation across "
            f"{', '.join(targets)} was not inferred.",
            "Use `output <dataset>;` to name the target explicitly.",
            block.as_source("AMBIGUOUS_OUTPUT_TARGET"),
            affected_nodes=output_ids,
        )


def _apply_null_data(block, ctx, let_events):
    """Section 12.8: DATA _NULL_ creates a Step with no output Dataset node."""
    step_id = ctx.next_step_id()
    ctx.add_node(step_id, "Step", f"DATA step {step_id.split(':')[1]}", step_kind="DATA",
                 source=block.as_source("data_null_step"))
    node = next(n for n in ctx.nodes if n["id"] == step_id)
    node["patterns"] = ["DATA_NULL_STEP"]

    for statement in block.statements[1:]:
        set_match = _SET_RE.match(statement.text)
        if set_match:
            input_ids = _resolve_dataset_list(
                set_match.group(1),
                statement.statement_order,
                let_events,
                statement.as_source("data_step_set"),
                ctx,
            )
            for input_id in input_ids:
                ctx.add_edge(
                    "reads_dataset", step_id, input_id, statement.as_source("data_step_set")
                )

    if re.search(r"call\s+symputx\s*\(", " ".join(s.text for s in block.statements), re.IGNORECASE):
        node["patterns"].append("RUNTIME_MACRO_VARIABLE_CREATION")
        node["usable_for_static_resolution"] = False


def _apply_where(block, ctx, step_id):
    """Section 12.9. Matched per-statement, not joined text -- a joined-text
    scan with a greedy `.+?;` risks spanning into an unrelated statement."""
    match = None
    for statement in block.statements[1:]:
        match = _WHERE_RE.search(statement.original_text)
        if match:
            break
    if not match:
        return
    ctx.add_finding(
        "row_filter",
        "SUPPORTED",
        "INFORMATION",
        step_id,
        f"WHERE condition captured as row-filter metadata: {match.group(1).strip()}",
        None,
        block.as_source("row_filter"),
        affected_nodes=[step_id],
    )
    node = next(n for n in ctx.nodes if n["id"] == step_id)
    node.setdefault("patterns", []).append("ROW_FILTER")
    node["condition_text"] = match.group(1).strip()
    node["clinical_meaning_interpreted"] = False
    node["where_condition"] = match.group(1).strip()


def _apply_shape_metadata(block, ctx, step_id):
    """Section 12.10: KEEP/DROP/RENAME as variable-shape metadata only.

    Matched per-statement, same reasoning as `_apply_where` -- each of these
    is a single SAS statement in practice, and scanning per-statement removes
    any risk of a joined-text match spanning into a sibling statement."""
    keep = drop = rename = None
    for statement in block.statements:
        keep = keep or _KEEP_RE.search(statement.original_text)
        drop = drop or _DROP_RE.search(statement.original_text)
        rename = rename or _RENAME_RE.search(statement.original_text)
    if not (keep or drop or rename):
        return

    node = next(n for n in ctx.nodes if n["id"] == step_id)
    node.setdefault("patterns", []).append("VARIABLE_SHAPE_CHANGE")
    node["keep_vars"] = keep.group(1).split() if keep else []
    node["drop_vars"] = drop.group(1).split() if drop else []
    node["rename_map"] = (
        dict(_RENAME_PAIR_RE.findall(rename.group(1))) if rename else {}
    )
