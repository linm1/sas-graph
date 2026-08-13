"""PROC SQL rules (dev plan section 14.2-14.6, 21 Phase 4).

One PROC SQL block can hold multiple independent statements (section 14.3's
own example: two `create table`s in one `proc sql;...quit;`), so a `SqlBlock`
container node holds one `SqlStatement` per `create table`/`create view`/
`insert into` found in the block body -- never merged into one node, which
would hide which output came from which query (section 14.1's own rationale).

Section 14.7 (`PROC SQL ... INTO :`) is out of scope here: `macro_state.py`
already treats it as runtime macro-variable creation evidence at the
statement level, before this module ever sees the block.

Variable-level lineage (wayfinder: sql-select-alias-variable-edges) extends
this module with `SELECT` column list parsing: a qualified column (`t1.var`)
resolves through the statement's own `FROM`/`JOIN` alias table; an
unqualified column resolves only when the statement has exactly one source
table, otherwise it is ambiguous.

wayfinder: sql-where-case-variable-edges extends this further to `WHERE`
predicates and single-branch `CASE WHEN ... THEN ... [ELSE ...] END`
SELECT columns, reusing the same alias table and the smallest supported
grammar: one `column OP (literal|column)` comparison. Unlike the DATA-step
condition rule (`rules_data_step._condition_edges`), an unsupported shape
here (AND/OR, IN, LIKE, a subquery, or a multi-WHEN CASE) always produces a
`proc_sql_unsupported_syntax` finding rather than silently falling through
-- SQL predicates have no other statement-level evidence to fall back on.
"""

import re

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
# `alias` capture requires a literal `AS` -- a bare trailing word after a
# table/join reference is ambiguous with the next clause's own keywords
# (`on`, `where`, ...), so only the unambiguous `AS name` form is recognized.
# `FROM`/`WHERE` are start-token patterns. Their clause ends are found by the
# top-level scanner below so a nested subquery's keywords are skipped.
_FROM_CLAUSE_RE = re.compile(r"\bfrom\b", re.IGNORECASE)
_FROM_CLAUSE_END_RE = re.compile(
    r"\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\bhaving\b|;|$",
    re.IGNORECASE,
)
_JOIN_BOUNDARY_RE = re.compile(
    r"(?:(?:inner|left|right|full|cross|outer)\s+)*\bjoin\b",
    re.IGNORECASE,
)
_JOIN_REF_RE = re.compile(r"\bjoin\s+([^\s,;()]+)(?:\s+as\s+([A-Za-z_]\w*))?", re.IGNORECASE)
# Anchored full-string match per comma-split FROM item: a source that isn't a
# plain `name` or `name AS alias` (e.g. a function-call-shaped
# `myfunc('arg')`) simply fails to match rather than capturing a truncated
# prefix -- caller emits a finding instead of guessing a dataset from it.
_FROM_ITEM_RE = re.compile(r"^([^\s,;()]+)(?:\s+as\s+([A-Za-z_]\w*))?$", re.IGNORECASE)

# `\s+?` (lazy) after `select`, not `\s+` (greedy): a masked quoted literal
# right after `select` is indistinguishable from real whitespace, so a
# greedy quantifier here would swallow it before the capture group even
# starts, losing the column boundary for a column that's a bare literal.
_SELECT_LIST_RE = re.compile(r"\bselect\s+?", re.IGNORECASE)
_DISTINCT_RE = re.compile(r"^\s*(?:distinct|all|unique)\s+", re.IGNORECASE)
# Same reasoning as `_TABLE_REF_RE`: only the unambiguous `AS name` column
# alias form is recognized, not a bare trailing word. Group 1 may be empty:
# `_split_top_level_commas` strips each column, so a column that's entirely
# a masked (quoted-literal) expression collapses to just "as alias" with no
# separating whitespace left -- that's a real zero-identifier expression,
# not a missing one, so `\s*\bas\s+` (boundary, not mandatory whitespace)
# still recognizes it. `_extract_identifiers` on an empty string correctly
# yields no reads_variable edges.
_COLUMN_ALIAS_RE = re.compile(
    r"^(.*?)\s*\bas\s+([A-Za-z_]\w*)"
    r"(?:\s+(?:length|format|label)\s*=\s*.*)?$",
    re.IGNORECASE | re.DOTALL,
)
_BARE_COLUMN_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$")
_IDENTIFIER_RE = re.compile(
    r"(?<!\.)\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\b\s*(\()?(?!\s*\.)"
)
_EXCLUDED_IDENTIFIER_WORDS = {
    "and", "or", "not", "case", "when", "then", "else", "end",
    "distinct", "all", "unique", "calculated", "user", "null",
}
# SAS name-literal syntax (`'odd name'n`) is not parsed -- without this guard
# the masked quote content plus trailing `n` reads as two stray identifiers.
_N_LITERAL_RE = re.compile(r"['\"][^'\"]*['\"]\s*n\b", re.IGNORECASE)
_LITERAL_SUFFIX_RE = re.compile(
    r"(['\"])(?:(?!\1).|\1\1)*\1(?:dt|d|t|x)\b",
    re.IGNORECASE | re.DOTALL,
)
_SPECIAL_MISSING_RE = re.compile(r"(?<![\w.])\.[A-Za-z]\b", re.IGNORECASE)
_QUOTED_SPAN_RE = re.compile(
    r'"(?:""|[^"])*(?:"|$)|\'(?:\'\'|[^\'])*(?:\'|$)', re.DOTALL
)

# wayfinder: sql-where-case-variable-edges -- WHERE start/end patterns. Both
# are matched against masked text so a quoted `;` or clause keyword cannot end
# the span early; the resulting offsets slice the ORIGINAL text below.
_WHERE_CLAUSE_RE = re.compile(r"\bwhere\b", re.IGNORECASE)
_WHERE_CLAUSE_END_RE = re.compile(
    r"\bgroup\s+by\b|\border\s+by\b|\bhaving\b|;|$",
    re.IGNORECASE,
)
_GROUP_BY_CLAUSE_RE = re.compile(r"\bgroup\s+by\b", re.IGNORECASE)
_HAVING_CLAUSE_RE = re.compile(r"\bhaving\b", re.IGNORECASE)
_ORDER_BY_CLAUSE_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_ORDER_BY_DIRECTION_RE = re.compile(
    r"\s+(?:asc|desc)(?:\s+nulls\s+(?:first|last))?\s*$",
    re.IGNORECASE,
)
_INSERT_SET_RE = re.compile(r"^set\s+(.+)$", re.IGNORECASE | re.DOTALL)
_INSERT_VALUES_RE = re.compile(
    r"^values\s*\((.*)\)\s*$", re.IGNORECASE | re.DOTALL
)
# Smallest supported grammar: `column OP (literal|column)`, mirroring
# `rules_data_step._COMPARISON_RE` (duplicated, not imported -- rule modules
# stay independent) plus SQL's `<>` not-equal spelling. Order multi-char
# tokens before their single-char prefixes so alternation doesn't stop early.
_SQL_COMPARISON_RE = re.compile(
    r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*"
    r"(<>|>=|<=|~=|\^=|=|>|<|\beq\b|\bne\b|\bgt\b|\blt\b|\bge\b|\ble\b)\s*(.+)$",
    re.IGNORECASE,
)
_CASE_HEAD_RE = re.compile(r"^case\b", re.IGNORECASE)
# Counted on the MASKED expression, not the original -- a quoted THEN/ELSE
# value containing the word "when"/"then"/"else" must not be mistaken for a
# second branch. Exactly one WHEN and one THEN, at most one ELSE, is this
# ticket's supported shape; anything else is a multi-condition CASE
# fall-through, out of scope (finding, not a guessed edge).
_WHEN_COUNT_RE = re.compile(r"\bwhen\b", re.IGNORECASE)
_THEN_COUNT_RE = re.compile(r"\bthen\b", re.IGNORECASE)
_ELSE_COUNT_RE = re.compile(r"\belse\b", re.IGNORECASE)


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


def _protect_single_quoted(text):
    """Hide single-quoted literals while leaving bare and double-quoted text
    available to SAS macro resolution."""
    protected = {}
    parts = []
    start = 0
    for match in _QUOTED_SPAN_RE.finditer(text):
        if not match.group(0).startswith("'"):
            continue
        placeholder = f"\x00sas_graph_single_quote_{len(protected)}\x00"
        parts.append(text[start:match.start()])
        parts.append(placeholder)
        protected[placeholder] = match.group(0)
        start = match.end()
    parts.append(text[start:])
    return "".join(parts), protected


def _mask_literal_residue(text):
    text = _LITERAL_SUFFIX_RE.sub(
        lambda match: " " * len(match.group(0)), text
    )
    return _SPECIAL_MISSING_RE.sub(
        lambda match: " " * len(match.group(0)), text
    )


def _top_level_matches(text, pattern, start=0):
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            match = pattern.match(text, index)
            if match:
                yield match


def _top_level_match(text, pattern, start=0):
    return next(_top_level_matches(text, pattern, start), None)


def _select_list_span(text):
    select_match = _SELECT_LIST_RE.search(text)
    if not select_match:
        return None

    from_match = _top_level_match(text, _FROM_CLAUSE_RE, select_match.end())
    if not from_match:
        return None
    list_end = from_match.start()
    while list_end > select_match.end() and text[list_end - 1].isspace():
        list_end -= 1
    return select_match.end(), list_end


def _split_top_level_commas_spans(text):
    """Like `_split_top_level_commas` but returns unstripped (start, end)
    spans -- a caller can slice a second, same-length string (the original
    unmasked statement text) at the identical positions to recover a masked
    column's real text (codex-review fix, see `_apply_select_columns`)."""
    spans = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            spans.append((start, index))
            start = index + 1
    spans.append((start, len(text)))
    return spans


def _split_top_level_commas(text):
    """Split on commas outside of parens, so `coalesce(a, b) as c` stays whole."""
    return [text[start:end].strip() for start, end in _split_top_level_commas_spans(text)]


def _insert_body(statement):
    match = _INSERT_INTO_RE.match(statement.text)
    if not match:
        return ""
    body = statement.text[match.end():].strip()
    return body[:-1].rstrip() if body.endswith(";") else body


def _parse_insert_target_columns(ctx, statement, sql_statement_id, raw_columns):
    if raw_columns is None:
        return None
    columns = [item for item in _split_top_level_commas(_mask_quoted(raw_columns)) if item]
    if not columns or any(not _BARE_COLUMN_RE.fullmatch(column) for column in columns):
        return None
    return columns


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
            target_columns_raw = match.group(2) if subtype == "INSERT_INTO" else None
            all_recovered &= _apply_statement(
                ctx, let_events, sql_block_id, statement, subtype, match.group(1),
                target_columns_raw,
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


def _apply_statement(
    ctx, let_events, sql_block_id, statement, subtype, target_raw,
    target_columns_raw=None,
):
    rule = f"proc_sql_{subtype.lower()}"
    target_id = _dataset_id(
        target_raw, statement, ctx, let_events, f"{rule}_target"
    )
    source_text = _mask_quoted(statement.text)
    # A JOIN with no FROM in the same statement is not a recoverable source
    # list (original `_FROM_RE`/`_JOIN_RE` behavior: JOIN names were only
    # ever appended once a FROM had already matched) -- so JOIN refs are only
    # collected inside the `from_match` branch below.
    table_refs = []  # (raw_name, alias)
    from_match = _top_level_match(source_text, _FROM_CLAUSE_RE)
    if from_match:
        from_end_match = _top_level_match(
            source_text, _FROM_CLAUSE_END_RE, from_match.end()
        )
        from_end = from_end_match.start() if from_end_match else len(source_text)
        from_text = source_text[from_match.end():from_end]
        join_matches = list(_top_level_matches(from_text, _JOIN_REF_RE))
        first_join = _top_level_match(from_text, _JOIN_BOUNDARY_RE)
        base_from_text = from_text[:first_join.start()] if first_join else from_text
        for item in _split_top_level_commas(base_from_text):
            if not item:
                continue
            item_match = _FROM_ITEM_RE.match(item)
            if item_match:
                table_refs.append((item_match.group(1), item_match.group(2)))
            else:
                ctx.add_finding(
                    "proc_sql_unsupported_syntax",
                    "NOT_EXECUTED",
                    "WARNING",
                    sql_block_id,
                    f"FROM source `{item}` is not a plain `name` or "
                    "`name AS alias` reference; it was not resolved.",
                    "Rewrite as a plain dataset reference to capture lineage.",
                    statement.as_source(f"{rule}_source"),
                )
        for join_match in join_matches:
            table_refs.append((join_match.group(1), join_match.group(2)))
    unique_raw_names = list(dict.fromkeys(raw for raw, _ in table_refs))
    raw_to_id = {
        raw: _dataset_id(raw, statement, ctx, let_events, f"{rule}_source")
        for raw in unique_raw_names
    }
    source_ids = [raw_to_id[raw] for raw in unique_raw_names]
    alias_map = {
        alias.lower(): raw_to_id[raw] for raw, alias in table_refs if alias
    }

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
            "reads_dataset", source_id, sql_statement_id,
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

    insert_target_columns = (
        _parse_insert_target_columns(
            ctx, statement, sql_statement_id, target_columns_raw
        )
        if subtype == "INSERT_INTO" else None
    )
    insert_body = _insert_body(statement) if subtype == "INSERT_INTO" else ""
    set_match = _INSERT_SET_RE.match(insert_body)
    if set_match:
        _apply_insert_set(
            ctx, statement, sql_statement_id, target_id, alias_map, source_ids,
            set_match.group(1),
        )
    elif (values_match := _INSERT_VALUES_RE.match(insert_body)):
        _apply_insert_values(
            ctx, statement, sql_statement_id, target_id, alias_map, source_ids,
            insert_target_columns, values_match.group(1),
        )
    else:
        _apply_select_columns(
            ctx, statement, sql_statement_id, target_id, alias_map, source_ids,
            let_events, insert_target_columns,
        )
    _apply_where_predicate(ctx, statement, sql_statement_id, alias_map, source_ids, source_text)
    _apply_column_list_clause(
        ctx, statement, sql_statement_id, alias_map, source_ids, source_text,
        _GROUP_BY_CLAUSE_RE, "proc_sql_group_by",
    )
    _apply_having_predicate(
        ctx, statement, sql_statement_id, alias_map, source_ids, source_text
    )
    _apply_column_list_clause(
        ctx, statement, sql_statement_id, alias_map, source_ids, source_text,
        _ORDER_BY_CLAUSE_RE, "proc_sql_order_by", strip_order_direction=True,
    )

    return bool(source_ids)


def _resolve_column_ref(ctx, statement, alias_map, source_ids, ref, unresolved_names=()):
    """Resolve one `SELECT`-list column reference to a source `Variable`.

    A qualified reference (`t1.var`) resolves through the statement's own
    alias table. An unqualified reference resolves only when the statement
    has exactly one FROM/JOIN source table -- more than one (or an unresolved
    qualifier/source) is ambiguous and becomes an `UnknownVariable` plus a
    finding, never a guess (wayfinder: sql-select-alias-variable-edges).
    """
    unresolved_ref = ref.strip()
    if unresolved_ref.startswith("&"):
        unresolved_ref = unresolved_ref[1:].rstrip(".")
    if unresolved_ref.lower() in unresolved_names:
        ref = unresolved_ref
        var_name = ref
        dataset_id = None
    elif "." in ref:
        qualifier, var_name = ref.split(".", 1)
        dataset_id = alias_map.get(qualifier.lower())
    else:
        var_name = ref
        dataset_id = source_ids[0] if len(source_ids) == 1 else None

    if dataset_id and dataset_id.startswith("dataset:"):
        return ctx.add_variable(dataset_id[len("dataset:"):], var_name)

    source = statement.as_source("unqualified_variable_reference")
    node_id = ctx.add_unknown_variable(ref, statement.statement_order, source)
    ctx.add_finding(
        "unqualified_variable_reference",
        "UNQUALIFIED_VARIABLE",
        "WARNING",
        node_id,
        f"Column `{ref}` could not be resolved to exactly one FROM/JOIN "
        "source in this statement.",
        "Qualify the column with its source table alias, or ensure the "
        "statement has a single source table.",
        source,
        affected_nodes=[node_id],
    )
    return node_id


_STRING_LITERAL_RE = re.compile(r"^(['\"])(.*)\1$", re.DOTALL)
_NUMERIC_LITERAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_MACRO_UNQUOTE_RE = re.compile(r"%unquote\s*\(\s*([^()]*)\s*\)", re.IGNORECASE)


def _literal_value(text):
    """The literal SAS text (quotes stripped) a fully-literal expression
    represents, or `None` when it is not one -- mirrors
    `rules_data_step._literal_value` (duplicated, not imported: rule modules
    never depend on each other)."""
    text = text.strip()
    match = _STRING_LITERAL_RE.match(text)
    if match:
        return match.group(2)
    if _NUMERIC_LITERAL_RE.match(text):
        return text
    return None


def _resolve_select_text(text, statement, let_events):
    masked_text, protected_literals = _protect_single_quoted(text)
    resolved, unresolved = resolve_text(
        masked_text, statement.statement_order, let_events, return_unresolved=True
    )
    if not unresolved:
        resolved = _MACRO_UNQUOTE_RE.sub(lambda match: match.group(1), resolved)
    for placeholder, literal in protected_literals.items():
        resolved = resolved.replace(placeholder, literal)
    return resolved, {name.lower() for name in unresolved}


def _predicate_edges(
    ctx, statement, sql_statement_id, alias_map, source_ids, condition_text, rule,
    unresolved_names=(),
):
    """One `column OP (literal|column)` comparison's `reads_variable` edges
    -- shared by `WHERE` (see `_apply_where_predicate`) and single-branch
    `CASE WHEN` (see `_apply_case_select_column`). Mirrors
    `rules_data_step._condition_edges`'s grammar, duplicated not imported
    (rule modules stay independent), but an unsupported shape here always
    produces a `proc_sql_unsupported_syntax` finding rather than the
    DATA-step version's silent fallthrough (wayfinder:
    sql-where-case-variable-edges). Returns whether the comparison was
    supported, so a CASE caller knows whether to still write its output
    variable."""
    match = _SQL_COMPARISON_RE.match(condition_text.strip())
    value = operator = rhs_ref = None
    if match:
        lhs, operator_token, rhs = match.group(1), match.group(2), match.group(3).strip()
        literal = _literal_value(rhs)
        if literal is not None:
            value, operator = literal, operator_token.upper()
        elif _BARE_COLUMN_RE.match(rhs):
            operator, rhs_ref = operator_token.upper(), rhs
        else:
            match = None

    if not match:
        ctx.add_finding(
            "proc_sql_unsupported_syntax",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            f"Condition `{condition_text.strip()}` is not a single `column "
            "OP literal|column` comparison; it was not parsed.",
            "Rewrite as a single comparison to capture variable-level lineage.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return False

    lhs_id = _resolve_column_ref(
        ctx, statement, alias_map, source_ids, lhs, unresolved_names
    )
    ctx.add_edge(
        "reads_variable", lhs_id, sql_statement_id, statement.as_source(rule),
        value=value, operator=operator,
    )
    if rhs_ref is not None:
        rhs_id = _resolve_column_ref(
            ctx, statement, alias_map, source_ids, rhs_ref, unresolved_names
        )
        ctx.add_edge(
            "reads_variable", rhs_id, sql_statement_id, statement.as_source(rule),
            value=None, operator=operator,
        )
    return True


def _top_level_clause_span(source_text, clause_pattern):
    clause_match = _top_level_match(source_text, clause_pattern)
    if not clause_match:
        return
    end_match = _top_level_match(
        source_text, _WHERE_CLAUSE_END_RE, clause_match.end()
    )
    end = end_match.start() if end_match else len(source_text)
    return clause_match.end(), end


def _apply_predicate_clause(
    ctx, statement, sql_statement_id, alias_map, source_ids, source_text,
    clause_pattern, rule,
):
    span = _top_level_clause_span(source_text, clause_pattern)
    if not span:
        return
    start, end = span
    condition_text = statement.text[start:end].strip()
    if not condition_text:
        return
    _predicate_edges(
        ctx, statement, sql_statement_id, alias_map, source_ids,
        condition_text, rule,
    )


def _apply_where_predicate(ctx, statement, sql_statement_id, alias_map, source_ids, source_text):
    _apply_predicate_clause(
        ctx, statement, sql_statement_id, alias_map, source_ids, source_text,
        _WHERE_CLAUSE_RE, "proc_sql_where_condition",
    )


def _apply_having_predicate(ctx, statement, sql_statement_id, alias_map, source_ids, source_text):
    _apply_predicate_clause(
        ctx, statement, sql_statement_id, alias_map, source_ids, source_text,
        _HAVING_CLAUSE_RE, "proc_sql_having_condition",
    )


def _apply_column_list_clause(
    ctx, statement, sql_statement_id, alias_map, source_ids, source_text,
    clause_pattern, rule, strip_order_direction=False,
):
    span = _top_level_clause_span(source_text, clause_pattern)
    if not span:
        return
    start, end = span
    clause_text = statement.text[start:end].strip()
    if not clause_text:
        return

    for item in _split_top_level_commas(clause_text):
        ref = _ORDER_BY_DIRECTION_RE.sub("", item).strip() if strip_order_direction else item
        if not _BARE_COLUMN_RE.fullmatch(ref):
            ctx.add_finding(
                "proc_sql_unsupported_syntax",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                f"{rule.replace('proc_sql_', '', 1).replace('_', ' ').upper()} item `"
                f"{item}` is not a bare column reference; it was not parsed.",
                "Rewrite the clause as a comma-separated list of columns to capture lineage.",
                statement.as_source(rule),
                affected_nodes=[sql_statement_id],
            )
            continue
        ref_id = _resolve_column_ref(
            ctx, statement, alias_map, source_ids, ref
        )
        ctx.add_edge(
            "reads_variable", ref_id, sql_statement_id, statement.as_source(rule),
            value=None, operator=None,
        )


_LITERAL_TOKEN_RE = r"(?:'[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?)"


def _bare_literal_expr_value(original_column, alias):
    """The literal SAS text a `select 'Y' as flag`-shaped ORIGINAL (unmasked)
    column represents, or `None` when its pre-alias part isn't a single
    literal token.

    Matched directly against the original text, not sliced from a masked
    string: a masked quote character reads as plain whitespace, so a regex
    can't reliably tell "the real separator before AS" apart from "the
    literal's own now-blanked characters" when the literal sits at the
    column's edge -- codex-review fix, an earlier span-slicing attempt at
    this recovered the wrong substring for exactly that reason. `alias` is
    already known from the masked-text split, so this only needs to confirm
    the pre-alias part is one literal token, not re-derive the alias too."""
    pattern = re.compile(
        rf"^\s*({_LITERAL_TOKEN_RE})\s+as\s+{re.escape(alias)}\s*$", re.IGNORECASE
    )
    match = pattern.match(original_column)
    if not match:
        return None
    return _literal_value(match.group(1))


def _split_select_alias(column):
    match = _COLUMN_ALIAS_RE.match(column)
    if match:
        return match.group(1).strip(), match.group(2)
    return column.strip(), None


def _extract_identifiers(expr):
    """Named variables referenced in a computed expression -- a function name
    (immediately followed by `(`) is not a variable and is skipped."""
    refs = []
    skip_calculated_ref = False
    for match in _IDENTIFIER_RE.finditer(expr):
        token, paren = match.group(1), match.group(2)
        if skip_calculated_ref:
            skip_calculated_ref = False
            continue
        if paren:
            continue
        base = token.rsplit(".", 1)[-1].lower()
        if base in _EXCLUDED_IDENTIFIER_WORDS:
            skip_calculated_ref = base == "calculated"
            continue
        refs.append(token)
    return refs


def _apply_expression_reads(
    ctx, statement, sql_statement_id, alias_map, source_ids, expr, rule,
    unresolved_names,
):
    refs = _extract_identifiers(expr)
    for ref in refs:
        read_id = _resolve_column_ref(
            ctx, statement, alias_map, source_ids, ref, unresolved_names
        )
        ctx.add_edge(
            "reads_variable", read_id, sql_statement_id, statement.as_source(rule),
            value=None, operator=None,
        )
    return refs


_INSERT_SET_ASSIGNMENT_RE = re.compile(
    r"^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*=\s*(.*?)\s*$",
    re.DOTALL,
)


def _apply_insert_set(
    ctx, statement, sql_statement_id, target_id, alias_map, source_ids,
    assignments_text,
):
    if not target_id.startswith("dataset:"):
        return
    target_raw = target_id[len("dataset:"):]
    rule = "proc_sql_insert_set"
    masked_assignments = _mask_quoted(assignments_text)
    for rel_start, rel_end in _split_top_level_commas_spans(masked_assignments):
        item = assignments_text[rel_start:rel_end].strip()
        match = _INSERT_SET_ASSIGNMENT_RE.match(item)
        if not match:
            ctx.add_finding(
                "proc_sql_unsupported_syntax",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                f"INSERT SET item `{item}` is not a `column=expression` pair; "
                "it was not parsed.",
                "Rewrite INSERT SET as comma-separated column=expression pairs.",
                statement.as_source(rule),
                affected_nodes=[sql_statement_id],
            )
            continue
        column, expression = match.group(1), match.group(2)
        literal = _literal_value(expression)
        if literal is None:
            _apply_expression_reads(
                ctx, statement, sql_statement_id, alias_map, source_ids,
                _mask_quoted(_mask_literal_residue(expression)), rule, (),
            )
        write_id = ctx.add_variable(target_raw, column)
        ctx.add_edge(
            "writes_variable", sql_statement_id, write_id, statement.as_source(rule),
            value=literal, operator=None,
        )


def _apply_insert_values(
    ctx, statement, sql_statement_id, target_id, alias_map, source_ids,
    target_columns, values_text,
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
            "INSERT VALUES requires an explicit target column list for "
            "positional mapping; it was not parsed.",
            "Add an explicit target column list to the INSERT statement.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return

    masked_values = _mask_quoted(values_text)
    value_spans = _split_top_level_commas_spans(masked_values)
    if len(value_spans) != len(target_columns):
        ctx.add_finding(
            "proc_sql_unsupported_syntax",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            "INSERT VALUES count does not match the explicit target column "
            "count; it was not parsed.",
            "Provide one VALUES expression for each target column.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return

    target_raw = target_id[len("dataset:"):]
    for column, (start, end) in zip(target_columns, value_spans):
        expression = values_text[start:end].strip()
        literal = _literal_value(expression)
        if literal is None:
            _apply_expression_reads(
                ctx, statement, sql_statement_id, alias_map, source_ids,
                _mask_quoted(_mask_literal_residue(expression)), rule, (),
            )
        write_id = ctx.add_variable(target_raw, column)
        ctx.add_edge(
            "writes_variable", sql_statement_id, write_id, statement.as_source(rule),
            value=literal, operator=None,
        )


def _apply_bare_select_column(
    ctx, statement, sql_statement_id, alias_map, source_ids, target_raw, expr, alias,
    write_name=None,
):
    rule = "proc_sql_select_column"
    read_id = _resolve_column_ref(ctx, statement, alias_map, source_ids, expr)
    ctx.add_edge(
        "reads_variable", read_id, sql_statement_id, statement.as_source(rule),
        value=None, operator=None,
    )
    write_name = write_name if write_name is not None else alias or expr.rsplit(".", 1)[-1]
    write_id = ctx.add_variable(target_raw, write_name)
    ctx.add_edge(
        "writes_variable", sql_statement_id, write_id, statement.as_source(rule),
        value=None, operator=None,
    )


def _apply_computed_select_column(
    ctx, statement, sql_statement_id, alias_map, source_ids, target_raw, expr, alias,
    original_column, unresolved_names, write_name=None,
):
    rule = "proc_sql_select_expression"
    if not alias:
        ctx.add_finding(
            "proc_sql_select_expression_unaliased",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            f"Computed SELECT expression `{expr}` has no alias; its output "
            "variable could not be named.",
            "Add an alias (`AS name`) to the computed expression.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return
    refs = _apply_expression_reads(
        ctx, statement, sql_statement_id, alias_map, source_ids, expr, rule,
        unresolved_names,
    )
    # codex-review fix: a bare literal (`select 'Y' as flag from ...`, zero
    # identifiers) is the SQL analogue of a DATA-step `flag = 'Y';` -- the
    # frozen contract's `value` field is "the literal being read/assigned/
    # compared, or null if the statement carries no literal", so it must be
    # captured here too, not left null just because this is the "computed"
    # path. Only checked when there are no identifiers: a real computed
    # expression (`x + 1 as y`) still carries no single literal value.
    literal = None if refs else _bare_literal_expr_value(original_column, alias)
    write_name = write_name if write_name is not None else alias
    write_id = ctx.add_variable(target_raw, write_name)
    ctx.add_edge(
        "writes_variable", sql_statement_id, write_id, statement.as_source(rule),
        value=literal, operator=None,
    )


def _case_simple_match(original_column, alias):
    """Matches the supported `CASE WHEN cond THEN val [ELSE val] END AS
    alias` shape directly against the ORIGINAL (unmasked) column text --
    same technique as `_bare_literal_expr_value`, for the same reason: a
    masked quote character reads as plain whitespace, so a literal
    comparand inside the WHEN condition can't be recovered by slicing a
    span out of the masked text. `alias` is already known from the
    masked-text split (caller already verified WHEN/THEN/ELSE occur at most
    once each), so this only needs to confirm the CASE...END shape."""
    pattern = re.compile(
        r"^\s*case\s+when\s+(.+?)\s+then\s+(.+?)"
        r"(?:\s+else\s+(.+?))?\s+end\s+as\s+"
        + re.escape(alias)
        + r"(?:\s+(?:length|format|label)\s*=\s*.*)?\s*$",
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.match(original_column)


def _apply_case_select_column(
    ctx, statement, sql_statement_id, alias_map, source_ids, target_raw, expr, alias,
    original_column, unresolved_names, write_name=None,
):
    rule = "proc_sql_select_case"
    if not alias:
        ctx.add_finding(
            "proc_sql_select_expression_unaliased",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            f"Computed SELECT expression `{expr}` has no alias; its output "
            "variable could not be named.",
            "Add an alias (`AS name`) to the computed expression.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return

    if (
        len(_WHEN_COUNT_RE.findall(expr)) != 1
        or len(_THEN_COUNT_RE.findall(expr)) != 1
        or len(_ELSE_COUNT_RE.findall(expr)) > 1
    ):
        ctx.add_finding(
            "proc_sql_unsupported_syntax",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            f"CASE expression `{expr}` has more than one WHEN/THEN branch, "
            "which is not parsed.",
            "Rewrite as a single WHEN...THEN[...ELSE...] CASE to capture "
            "variable-level lineage.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return

    match = _case_simple_match(original_column, alias)
    if not match:
        ctx.add_finding(
            "proc_sql_unsupported_syntax",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            f"CASE expression `{expr}` is not a single `CASE WHEN ... THEN "
            "... [ELSE ...] END AS alias` shape.",
            "Rewrite to the supported CASE shape to capture variable-level lineage.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return

    supported = _predicate_edges(
        ctx, statement, sql_statement_id, alias_map, source_ids, match.group(1), rule,
        unresolved_names,
    )
    if not supported:
        return

    for result_expr in (match.group(2), match.group(3)):
        if result_expr:
            result_expr = _mask_quoted(_mask_literal_residue(result_expr))
            _apply_expression_reads(
                ctx, statement, sql_statement_id, alias_map, source_ids,
                result_expr, rule, unresolved_names,
            )

    write_name = write_name if write_name is not None else alias
    write_id = ctx.add_variable(target_raw, write_name)
    ctx.add_edge(
        "writes_variable", sql_statement_id, write_id, statement.as_source(rule),
        value=None, operator=None,
    )


def _apply_select_columns(
    ctx, statement, sql_statement_id, target_id, alias_map, source_ids, let_events,
    target_columns=None,
):
    if not target_id.startswith("dataset:"):
        return
    target_raw = target_id[len("dataset:"):]

    original_text = statement.text
    masked_text = _mask_quoted(_mask_literal_residue(original_text))
    select_span = _select_list_span(masked_text)
    if not select_span:
        return

    # `original_list` is `original_text` sliced at the exact same offsets as
    # the masked match -- masking never changes string length, so the two
    # stay character-aligned through every further slice below.
    list_start, list_end = select_span
    masked_list = masked_text[list_start:list_end]
    original_list = original_text[list_start:list_end]
    # codex-review fix: `SELECT DISTINCT a.x, b.y FROM ...` -- `DISTINCT` (or
    # `ALL`) is a SELECT-list modifier, not part of the first column's
    # expression; strip it from both strings (same length) before splitting.
    distinct_match = _DISTINCT_RE.match(masked_list)
    if distinct_match:
        cut = distinct_match.end()
        masked_list, original_list = masked_list[cut:], original_list[cut:]

    column_index = 0
    for rel_start, rel_end in _split_top_level_commas_spans(masked_list):
        # Deliberately unstripped: a leading/trailing masked-whitespace count
        # from the masked string does NOT equal the real leading/trailing
        # whitespace in the original string when a literal touches the
        # column boundary (`'Y' as flag` masks to `    as flag` -- stripping
        # by the masked count would cut the literal's own characters out of
        # `original_column`, not just whitespace). `column` below is the only
        # stripped value, used solely for shape checks; every offset used to
        # slice `original_column` stays relative to the unstripped strings.
        masked_column = masked_list[rel_start:rel_end]
        original_column = original_list[rel_start:rel_end]
        column = masked_column.strip()
        if not column:
            continue
        write_name = (
            target_columns[column_index]
            if target_columns is not None and column_index < len(target_columns)
            else None
        )
        column_index += 1
        if column == "*" or re.fullmatch(r"[A-Za-z_]\w*\.\*", column):
            ctx.add_finding(
                "proc_sql_select_star_unsupported",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                "SELECT * does not enumerate source columns; no per-column "
                "variable lineage was inferred.",
                "List explicit columns to capture variable-level lineage.",
                statement.as_source("proc_sql_select_star_unsupported"),
                affected_nodes=[sql_statement_id],
            )
            continue
        if _N_LITERAL_RE.search(original_column):
            ctx.add_finding(
                "proc_sql_unsupported_syntax",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                f"SELECT expression `{original_column.strip()}` uses SAS "
                "name-literal syntax (`'name'n`), which is not parsed.",
                "Rewrite without a name literal to capture variable-level lineage.",
                statement.as_source("proc_sql_unsupported_syntax"),
                affected_nodes=[sql_statement_id],
            )
            continue
        resolved_column, unresolved_names = _resolve_select_text(
            original_column, statement, let_events
        )
        resolved_masked_column = _mask_quoted(_mask_literal_residue(resolved_column))
        expr, alias = _split_select_alias(resolved_masked_column)
        if _CASE_HEAD_RE.match(expr):
            _apply_case_select_column(
                ctx, statement, sql_statement_id, alias_map, source_ids, target_raw,
                expr, alias, resolved_column, unresolved_names, write_name,
            )
        elif (
            _BARE_COLUMN_RE.match(expr)
            and expr.rsplit(".", 1)[-1].lower() not in _EXCLUDED_IDENTIFIER_WORDS
        ):
            _apply_bare_select_column(
                ctx, statement, sql_statement_id, alias_map, source_ids, target_raw,
                expr, alias, write_name,
            )
        else:
            _apply_computed_select_column(
                ctx, statement, sql_statement_id, alias_map, source_ids, target_raw,
                expr, alias, resolved_column, unresolved_names, write_name,
            )
