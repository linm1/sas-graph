"""PROC SQL block rules and dataset-level statement bindings.

SELECT/WHERE/JOIN parsing lives in :mod:`sas_graph.sql_ir`.  This module owns
only PROC SQL block recognition, dataset edges, and the legacy INSERT SET /
INSERT VALUES mutation paths.  Keeping those paths here preserves the rule
module isolation boundary: this rule still knows nothing about DATA-step or
PROC SORT behavior.
"""

import re

from . import sql_ir
from .evidence import EvidenceKind, ResolutionStatus
from .macro_state import resolve_text


_INSERT_INTO_RE = re.compile(
    r"^insert\s+into\s+([^\s(;()]+)(?:\s*\(([^)]*)\))?",
    re.IGNORECASE,
)
_STATEMENT_KINDS = (
    ("CREATE_TABLE", re.compile(r"^create\s+table\s+([^\s(;]+)", re.IGNORECASE)),
    ("CREATE_VIEW", re.compile(r"^create\s+view\s+([^\s(;]+)", re.IGNORECASE)),
    ("INSERT_INTO", _INSERT_INTO_RE),
)
_INSERT_SET_RE = re.compile(r"^set\s+(.+)$", re.IGNORECASE | re.DOTALL)
_INSERT_VALUES_RE = re.compile(
    r"^values\s*\((.*)\)\s*$", re.IGNORECASE | re.DOTALL
)
_INSERT_SET_ASSIGNMENT_RE = re.compile(
    r"^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*=\s*(.*?)\s*$",
    re.DOTALL,
)
_BARE_COLUMN_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$")
_MACRO_REF_RE = re.compile(r"&[A-Za-z_]\w*\.?", re.IGNORECASE)
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*(?:'|$)", re.DOTALL)
_UNKNOWN_ID_PREFIXES = ("unknowndataset:", "unknownvariable:", "unknownmacro:")


def _has_macro_ref(text):
    return bool(_MACRO_REF_RE.search(_SINGLE_QUOTED_RE.sub("", str(text or ""))))


def _edge_evidence(ctx, source, *endpoint_ids, macro_resolved=False):
    """Classify only the endpoint text that produced this SQL edge."""

    extractor = (
        source.get("rule", "rules_proc_sql")
        if isinstance(source, dict) else "rules_proc_sql"
    )
    if any(
        str(node_id).lower().startswith(_UNKNOWN_ID_PREFIXES)
        for node_id in endpoint_ids
    ):
        return ctx.make_evidence(
            EvidenceKind.UNKNOWN,
            ResolutionStatus.UNRESOLVED,
            extractor,
            source,
        )
    if macro_resolved:
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


def _next_sql_id(ctx, node_type, prefix):
    number = sum(node["type"] == node_type for node in ctx.nodes) + 1
    return "{}:{:03d}".format(prefix, number)


def _insert_body(statement):
    match = _INSERT_INTO_RE.match(statement.text)
    if not match:
        return ""
    body = statement.text[match.end():].strip()
    return body[:-1].rstrip() if body.endswith(";") else body


def _parse_insert_target_columns(raw_columns):
    if raw_columns is None:
        return None
    columns = [
        item for item in sql_ir.split_top_level_commas(sql_ir.mask_quoted(raw_columns))
        if item
    ]
    if not columns or any(not _BARE_COLUMN_RE.fullmatch(column) for column in columns):
        return None
    return columns


def apply(block, ctx, let_events):
    """Apply PROC SQL rules to one block.  Mutates the shared graph context."""

    if block.kind != "PROC" or block.proc_name != "sql":
        return

    sql_block_id = _next_sql_id(ctx, "SqlBlock", "sqlblock")
    ctx.add_node(
        sql_block_id,
        "SqlBlock",
        "PROC SQL {}".format(sql_block_id.split(":")[1]),
        source=block.as_source("proc_sql_block"),
    )

    found_any = False
    all_recovered = True
    for statement in block.statements[1:]:
        text = statement.text
        for subtype, pattern in _STATEMENT_KINDS:
            match = pattern.match(text)
            if not match:
                continue
            found_any = True
            target_columns_raw = match.group(2) if subtype == "INSERT_INTO" else None
            all_recovered &= _apply_statement(
                ctx,
                let_events,
                sql_block_id,
                statement,
                subtype,
                match.group(1),
                target_columns_raw,
            )
            break

    if not found_any:
        ctx.add_finding(
            "proc_sql_no_recognized_statement",
            "NOT_EXECUTED",
            "INFORMATION",
            sql_block_id,
            "No CREATE TABLE/CREATE VIEW/INSERT INTO statement was recognized in this PROC SQL block.",
            None,
            block.as_source("proc_sql_no_recognized_statement"),
            affected_nodes=[sql_block_id],
        )

    if not block.terminated:
        recovered = found_any and all_recovered
        ctx.add_finding(
            "sql_block_not_explicitly_closed",
            "SUPPORTED" if recovered else "NOT_EXECUTED",
            "INFORMATION" if recovered else "WARNING",
            sql_block_id,
            "This PROC SQL block has no closing QUIT statement; parsing "
            + (
                "recovered supported lineage at the next DATA/PROC boundary or end of file."
                if recovered else
                "could not recover a complete supported lineage at the next DATA/PROC boundary or end of file."
            ),
            "Add the missing QUIT statement.",
            block.as_source("sql_block_not_explicitly_closed"),
            affected_nodes=[sql_block_id],
        )


def _dataset_id(raw, statement, ctx, let_events, rule):
    resolved, unresolved = resolve_text(
        raw,
        statement.statement_order,
        let_events,
        return_unresolved=True,
    )
    if not unresolved:
        return ctx.add_dataset(resolved)

    source = statement.as_source(rule)
    node_id = ctx.add_unknown_dataset(raw, source)
    ctx.add_finding(
        "unresolved_macro_variable_in_sql_dataset",
        "UNRESOLVED_MACRO_VARIABLE",
        "WARNING",
        raw,
        "Macro variable in SQL dataset `{}` has no value at this statement.".format(raw),
        "Define the macro variable earlier, or confirm it is created at runtime.",
        source,
        affected_nodes=[node_id],
    )
    return node_id


def _resolve_column_ref(ctx, statement, alias_map, source_ids, ref, unresolved_names=()):
    """Materialize an INSERT expression reference through SQL's resolver pass."""

    reference = sql_ir.IRVariableRef(ref)
    binding = sql_ir.resolve_reference(
        reference,
        tuple(source_ids),
        alias_map,
        unresolved_names,
    )
    return sql_ir.materialize_reference(ctx, statement, binding)


def _apply_statement(
    ctx,
    let_events,
    sql_block_id,
    statement,
    subtype,
    target_raw,
    target_columns_raw=None,
):
    rule = "proc_sql_{}".format(subtype.lower())
    query = sql_ir.parse_sql(
        statement.text,
        subtype,
        target_raw,
        statement.as_source(rule),
        let_events,
    )
    target_id = _dataset_id(
        target_raw,
        statement,
        ctx,
        let_events,
        "{}_target".format(rule),
    )

    # The old parser surfaced malformed FROM items before minting the SQL node;
    # retain that ordering and finding wording while the IR owns recognition.
    for diagnostic in query.diagnostics:
        ctx.add_finding(
            diagnostic.code,
            "NOT_EXECUTED",
            "WARNING",
            sql_block_id,
            diagnostic.message,
            diagnostic.suggested_action,
            statement.as_source("{}{}".format(rule, diagnostic.source_suffix)),
        )

    unique_raw_names = list(dict.fromkeys(source.raw_name for source in query.sources))
    raw_to_id = {
        raw: _dataset_id(
            raw,
            statement,
            ctx,
            let_events,
            "{}_source".format(rule),
        )
        for raw in unique_raw_names
    }
    source_ids = [raw_to_id[raw] for raw in unique_raw_names]
    alias_map = {raw.lower(): raw_to_id[raw] for raw in unique_raw_names}
    alias_map.update(
        {
            source.alias.lower(): raw_to_id[source.raw_name]
            for source in query.sources
            if source.alias
        }
    )
    sql_statement_id = _next_sql_id(ctx, "SqlStatement", "sqlstatement")
    extra = {"sql_subtype": subtype}
    if subtype == "INSERT_INTO":
        extra["pattern"] = "DATASET_MUTATION"
    ctx.add_node(
        sql_statement_id,
        "SqlStatement",
        subtype,
        source=statement.as_source(rule),
        **extra
    )
    ctx.add_edge(
        "contains_sql_statement",
        sql_block_id,
        sql_statement_id,
        source=statement.as_source(rule),
        evidence=_edge_evidence(
            ctx,
            statement.as_source(rule),
            sql_block_id,
            sql_statement_id,
        ),
    )

    for raw_source, source_id in zip(unique_raw_names, source_ids):
        source = statement.as_source("{}_source".format(rule))
        ctx.add_edge(
            "reads_dataset",
            source_id,
            sql_statement_id,
            source,
            evidence=_edge_evidence(
                ctx,
                source,
                source_id,
                sql_statement_id,
                macro_resolved=_has_macro_ref(raw_source),
            ),
        )
    write_kwargs = {}
    if subtype == "CREATE_VIEW":
        write_kwargs = {"output_kind": "view", "materialized": False}
    target_source = statement.as_source("{}_target".format(rule))
    ctx.add_edge(
        "writes_dataset",
        sql_statement_id,
        target_id,
        target_source,
        evidence=_edge_evidence(
            ctx,
            target_source,
            sql_statement_id,
            target_id,
            macro_resolved=_has_macro_ref(target_raw),
        ),
        **write_kwargs
    )
    for raw_source, source_id in zip(unique_raw_names, source_ids):
        source = statement.as_source(rule)
        ctx.add_edge(
            "depends_on",
            target_id,
            source_id,
            source,
            evidence=_edge_evidence(
                ctx,
                source,
                target_id,
                source_id,
                macro_resolved=(
                    _has_macro_ref(target_raw) or _has_macro_ref(raw_source)
                ),
            ),
        )

    insert_target_columns = (
        _parse_insert_target_columns(target_columns_raw)
        if subtype == "INSERT_INTO" else None
    )
    insert_body = _insert_body(statement) if subtype == "INSERT_INTO" else ""
    set_match = _INSERT_SET_RE.match(insert_body)
    if set_match:
        _apply_insert_set(
            ctx,
            statement,
            sql_statement_id,
            target_id,
            alias_map,
            source_ids,
            set_match.group(1),
        )
    else:
        values_match = _INSERT_VALUES_RE.match(insert_body)
        if values_match:
            _apply_insert_values(
                ctx,
                statement,
                sql_statement_id,
                target_id,
                alias_map,
                source_ids,
                insert_target_columns,
                values_match.group(1),
            )
        else:
            resolved = sql_ir.resolve_sql(query, source_ids, alias_map)
            sql_ir.emit_sql(
                resolved,
                ctx,
                statement,
                sql_statement_id,
                target_id,
                insert_target_columns,
                semantic_edges=True,
            )

    return bool(source_ids)


def _apply_expression_reads(
    ctx,
    statement,
    sql_statement_id,
    alias_map,
    source_ids,
    expr,
    rule,
    unresolved_names=(),
    macro_source=None,
):
    for ref in sql_ir.extract_identifiers(expr):
        read_id = _resolve_column_ref(
            ctx,
            statement,
            alias_map,
            source_ids,
            ref,
            unresolved_names,
        )
        expression_source = statement.as_source(rule)
        ctx.add_edge(
            "reads_variable",
            read_id,
            sql_statement_id,
            expression_source,
            evidence=_edge_evidence(
                ctx,
                expression_source,
                read_id,
                sql_statement_id,
                macro_resolved=_has_macro_ref(
                    macro_source if macro_source is not None else ref
                ),
            ),
            value=None,
            operator=None,
        )


def _apply_insert_set(
    ctx,
    statement,
    sql_statement_id,
    target_id,
    alias_map,
    source_ids,
    assignments_text,
):
    if not target_id.startswith("dataset:"):
        return
    target_raw = target_id[len("dataset:"):]
    rule = "proc_sql_insert_set"
    masked_assignments = sql_ir.mask_quoted(assignments_text)
    for rel_start, rel_end in sql_ir.split_top_level_commas_spans(masked_assignments):
        item = assignments_text[rel_start:rel_end].strip()
        match = _INSERT_SET_ASSIGNMENT_RE.match(item)
        if not match:
            ctx.add_finding(
                "proc_sql_unsupported_syntax",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                "INSERT SET item `{}` is not a `column=expression` pair; it was not parsed.".format(item),
                "Rewrite INSERT SET as comma-separated column=expression pairs.",
                statement.as_source(rule),
                affected_nodes=[sql_statement_id],
            )
            continue
        column, expression = match.group(1), match.group(2)
        literal = sql_ir.literal_value(expression)
        if literal is None:
            _apply_expression_reads(
                ctx,
                statement,
                sql_statement_id,
                alias_map,
                source_ids,
                sql_ir.mask_quoted(sql_ir.mask_literal_residue(expression)),
                rule,
                (),
                macro_source=expression,
            )
        write_id = ctx.add_variable(target_raw, column)
        write_source = statement.as_source(rule)
        ctx.add_edge(
            "writes_variable",
            sql_statement_id,
            write_id,
            write_source,
            evidence=_edge_evidence(
                ctx,
                write_source,
                sql_statement_id,
                write_id,
                macro_resolved=False,
            ),
            value=literal,
            operator=None,
        )


def _apply_insert_values(
    ctx,
    statement,
    sql_statement_id,
    target_id,
    alias_map,
    source_ids,
    target_columns,
    values_text,
):
    rule = "proc_sql_insert_values"
    if not target_id.startswith("dataset:"):
        return
    if not target_columns:
        ctx.add_finding(
            "proc_sql_unsupported_syntax",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            "INSERT VALUES requires an explicit target column list for positional mapping; it was not parsed.",
            "Add an explicit target column list to the INSERT statement.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return

    masked_values = sql_ir.mask_quoted(values_text)
    value_spans = sql_ir.split_top_level_commas_spans(masked_values)
    if len(value_spans) != len(target_columns):
        ctx.add_finding(
            "proc_sql_unsupported_syntax",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            "INSERT VALUES count does not match the explicit target column count; it was not parsed.",
            "Provide one VALUES expression for each target column.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return

    target_raw = target_id[len("dataset:"):]
    for column, (start, end) in zip(target_columns, value_spans):
        expression = values_text[start:end].strip()
        literal = sql_ir.literal_value(expression)
        if literal is None:
            _apply_expression_reads(
                ctx,
                statement,
                sql_statement_id,
                alias_map,
                source_ids,
                sql_ir.mask_quoted(sql_ir.mask_literal_residue(expression)),
                rule,
                (),
                macro_source=expression,
            )
        write_id = ctx.add_variable(target_raw, column)
        write_source = statement.as_source(rule)
        ctx.add_edge(
            "writes_variable",
            sql_statement_id,
            write_id,
            write_source,
            evidence=_edge_evidence(
                ctx,
                write_source,
                sql_statement_id,
                write_id,
                macro_resolved=False,
            ),
            value=literal,
            operator=None,
        )


def demo():
    print("proc sql rules: use tests/test_proc_sql_rules.py for coverage")


if __name__ == "__main__":
    demo()
