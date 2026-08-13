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
from dataclasses import replace

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
_RENAME_NAME_TOKEN = r"(?:\w+|'(?:''|[^'])*'\s*n\b|\"(?:\"\"|[^\"])*\"\s*n\b)"
_RENAME_PAIR_RE = re.compile(
    rf"({_RENAME_NAME_TOKEN})\s*=\s*({_RENAME_NAME_TOKEN})",
    re.IGNORECASE,
)

# wayfinder: data-step-assignment-condition-variable-edges -- smallest
# supported grammar. Anchored at `^`, so a keyword-led statement (`rename
# x=y;`, `do i=1 to 10;`) never matches: the keyword itself isn't followed by
# `=`, only the identifier after it is. Anything outside this grammar
# (compound AND/OR conditions, IN/LIKE) falls through with no edge and no
# finding. Arrays, DO loops, and dynamic variable lists are handled by
# wayfinder: data-step-keep-drop-rename-variable-edges -- they get a
# deferred-construct finding instead (see `_emit_variable_edges`).
_ASSIGNMENT_RE = re.compile(r"^(&?[A-Za-z_]\w*\.?)\s*=\s*(.+?);?$")
_DO_HEAD_RE = re.compile(r"^do\b", re.IGNORECASE)
_ARRAY_DECL_RE = re.compile(
    r"^array\s+([A-Za-z_]\w*)\s*[\{\[\(]", re.IGNORECASE
)
_ARRAY_REF_RE = re.compile(
    r"(?P<name>[A-Za-z_]\w*)\s*(?P<delimiter>[\{\[\(])"
)
_IF_HEAD_RE = re.compile(r"^if\s+(.+?);?$", re.IGNORECASE)
_THEN_SPLIT_RE = re.compile(r"\bthen\b", re.IGNORECASE)
_ELSE_HEAD_RE = re.compile(r"^else\s+(.+?);?$", re.IGNORECASE)
_CALL_MISSING_RE = re.compile(r"^call\s+missing\s*\((.*?)\)\s*;?$", re.IGNORECASE)
_CALL_RE = re.compile(
    r"^call\s+([A-Za-z_]\w*)\s*\((.*)\)\s*;?$", re.IGNORECASE
)
_CALL_HEAD_RE = re.compile(r"^call\b", re.IGNORECASE)
_SELECT_RE = re.compile(r"^select\s*\(([^()]*)\)\s*;?$", re.IGNORECASE)
_SELECT_HEAD_RE = re.compile(r"^select\b", re.IGNORECASE)
_WHEN_RE = re.compile(r"^when\s*\(([^()]*)\)\s+(.+?);?$", re.IGNORECASE)
_OTHERWISE_RE = re.compile(r"^otherwise(?:\s+(.+?))?;?$", re.IGNORECASE)
_END_RE = re.compile(r"^end\s*;?$", re.IGNORECASE)
_SUM_RE = re.compile(
    r"^([A-Za-z_]\w*)\s*\+\s*(.+?[^;])\s*;?$", re.IGNORECASE
)
_SUM_HEAD_RE = re.compile(r"^[A-Za-z_]\w*\s*\+", re.IGNORECASE)
_TRAILING_OPERATOR_RE = re.compile(r"(?:[+\-*/=<>]|,)\s*$")
_INPUT_PUT_RE = re.compile(
    r"^(input|put)\b(?!\s*(?:\(|=|;|$))(.+?);?$", re.IGNORECASE
)
_INPUT_PUT_HEAD_RE = re.compile(
    r"^(input|put)\b(?!\s*(?:\(|=|;|$))", re.IGNORECASE
)
_WHERE_HEAD_RE = re.compile(r"^where\s+(.+?);?$", re.IGNORECASE)
_COMPARISON_RE = re.compile(
    r"^([A-Za-z_]\w*)\s*(=>|=<|~=|\^=|>=|<=|>|<|=|\beq\b|\bne\b|\bgt\b|\blt\b|\bge\b|\ble\b)\s*(.+)$",
    re.IGNORECASE,
)
_COMPARISON_OPERATOR_ALIASES = {"=>": ">=", "=<": "<="}
# Trailing `d`/`dt`/`t` (`'01JAN2020'd`, `'01JAN2020:00:00'dt`, `'09:00't`) is
# SAS date/time/datetime literal syntax, common for an imputed-date value --
# recognized here as a literal like any quoted string, or the same masking
# hazard codex found for SQL name-literals (`'name'n`) hits here too: the
# suffix letter survives `_mask_quoted` (it's outside the quotes) and would
# otherwise be picked up by `_RHS_IDENTIFIER_RE` as a stray bare identifier.
# Only covers the whole-RHS/whole-comparand case (the common one); a date
# literal nested inside a function call argument is a known, undocumented
# boundary, not attempted here.
_STRING_LITERAL_RE = re.compile(
    r"^(['\"])((?:(?!\1).|\1\1)*)\1(?:dt|d|t)?$",
    re.IGNORECASE | re.DOTALL,
)
_NUMERIC_LITERAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_BARE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")
# RHS variable reads: any bare identifier not immediately followed by `(` --
# `compute_flag()` in `anl01vs = compute_flag();` must not mint a read.
_RHS_IDENTIFIER_RE = re.compile(r"\b([A-Za-z_]\w*)\b(?!\s*[.(])")
_RHS_EXCLUDED_WORDS = {
    "not", "and", "or", "eq", "ne", "gt", "lt", "ge", "le",
    "_n_", "_error_", "of",
}
_NUMBERED_RANGE_RE = re.compile(
    r"\b([A-Za-z_]\w*?)(\d+)\s*-\s*\1(\d+)\b", re.IGNORECASE
)
_OF_ARGUMENT_RE = re.compile(r"\bof\b([^()]*)", re.IGNORECASE)
_NAME_LITERAL_RE = re.compile(
    r"(['\"])(?:(?!\1).|\1\1)*\1\s*n\b",
    re.IGNORECASE | re.DOTALL,
)
_LITERAL_SUFFIX_RE = re.compile(
    r"(['\"])(?:(?!\1).|\1\1)*\1(?:dt|d|t|x)\b",
    re.IGNORECASE | re.DOTALL,
)
_SPECIAL_MISSING_RE = re.compile(r"(?<![\w.])\.[A-Za-z]\b", re.IGNORECASE)
# codex-review fix (sql-select-alias-variable-edges review pass, applied here
# too): identifiers must be extracted from a quote-masked RHS, or a literal
# string argument inside a function call (`ifc(test='Y', 'yes', 'no')`) gets
# misread as bare variable names `Y`/`yes`/`no`.
# Dataset options: `sdtm.ae (where=(aeser="Y"))` -- stripped before splitting
# on whitespace so `_resolve_dataset_list` never treats an option group as a
# dataset name of its own.
_DATASET_OPTIONS_RE = re.compile(r"\([^()]*\)")
_MACRO_UNQUOTE_RE = re.compile(r"%unquote\s*\(\s*([^()]*)\s*\)", re.IGNORECASE)
_QUOTED_SPAN_RE = re.compile(
    r'"(?:""|[^"])*(?:"|$)|\'(?:\'\'|[^\'])*(?:\'|$)', re.DOTALL
)

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


def _single_dataset_binding(output_ids):
    """The raw `libref.member` name a DATA step's bare variable references
    bind to, or `None` when there is no single determinable owner (zero or
    multiple output targets, or a target that itself failed to resolve to a
    real `Dataset`) -- wayfinder: specify-variable-node-and-edge-contract's
    `UnknownVariable` posture applied to DATA-step binding."""
    if len(output_ids) != 1:
        return None
    only = output_ids[0]
    if not only.startswith("dataset:"):
        return None
    return only[len("dataset:"):]


def _bind_variable(raw_name, binding, statement_order, source, ctx, unresolved_names=()):
    raw_name = raw_name.strip()
    if raw_name.startswith("&"):
        raw_name = raw_name[1:].rstrip(".")
    if raw_name.lower() in unresolved_names:
        binding = None
    if binding is not None:
        return ctx.add_variable(binding, raw_name)
    node_id = ctx.add_unknown_variable(raw_name, statement_order, source)
    ctx.add_finding(
        "unqualified_variable_reference",
        "UNQUALIFIED_VARIABLE",
        "WARNING",
        raw_name,
        f"`{raw_name}` has no single determinable owning dataset for this DATA step.",
        "Split the step so each output target has unambiguous variable "
        "references, or confirm the ambiguity is intentional.",
        source,
        affected_nodes=[node_id],
    )
    return node_id


def _resolve_variable_text(text, statement, let_events):
    masked_text, protected_literals = _protect_single_quoted(text)
    resolved, unresolved = resolve_text(
        masked_text, statement.statement_order, let_events, return_unresolved=True
    )
    if not unresolved:
        resolved = _MACRO_UNQUOTE_RE.sub(lambda match: match.group(1), resolved)
    for placeholder, literal in protected_literals.items():
        resolved = resolved.replace(placeholder, literal)
    return resolved, {name.lower() for name in unresolved}


def _literal_value(text):
    """The literal SAS text (quotes stripped) a fully-literal RHS/comparand
    represents, or `None` when it is a computed expression or a reference."""
    text = text.strip()
    match = _STRING_LITERAL_RE.match(text)
    if match:
        return match.group(2)
    if _NUMERIC_LITERAL_RE.match(text):
        return text
    return None


def _mask_quoted(text):
    """Blank out quoted-string contents so identifier extraction never reads
    a literal's text as a variable name (mirrors `rules_proc_sql._mask_quoted`
    -- duplicated, not imported: rule modules never depend on each other,
    CLAUDE.md's rule-module isolation)."""
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


def _is_array_reference(text, array_names):
    for match in _ARRAY_REF_RE.finditer(_mask_quoted(text)):
        if (
            match.group("delimiter") == "{"
            or match.group("name").lower() in array_names
        ):
            return True
    return False


def _split_top_level_commas(text):
    masked = _mask_quoted(text)
    depth = 0
    start = 0
    arguments = []
    for index, char in enumerate(masked):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
        elif char == "," and depth == 0:
            arguments.append(text[start:index].strip())
            start = index + 1
    if depth != 0:
        return None
    arguments.append(text[start:].strip())
    return arguments


def _rhs_identifiers(text):
    name_literals = {}

    def preserve_name_literal(match):
        token = f"__sas_name_literal_{len(name_literals)}__"
        name_literals[token] = match.group(0).strip()
        return token

    text = _NAME_LITERAL_RE.sub(preserve_name_literal, text)
    text = _LITERAL_SUFFIX_RE.sub(
        lambda match: " " * len(match.group(0)), text
    )
    masked = _OF_ARGUMENT_RE.sub(
        lambda match: match.group(0).replace(
            match.group(1),
            _NUMBERED_RANGE_RE.sub(
                lambda range_match: _expand_numbered_range(range_match),
                match.group(1),
            ),
            1,
        ),
        _mask_quoted(text),
    )
    masked = _SPECIAL_MISSING_RE.sub(
        lambda match: " " * len(match.group(0)), masked
    )
    refs = []
    for name in _RHS_IDENTIFIER_RE.findall(masked):
        name = name_literals.get(name, name)
        if name.lower() not in _RHS_EXCLUDED_WORDS:
            refs.append(name)
    return list(dict.fromkeys(refs))


def _expand_numbered_range(match):
    prefix, start_text, end_text = match.groups()
    start, end = int(start_text), int(end_text)
    step = 1 if end >= start else -1
    width = max(len(start_text), len(end_text)) if (
        start_text.startswith("0") or end_text.startswith("0")
    ) else 0
    return " ".join(
        f"{prefix}{number:0{width}d}" if width else f"{prefix}{number}"
        for number in range(start, end + step, step)
    )


def _emit_assignment(statement, ctx, step_id, binding, let_events):
    resolved_text, unresolved_names = _resolve_variable_text(
        statement.text, statement, let_events
    )
    match = _ASSIGNMENT_RE.match(resolved_text)
    if not match:
        return
    target, rhs = match.group(1), match.group(2).strip()
    source = statement.as_source("data_step_assignment")
    literal = _literal_value(rhs)
    if literal is not None:
        value, reads = literal, []
    else:
        value, reads = None, _rhs_identifiers(rhs)

    for name in reads:
        var_id = _bind_variable(
            name, binding, statement.statement_order, source, ctx, unresolved_names
        )
        ctx.add_edge("reads_variable", var_id, step_id, source, value=None, operator=None)

    target_id = _bind_variable(
        target, binding, statement.statement_order, source, ctx, unresolved_names
    )
    ctx.add_edge("writes_variable", step_id, target_id, source, value=value, operator=None)


def _condition_edges(condition_text, rule, statement, ctx, step_id, binding):
    """Section spec's "smallest supported grammar": a single `var OP
    (literal|var)` comparison. Anything else (AND/OR, IN, LIKE, functions)
    falls through with no edge -- never a guessed partial capture."""
    match = _COMPARISON_RE.match(condition_text.strip())
    if not match:
        return
    lhs, operator_token, rhs = match.group(1), match.group(2), match.group(3).strip()
    operator_token = _COMPARISON_OPERATOR_ALIASES.get(operator_token, operator_token)
    source = statement.as_source(rule)
    literal = _literal_value(rhs)
    if literal is not None:
        value, operator, rhs_var = literal, operator_token.upper(), None
    elif _BARE_IDENTIFIER_RE.match(rhs):
        # codex-review fix: `operator` records that a comparison happened,
        # independent of whether the comparand is a literal or a variable --
        # the frozen contract's own wording is "null when there is no
        # comparison" (specify-variable-node-and-edge-contract.md), and
        # `if left = right` is one. Both sides carry the same operator; only
        # `value` (the literal, if any) stays null here since neither side is
        # a literal.
        value, operator, rhs_var = None, operator_token.upper(), rhs
    else:
        return

    lhs_id = _bind_variable(lhs, binding, statement.statement_order, source, ctx)
    ctx.add_edge("reads_variable", lhs_id, step_id, source, value=value, operator=operator)
    if rhs_var is not None:
        rhs_id = _bind_variable(rhs_var, binding, statement.statement_order, source, ctx)
        ctx.add_edge("reads_variable", rhs_id, step_id, source, value=None, operator=operator)


def _emit_deferred_construct_finding(statement, ctx, step_id, construct):
    """wayfinder: data-step-keep-drop-rename-variable-edges -- arrays, DO
    loops, and dynamic variable lists (`&macrovar.`, numbered ranges) are
    out of the supported grammar. Each occurrence gets a finding instead of
    a guessed edge, never a silent drop."""
    source = statement.as_source("data_step_deferred_variable_construct")
    ctx.add_finding(
        "deferred_variable_construct",
        "NOT_EXECUTED",
        "WARNING",
        statement.original_text.strip(),
        f"{construct} is outside the supported variable-edge grammar; no "
        "variable edge was inferred for this statement.",
        "Review manually if variable-level lineage through this statement matters.",
        source,
        affected_nodes=[step_id],
    )


def _emit_keep_or_drop(list_text, edge_type, rule, statement, ctx, step_id, binding):
    tokens = list_text.split()
    if not tokens or any(not _BARE_IDENTIFIER_RE.match(token) for token in tokens):
        _emit_deferred_construct_finding(statement, ctx, step_id, "Dynamic variable list")
        return
    source = statement.as_source(rule)
    for name in tokens:
        var_id = _bind_variable(name, binding, statement.statement_order, source, ctx)
        if edge_type == "writes_variable":
            ctx.add_edge("writes_variable", step_id, var_id, source, value=None, operator=None)
        else:
            ctx.add_edge("reads_variable", var_id, step_id, source, value=None, operator=None)


def _emit_rename(pair_text, statement, ctx, step_id, binding):
    if "&" in pair_text or "{" in pair_text:
        _emit_deferred_construct_finding(statement, ctx, step_id, "Dynamic variable list")
        return
    pairs = _RENAME_PAIR_RE.findall(pair_text)
    if not pairs:
        return
    source = statement.as_source("data_step_rename")
    for old_name, new_name in pairs:
        old_id = _bind_variable(old_name, binding, statement.statement_order, source, ctx)
        ctx.add_edge("reads_variable", old_id, step_id, source, value=None, operator=None)
        new_id = _bind_variable(new_name, binding, statement.statement_order, source, ctx)
        ctx.add_edge("writes_variable", step_id, new_id, source, value=None, operator=None)


def _emit_action(action, statement, ctx, step_id, binding, let_events):
    action = action.strip()
    if action:
        _emit_variable_edges(
            [replace(statement, text=action)], ctx, step_id, binding, let_events,
        )


def _emit_call_missing(argument_text, statement, ctx, step_id, binding):
    names = [argument.strip() for argument in argument_text.split(",")]
    if not names or any(not _BARE_IDENTIFIER_RE.match(name) for name in names):
        _emit_deferred_construct_finding(statement, ctx, step_id, "Dynamic variable list")
        return
    source = statement.as_source("data_step_call_missing")
    for name in names:
        var_id = _bind_variable(name, binding, statement.statement_order, source, ctx)
        ctx.add_edge("writes_variable", step_id, var_id, source, value=None, operator=None)


def _emit_call(routine, argument_text, statement, ctx, step_id, binding):
    routine = routine.lower()
    arguments = _split_top_level_commas(argument_text)
    if arguments is None or not arguments or any(not argument for argument in arguments):
        _emit_deferred_construct_finding(statement, ctx, step_id, "CALL routine")
        return

    if routine in {"symput", "symputx"}:
        supported = (
            len(arguments) == 2
            and _STRING_LITERAL_RE.fullmatch(arguments[0])
        )
        expression = arguments[1] if supported else ""
    elif routine == "execute":
        supported = len(arguments) == 1
        expression = arguments[0] if supported else ""
    else:
        supported = False
        expression = ""

    if not supported or "&" in _mask_quoted(expression):
        _emit_deferred_construct_finding(statement, ctx, step_id, "CALL routine")
        return

    source = statement.as_source(f"data_step_call_{routine}")
    for name in _rhs_identifiers(expression):
        var_id = _bind_variable(name, binding, statement.statement_order, source, ctx)
        ctx.add_edge("reads_variable", var_id, step_id, source, value=None, operator=None)


def _emit_sum_statement(lhs, rhs, statement, ctx, step_id, binding):
    masked_rhs = _mask_quoted(rhs)
    if "&" in masked_rhs or _TRAILING_OPERATOR_RE.search(masked_rhs):
        _emit_deferred_construct_finding(statement, ctx, step_id, "SUM statement")
        return
    source = statement.as_source("data_step_sum_statement")
    for name in dict.fromkeys([lhs, *_rhs_identifiers(rhs)]):
        var_id = _bind_variable(name, binding, statement.statement_order, source, ctx)
        ctx.add_edge("reads_variable", var_id, step_id, source, value=None, operator=None)
    target_id = _bind_variable(lhs, binding, statement.statement_order, source, ctx)
    ctx.add_edge("writes_variable", step_id, target_id, source, value=None, operator=None)


def _emit_input_or_put(kind, body, statement, ctx, step_id, binding):
    body = body.strip()
    masked = _mask_quoted(body)
    if (
        not body
        or "&" in masked
        or re.search(r"\b_all_\b", masked, re.IGNORECASE)
    ):
        _emit_deferred_construct_finding(statement, ctx, step_id, f"{kind.upper()} statement")
        return

    names = _rhs_identifiers(body)
    if kind == "input" and not names:
        _emit_deferred_construct_finding(statement, ctx, step_id, "INPUT statement")
        return
    if kind == "put" and not names and not _STRING_LITERAL_RE.fullmatch(body):
        _emit_deferred_construct_finding(statement, ctx, step_id, "PUT statement")
        return

    source = statement.as_source(f"data_step_{kind}")
    edge_type = "writes_variable" if kind == "input" else "reads_variable"
    for name in names:
        var_id = _bind_variable(name, binding, statement.statement_order, source, ctx)
        if edge_type == "writes_variable":
            ctx.add_edge(edge_type, step_id, var_id, source, value=None, operator=None)
        else:
            ctx.add_edge(edge_type, var_id, step_id, source, value=None, operator=None)


def _emit_variable_edges(statements, ctx, step_id, binding, let_events):
    """One DATA step's variable edges, in exact statement order (wayfinder:
    data-step-assignment-condition-variable-edges,
    data-step-keep-drop-rename-variable-edges). Arrays, DO loops, and
    dynamic variable lists never match a supported statement shape, so they
    get a deferred-construct finding instead of a guessed edge."""
    array_names = set()
    select_active = False
    select_do_depth = 0
    for statement in statements:
        text = statement.text.strip()
        array_declaration = _ARRAY_DECL_RE.match(text)
        if array_declaration:
            array_names.add(array_declaration.group(1).lower())

        # codex-review fix: quote-masked first, or a literal string that
        # merely *contains* `word{` (e.g. `x = "not_an_array{";`) falsely
        # classifies an ordinary assignment as an array reference and drops
        # its real write edge -- same masking-boundary class already fixed
        # in rules_proc_sql.py.
        if _is_array_reference(text, array_names):
            _emit_deferred_construct_finding(statement, ctx, step_id, "Array reference")
            continue

        if select_active:
            if _DO_HEAD_RE.match(text):
                select_do_depth += 1
                _emit_deferred_construct_finding(statement, ctx, step_id, "DO loop")
                continue
            if _END_RE.match(text):
                if select_do_depth:
                    select_do_depth -= 1
                    continue
                select_active = False
                continue
            if select_do_depth:
                _emit_deferred_construct_finding(statement, ctx, step_id, "SELECT branch")
                continue
            when_match = _WHEN_RE.match(text)
            if when_match:
                action = when_match.group(2)
                if _DO_HEAD_RE.match(action.strip()):
                    select_do_depth += 1
                if _literal_value(when_match.group(1).strip()) is None:
                    _emit_deferred_construct_finding(statement, ctx, step_id, "SELECT branch")
                else:
                    _emit_action(action, statement, ctx, step_id, binding, let_events)
                continue
            otherwise_match = _OTHERWISE_RE.match(text)
            if otherwise_match:
                if otherwise_match.group(1):
                    action = otherwise_match.group(1)
                    if _DO_HEAD_RE.match(action.strip()):
                        select_do_depth += 1
                    _emit_action(action, statement, ctx, step_id, binding, let_events)
                else:
                    _emit_deferred_construct_finding(statement, ctx, step_id, "SELECT branch")
                continue
            _emit_deferred_construct_finding(statement, ctx, step_id, "SELECT branch")
            continue

        if _DO_HEAD_RE.match(text):
            _emit_deferred_construct_finding(statement, ctx, step_id, "DO loop")
            continue

        select_match = _SELECT_RE.match(text)
        if select_match:
            expression = select_match.group(1).strip()
            if not _BARE_IDENTIFIER_RE.match(expression):
                _emit_deferred_construct_finding(statement, ctx, step_id, "SELECT expression")
            else:
                source = statement.as_source("data_step_select_expression")
                var_id = _bind_variable(
                    expression, binding, statement.statement_order, source, ctx,
                )
                ctx.add_edge(
                    "reads_variable", var_id, step_id, source,
                    value=None, operator=None,
                )
            select_active = True
            select_do_depth = 0
            continue
        if _SELECT_HEAD_RE.match(text):
            _emit_deferred_construct_finding(statement, ctx, step_id, "SELECT statement")
            select_active = True
            select_do_depth = 0
            continue

        keep_match = _KEEP_RE.match(text)
        if keep_match:
            _emit_keep_or_drop(
                keep_match.group(1), "writes_variable", "data_step_keep",
                statement, ctx, step_id, binding,
            )
            continue
        drop_match = _DROP_RE.match(text)
        if drop_match:
            _emit_keep_or_drop(
                drop_match.group(1), "reads_variable", "data_step_drop",
                statement, ctx, step_id, binding,
            )
            continue
        rename_match = _RENAME_RE.match(text)
        if rename_match:
            _emit_rename(rename_match.group(1), statement, ctx, step_id, binding)
            continue

        call_missing_match = _CALL_MISSING_RE.match(text)
        if call_missing_match:
            _emit_call_missing(
                call_missing_match.group(1), statement, ctx, step_id, binding,
            )
            continue
        call_match = _CALL_RE.match(text)
        if call_match:
            _emit_call(
                call_match.group(1), call_match.group(2),
                statement, ctx, step_id, binding,
            )
            continue
        if _CALL_HEAD_RE.match(text):
            _emit_deferred_construct_finding(statement, ctx, step_id, "CALL routine")
            continue

        sum_match = _SUM_RE.match(text)
        if sum_match:
            _emit_sum_statement(
                sum_match.group(1), sum_match.group(2),
                statement, ctx, step_id, binding,
            )
            continue
        if _SUM_HEAD_RE.match(text):
            _emit_deferred_construct_finding(statement, ctx, step_id, "SUM statement")
            continue

        input_put_match = _INPUT_PUT_RE.match(text)
        if input_put_match:
            _emit_input_or_put(
                input_put_match.group(1).lower(), input_put_match.group(2),
                statement, ctx, step_id, binding,
            )
            continue
        if _INPUT_PUT_HEAD_RE.match(text) and not re.match(r"^(?:input|put)\s*\(", text, re.IGNORECASE):
            _emit_deferred_construct_finding(statement, ctx, step_id, "INPUT/PUT statement")
            continue

        if_match = _IF_HEAD_RE.match(text)
        if if_match:
            parts = _THEN_SPLIT_RE.split(if_match.group(1), maxsplit=1)
            condition = parts[0].strip()
            _condition_edges(condition, "data_step_if_condition", statement, ctx, step_id, binding)
            if len(parts) > 1:
                _emit_action(parts[1], statement, ctx, step_id, binding, let_events)
            continue
        else_match = _ELSE_HEAD_RE.match(text)
        if else_match:
            _emit_action(else_match.group(1), statement, ctx, step_id, binding, let_events)
            continue
        where_match = _WHERE_HEAD_RE.match(text)
        if where_match:
            _condition_edges(where_match.group(1), "data_step_where_condition", statement, ctx, step_id, binding)
            continue
        _emit_assignment(statement, ctx, step_id, binding, let_events)


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
        ctx.add_edge("reads_dataset", input_id, step_id, input_source)

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
    _emit_variable_edges(
        block.statements[1:], ctx, step_id, _single_dataset_binding(output_ids), let_events
    )

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
                    "reads_dataset", input_id, step_id, statement.as_source("data_step_set")
                )

    if re.search(r"call\s+symputx\s*\(", " ".join(s.text for s in block.statements), re.IGNORECASE):
        node["patterns"].append("RUNTIME_MACRO_VARIABLE_CREATION")
        node["usable_for_static_resolution"] = False

    # DATA _NULL_ writes no Dataset, so every variable reference here is
    # unbound by construction (section _single_dataset_binding's own
    # zero-output case).
    _emit_variable_edges(block.statements[1:], ctx, step_id, None, let_events)


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
