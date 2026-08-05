"""PROC SQL rules (dev plan section 14.2-14.6, 21 Phase 4).

One PROC SQL block can hold multiple independent statements (section 14.3's
own example: two `create table`s in one `proc sql;...quit;`), so a `SqlBlock`
container node holds one `SqlStatement` per `create table`/`create view`/
`insert into` found in the block body -- never merged into one node, which
would hide which output came from which query (section 14.1's own rationale).

Section 14.7 (`PROC SQL ... INTO :`) is out of scope here: `macro_state.py`
already treats it as runtime macro-variable creation evidence at the
statement level, before this module ever sees the block.
"""

import re

from .macro_state import resolve_text

_STATEMENT_KINDS = (
    ("CREATE_TABLE", re.compile(r"^create\s+table\s+([^\s(;]+)", re.IGNORECASE)),
    ("CREATE_VIEW", re.compile(r"^create\s+view\s+([^\s(;]+)", re.IGNORECASE)),
    ("INSERT_INTO", re.compile(r"^insert\s+into\s+([^\s(;]+)", re.IGNORECASE)),
)
_FROM_RE = re.compile(r"\bfrom\s+([^\s,;()]+)", re.IGNORECASE)
_JOIN_RE = re.compile(r"\bjoin\s+([^\s,;()]+)", re.IGNORECASE)
# ponytail: add a clause tokenizer if comma-separated FROM lists enter scope.


def _next_sql_id(ctx, node_type, prefix):
    number = sum(node["type"] == node_type for node in ctx.nodes) + 1
    return f"{prefix}:{number:03d}"


def _mask_quoted(text):
    masked = list(text)
    index = 0
    while index < len(text):
        if text[index] not in "\"'":
            index += 1
            continue
        quote = text[index]
        masked[index] = " "
        index += 1
        while index < len(text):
            masked[index] = " "
            if text[index] != quote:
                index += 1
                continue
            if index + 1 < len(text) and text[index + 1] == quote:
                masked[index + 1] = " "
                index += 2
                continue
            index += 1
            break
    return "".join(masked)


def apply(block, ctx, let_events):
    """Apply section 14.2-14.6 rules to one `PROC SQL` block. Mutates ctx."""
    if block.kind != "PROC" or block.proc_name != "sql":
        return

    sql_block_id = _next_sql_id(ctx, "SqlBlock", "sqlblock")
    ctx.add_node(sql_block_id, "SqlBlock", f"PROC SQL {sql_block_id.split(':')[1]}",
                 source=block.as_source("proc_sql_block"))

    found_any = False
    all_recovered = True
    for statement in block.statements[1:]:
        text = statement.text
        for subtype, pattern in _STATEMENT_KINDS:
            match = pattern.match(text)
            if not match:
                continue
            found_any = True
            all_recovered &= _apply_statement(
                ctx, let_events, sql_block_id, statement, subtype, match.group(1),
            )
            break

    if not found_any:
        ctx.add_finding(
            "proc_sql_no_recognized_statement",
            "NOT_EXECUTED",
            "INFORMATION",
            sql_block_id,
            "No CREATE TABLE/CREATE VIEW/INSERT INTO statement was recognized "
            "in this PROC SQL block.",
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
        raw, statement.statement_order, let_events, return_unresolved=True
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
        f"Macro variable in SQL dataset `{raw}` has no value at this statement.",
        "Define the macro variable earlier, or confirm it is created at runtime.",
        source,
        affected_nodes=[node_id],
    )
    return node_id


def _apply_statement(ctx, let_events, sql_block_id, statement, subtype, target_raw):
    rule = f"proc_sql_{subtype.lower()}"
    target_id = _dataset_id(
        target_raw, statement, ctx, let_events, f"{rule}_target"
    )
    source_text = _mask_quoted(statement.text)
    source_names = _FROM_RE.findall(source_text)
    if source_names:
        source_names += _JOIN_RE.findall(source_text)
    source_ids = [
        _dataset_id(raw, statement, ctx, let_events, f"{rule}_source")
        for raw in dict.fromkeys(source_names)
    ]

    sql_statement_id = _next_sql_id(ctx, "SqlStatement", "sqlstatement")
    extra = {"sql_subtype": subtype}
    if subtype == "INSERT_INTO":
        extra["pattern"] = "DATASET_MUTATION"
    ctx.add_node(
        sql_statement_id, "SqlStatement", subtype,
        source=statement.as_source(rule), **extra,
    )
    ctx.add_edge(
        "contains_sql_statement", sql_block_id, sql_statement_id,
        source=statement.as_source(rule),
    )

    for source_id in source_ids:
        ctx.add_edge(
            "reads_dataset", sql_statement_id, source_id,
            statement.as_source(f"{rule}_source"),
        )
    write_kwargs = {}
    if subtype == "CREATE_VIEW":
        write_kwargs = {"output_kind": "view", "materialized": False}
    ctx.add_edge(
        "writes_dataset", sql_statement_id, target_id,
        statement.as_source(f"{rule}_target"), **write_kwargs,
    )
    for source_id in source_ids:
        ctx.add_edge(
            "depends_on", target_id, source_id, statement.as_source(rule),
        )
    return bool(source_ids)
