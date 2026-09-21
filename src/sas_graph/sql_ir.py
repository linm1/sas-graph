"""Parse, resolve, and emit the deliberately small PROC SQL IR.

The PROC SQL rule used to parse SELECT lists and clauses while it emitted the
graph.  This module keeps those concerns separate:

``parse_sql``
    recognizes source text and produces unresolved IR objects;
``resolve_sql``
    binds qualified and unqualified column references against the statement's
    source aliases without choosing an arbitrary source;
``emit_sql``
    consumes the resolved IR and mutates a :class:`GraphContext`.

The parser is intentionally conservative.  Expressions that are outside the
small grammar retain their source text and variable references in
``IRUnknownExpression``.  The emitter therefore keeps the legacy variable
edges while still reporting constructs for which no safe semantic edge can be
made.  No regular-expression parsing is performed by the emitter.
"""

import re
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from .evidence import EvidenceKind, ResolutionStatus
from .ir import (
    IRComparison,
    IRExpression,
    IRLiteral,
    IRUnknownExpression,
    IRVariableRef,
    SourceSpan,
    iter_variable_refs,
)
from .macro_state import resolve_text


# ---------------------------------------------------------------------------
# SQL-specific IR containers.  Expressions deliberately reuse ir.py's types.


@dataclass(frozen=True)
class IRSqlSource:
    """One FROM/JOIN source and its optional explicit ``AS`` alias."""

    raw_name: str
    alias: Optional[str] = None
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRCaseExpression:
    """The one-branch CASE shape supported by the PROC SQL IR."""

    condition: IRExpression
    then_expression: IRExpression
    else_expression: Optional[IRExpression] = None
    source_span: Optional[SourceSpan] = None
    supported: bool = True


@dataclass(frozen=True)
class IRSqlSelect:
    """One SELECT item, including its output alias and original source text."""

    expression: IRExpression
    alias: Optional[str]
    expression_text: str
    original_text: str
    source_span: Optional[SourceSpan] = None
    kind: str = "expression"
    unresolved_names: Tuple[str, ...] = ()
    macro_resolved_names: Tuple[str, ...] = ()
    literal_value: Optional[str] = None


@dataclass(frozen=True)
class IRSqlJoin:
    """A JOIN source, preserving explicit kind and the ON predicate."""

    source: IRSqlSource
    kind: Optional[str]
    predicate: Optional[IRExpression]
    predicate_text: str = ""
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRSqlOrder:
    expression: IRExpression
    direction: Optional[str] = None
    original_text: str = ""
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRSqlDiagnostic:
    """A parse-stage diagnostic emitted by the owning rule module."""

    code: str
    message: str
    suggested_action: str
    source_suffix: str = "_source"


@dataclass(frozen=True)
class IRSqlStatement:
    """Unresolved PROC SQL statement IR."""

    subtype: str
    target_raw: str
    select_items: Tuple[IRSqlSelect, ...]
    sources: Tuple[IRSqlSource, ...]
    joins: Tuple[IRSqlJoin, ...]
    where: Optional[IRExpression]
    having: Optional[IRExpression]
    group_by: Tuple[IRExpression, ...]
    order_by: Tuple[IRSqlOrder, ...]
    source_span: SourceSpan
    diagnostics: Tuple[IRSqlDiagnostic, ...] = ()


@dataclass(frozen=True)
class IRSqlBinding:
    """Resolver output for one column reference.

    ``exact`` is true only for a concrete Dataset source.  For an unqualified
    reference in a multi-source statement, ``candidates`` contains every
    source id and ``exact`` remains false.  This is the important no-guessing
    boundary for B2.
    """

    reference: IRVariableRef
    variable_name: str
    dataset_id: Optional[str]
    candidates: Tuple[str, ...] = ()
    exact: bool = False
    macro_resolved: bool = False


@dataclass(frozen=True)
class IRSqlResolvedExpression:
    expression: IRExpression
    bindings: Tuple[IRSqlBinding, ...]


@dataclass(frozen=True)
class IRSqlResolvedSelect:
    item: IRSqlSelect
    expression: IRSqlResolvedExpression


@dataclass(frozen=True)
class IRSqlResolvedJoin:
    join: IRSqlJoin
    predicate: Optional[IRSqlResolvedExpression]


@dataclass(frozen=True)
class IRSqlResolvedOrder:
    order: IRSqlOrder
    expression: IRSqlResolvedExpression


@dataclass(frozen=True)
class IRSqlResolvedStatement:
    """Resolved IR consumed by :func:`emit_sql`."""

    statement: IRSqlStatement
    select_items: Tuple[IRSqlResolvedSelect, ...]
    joins: Tuple[IRSqlResolvedJoin, ...]
    where: Optional[IRSqlResolvedExpression]
    having: Optional[IRSqlResolvedExpression]
    group_by: Tuple[IRSqlResolvedExpression, ...]
    order_by: Tuple[IRSqlResolvedOrder, ...]
    source_ids: Tuple[str, ...]
    alias_map: Mapping[str, str]


# ---------------------------------------------------------------------------
# Parse helpers.  These are the only functions in this module that inspect
# source text with regexes.  The emitters below consume IR objects only.


_FROM_CLAUSE_RE = re.compile(r"\bfrom\b", re.IGNORECASE)
_FROM_CLAUSE_END_RE = re.compile(
    r"\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\bhaving\b|;|$",
    re.IGNORECASE,
)
_JOIN_BOUNDARY_RE = re.compile(
    r"(?:(?:inner|left|right|full|cross|outer)(?:\s+outer)?\s+)?\bjoin\b",
    re.IGNORECASE,
)
_ON_RE = re.compile(r"\bon\b", re.IGNORECASE)
_FROM_ITEM_RE = re.compile(
    r"^([^\s,;()]+)(?:\s+as\s+([A-Za-z_]\w*))?$", re.IGNORECASE
)
_SELECT_LIST_RE = re.compile(r"\bselect\s+?", re.IGNORECASE)
_DISTINCT_RE = re.compile(r"^\s*(?:distinct|all|unique)\s+", re.IGNORECASE)
_COLUMN_ALIAS_RE = re.compile(
    r"^(.*?)\s*\bas\s+([A-Za-z_]\w*)"
    r"(?:\s+(?:length|format|label)\s*=\s*.*)?$",
    re.IGNORECASE | re.DOTALL,
)
_BARE_COLUMN_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$")
_IDENTIFIER_RE = re.compile(
    r"(?<!\.)\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\b\s*(\()?(?!\s*\.)"
)
_N_LITERAL_RE = re.compile(r"['\"][^'\"]*['\"]\s*n\b", re.IGNORECASE)
_LITERAL_SUFFIX_RE = re.compile(
    r"(['\"])(?:(?!\1).|\1\1)*\1(?:dt|d|t|x)\b",
    re.IGNORECASE | re.DOTALL,
)
_SPECIAL_MISSING_RE = re.compile(r"(?<![\w.])\.[A-Za-z]\b", re.IGNORECASE)
_QUOTED_SPAN_RE = re.compile(
    r'"(?:""|[^"])*(?:"|$)|\'(?:\'\'|[^\'])*(?:\'|$)', re.DOTALL
)
_WHERE_CLAUSE_RE = re.compile(r"\bwhere\b", re.IGNORECASE)
_GROUP_BY_CLAUSE_RE = re.compile(r"\bgroup\s+by\b", re.IGNORECASE)
_HAVING_CLAUSE_RE = re.compile(r"\bhaving\b", re.IGNORECASE)
_ORDER_BY_CLAUSE_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_CLAUSE_END_RE = re.compile(
    r"\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\bhaving\b|;|$",
    re.IGNORECASE,
)
_ORDER_BY_DIRECTION_RE = re.compile(
    r"\s+(?:asc|desc)(?:\s+nulls\s+(?:first|last))?\s*$",
    re.IGNORECASE,
)
_SQL_COMPARISON_RE = re.compile(
    r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*"
    r"(<>|>=|<=|~=|\^=|=|>|<|\beq\b|\bne\b|\bgt\b|\blt\b|\bge\b|\ble\b)\s*(.+)$",
    re.IGNORECASE,
)
_CASE_HEAD_RE = re.compile(r"^case\b", re.IGNORECASE)
_CASE_SHAPE_RE = re.compile(
    r"^\s*case\s+when\s+(.+?)\s+then\s+(.+?)"
    r"(?:\s+else\s+(.+?))?\s+end\s*$",
    re.IGNORECASE | re.DOTALL,
)
_WHEN_COUNT_RE = re.compile(r"\bwhen\b", re.IGNORECASE)
_THEN_COUNT_RE = re.compile(r"\bthen\b", re.IGNORECASE)
_ELSE_COUNT_RE = re.compile(r"\belse\b", re.IGNORECASE)
_MACRO_REF_RE = re.compile(r"&[A-Za-z_]\w*\.?", re.IGNORECASE)
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*(?:'|$)", re.DOTALL)
_STRING_LITERAL_RE = re.compile(r"^(['\"])(.*)\1$", re.DOTALL)
_NUMERIC_LITERAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_LITERAL_TOKEN_RE = r"(?:'[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?)"
_EXCLUDED_IDENTIFIER_WORDS = {
    "and", "or", "not", "case", "when", "then", "else", "end",
    "distinct", "all", "unique", "calculated", "user", "null",
}
_UNKNOWN_ID_PREFIXES = ("unknowndataset:", "unknownvariable:", "unknownmacro:")


def source_span(source, original_text=None):
    """Convert a statement source mapping into a dependency-free IR span."""

    if isinstance(source, SourceSpan):
        if original_text is None or source.original_text == original_text:
            return source
        return SourceSpan(
            file=source.file,
            line_start=source.line_start,
            line_end=source.line_end,
            statement_order=source.statement_order,
            original_text=original_text,
        )
    if original_text is None:
        original_text = source.get("original_text", "")
    return SourceSpan(
        file=source.get("file", ""),
        line_start=source.get("line_start", 0),
        line_end=source.get("line_end", 0),
        statement_order=source.get("statement_order", 0),
        original_text=original_text,
    )


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
    """Hide single-quoted literals while leaving double-quoted text resolvable."""

    protected = {}
    parts = []
    start = 0
    for match in _QUOTED_SPAN_RE.finditer(text):
        if not match.group(0).startswith("'"):
            continue
        placeholder = "\x00sas_graph_single_quote_{}\x00".format(len(protected))
        parts.append(text[start:match.start()])
        parts.append(placeholder)
        protected[placeholder] = match.group(0)
        start = match.end()
    parts.append(text[start:])
    return "".join(parts), protected


def _mask_literal_residue(text):
    text = _LITERAL_SUFFIX_RE.sub(lambda match: " " * len(match.group(0)), text)
    return _SPECIAL_MISSING_RE.sub(lambda match: " " * len(match.group(0)), text)


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


def split_top_level_commas_spans(text):
    """Return unstripped comma-separated spans outside parentheses."""

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


def split_top_level_commas(text):
    return [text[start:end].strip() for start, end in split_top_level_commas_spans(text)]


def mask_quoted(text):
    """Public compatibility helper used by the INSERT path."""

    return _mask_quoted(text)


def mask_literal_residue(text):
    """Public compatibility helper used by the INSERT path."""

    return _mask_literal_residue(text)


def literal_value(text):
    """Return a scalar SQL literal, or ``None`` for a non-literal expression."""

    text = text.strip()
    match = _STRING_LITERAL_RE.match(text)
    if match:
        return match.group(2)
    if _NUMERIC_LITERAL_RE.match(text):
        return text
    return None


def extract_identifiers(expr):
    """Extract variable names while skipping function names and SQL keywords."""

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


def has_macro_ref(text):
    return bool(_MACRO_REF_RE.search(_SINGLE_QUOTED_RE.sub("", str(text or ""))))


def _resolve_select_text(text, statement_order, let_events):
    masked_text, protected_literals = _protect_single_quoted(text)
    resolved, unresolved = resolve_text(
        masked_text, statement_order, let_events, return_unresolved=True
    )
    if not unresolved:
        # Keep the legacy behavior for the macro quoting helper.  It is only
        # applied after ordinary macro references are known to be resolved.
        resolved = re.sub(
            r"%unquote\s*\(\s*([^()]*)\s*\)",
            lambda match: match.group(1),
            resolved,
            flags=re.IGNORECASE,
        )
    for placeholder, literal in protected_literals.items():
        resolved = resolved.replace(placeholder, literal)
    return resolved, tuple(name.lower() for name in unresolved)


def _macro_resolved_names(original, resolved, statement_order, let_events):
    """Return resolved identifiers whose text came from an individual macro."""

    names = set()
    for token in _MACRO_REF_RE.findall(_SINGLE_QUOTED_RE.sub("", original)):
        replacement = resolve_text(token, statement_order, let_events)
        if replacement == token:
            continue
        replacement = replacement.strip().lower()
        for identifier in extract_identifiers(resolved):
            identifier_lower = identifier.lower()
            if (
                identifier_lower == replacement
                or identifier_lower.startswith(replacement + ".")
            ):
                names.add(identifier.lower())
    return tuple(sorted(names))


def _split_select_alias(column):
    match = _COLUMN_ALIAS_RE.match(column)
    if match:
        return match.group(1).strip(), match.group(2)
    return column.strip(), None


def _bare_literal_expr_value(original_column, alias):
    pattern = re.compile(
        r"^\s*({})\s+as\s+{}\s*$".format(
            _LITERAL_TOKEN_RE, re.escape(alias)
        ),
        re.IGNORECASE,
    )
    match = pattern.match(original_column)
    return literal_value(match.group(1)) if match else None


def _variable(name, unresolved_names, span):
    clean = name.strip()
    return IRVariableRef(
        clean,
        clean.lstrip("&").rstrip(".").lower() in unresolved_names,
        span,
    )


def _unknown_expression(text, span, unresolved_names=()):
    refs = tuple(
        _variable(name, unresolved_names, span) for name in extract_identifiers(text)
    )
    return IRUnknownExpression(text, span, refs)


def _condition_operand(text, span, unresolved_names):
    stripped = text.strip()
    value = literal_value(stripped)
    if value is not None:
        return IRLiteral(value, span)
    if _BARE_COLUMN_RE.match(stripped):
        return _variable(stripped, unresolved_names, span)
    return None


def parse_predicate(text, span, unresolved_names=()):
    """Parse one comparison into existing IR nodes, or retain it as unknown."""

    condition_span = source_span(span, text.strip())
    match = _SQL_COMPARISON_RE.match(text.strip())
    if not match:
        return _unknown_expression(text.strip(), condition_span, unresolved_names)
    left = _condition_operand(match.group(1), condition_span, unresolved_names)
    right = _condition_operand(match.group(3), condition_span, unresolved_names)
    if left is None or right is None:
        return _unknown_expression(text.strip(), condition_span, unresolved_names)
    return IRComparison(left, match.group(2).upper(), right, condition_span)


def _parse_expression(text, span, unresolved_names=()):
    stripped = text.strip()
    literal = literal_value(stripped)
    if literal is not None:
        return IRLiteral(literal, source_span(span, stripped))
    if _BARE_COLUMN_RE.match(stripped):
        if stripped.rsplit(".", 1)[-1].lower() in _EXCLUDED_IDENTIFIER_WORDS:
            return IRUnknownExpression(stripped, source_span(span, stripped), ())
        return _variable(stripped, unresolved_names, source_span(span, stripped))
    return _unknown_expression(stripped, source_span(span, stripped), unresolved_names)


def _parse_case_expression(text, span, unresolved_names=()):
    masked = _mask_quoted(_mask_literal_residue(text))
    if (
        len(_WHEN_COUNT_RE.findall(masked)) != 1
        or len(_THEN_COUNT_RE.findall(masked)) != 1
        or len(_ELSE_COUNT_RE.findall(masked)) > 1
    ):
        return IRCaseExpression(
            _unknown_expression(text, span, unresolved_names),
            _unknown_expression(text, span, unresolved_names),
            source_span=source_span(span, text),
            supported=False,
        )
    match = _CASE_SHAPE_RE.match(text)
    if not match:
        return IRCaseExpression(
            _unknown_expression(text, span, unresolved_names),
            _unknown_expression(text, span, unresolved_names),
            source_span=source_span(span, text),
            supported=False,
        )
    condition = parse_predicate(match.group(1), span, unresolved_names)
    then_expression = _parse_expression(match.group(2), span, unresolved_names)
    else_expression = (
        _parse_expression(match.group(3), span, unresolved_names)
        if match.group(3) else None
    )
    return IRCaseExpression(
        condition,
        then_expression,
        else_expression,
        source_span=source_span(span, text),
        supported=True,
    )


def _parse_select_item(original_column, masked_column, span, statement_order, let_events):
    column = masked_column.strip()
    if not column:
        return None
    if column == "*" or re.fullmatch(r"[A-Za-z_]\w*\.\*", column):
        return IRSqlSelect(
            IRUnknownExpression(column, source_span(span, original_column), ()),
            None,
            column,
            original_column,
            source_span(span, original_column),
            kind="star",
        )
    if _N_LITERAL_RE.search(original_column):
        return IRSqlSelect(
            IRUnknownExpression(original_column, source_span(span, original_column), ()),
            None,
            column,
            original_column,
            source_span(span, original_column),
            kind="name_literal",
        )

    resolved_column, unresolved = _resolve_select_text(
        original_column, span.statement_order, let_events
    )
    resolved_masked = _mask_quoted(_mask_literal_residue(resolved_column))
    expression_text, alias = _split_select_alias(resolved_masked)
    macro_names = _macro_resolved_names(
        original_column, resolved_column, span.statement_order, let_events
    )
    expression_span = source_span(span, expression_text)
    literal = _bare_literal_expr_value(resolved_column, alias) if alias else None
    if literal is None and alias:
        # A single-quoted literal is intentionally protected from macro
        # substitution, so fall back to the original token in that case.
        literal = _bare_literal_expr_value(original_column, alias)

    if _CASE_HEAD_RE.match(expression_text):
        case_source = re.sub(
            r"\s+as\s+{}(?:\s+(?:length|format|label)\s*=\s*.*)?$".format(
                re.escape(alias or "")
            ),
            "",
            resolved_column,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        expression = _parse_case_expression(
            case_source, expression_span, unresolved
        )
        kind = "case"
    elif (
        _BARE_COLUMN_RE.match(expression_text)
        and expression_text.rsplit(".", 1)[-1].lower()
        not in _EXCLUDED_IDENTIFIER_WORDS
    ):
        expression = _variable(expression_text, unresolved, expression_span)
        kind = "column"
    elif literal is not None:
        expression = IRLiteral(literal, expression_span)
        kind = "expression"
    else:
        expression = _parse_expression(expression_text, expression_span, unresolved)
        kind = "expression"
    return IRSqlSelect(
        expression,
        alias,
        expression_text,
        original_column,
        source_span(span, original_column),
        kind=kind,
        unresolved_names=unresolved,
        macro_resolved_names=macro_names,
        literal_value=literal,
    )


def _clause_span(source_text, clause_pattern):
    clause_match = _top_level_match(source_text, clause_pattern)
    if not clause_match:
        return None
    end_match = _top_level_match(source_text, _CLAUSE_END_RE, clause_match.end())
    end = end_match.start() if end_match else len(source_text)
    return clause_match.end(), end


def _parse_clause_expression(text, span):
    stripped = text.strip()
    if _BARE_COLUMN_RE.match(stripped):
        return _variable(stripped, (), source_span(span, stripped))
    # A non-bare GROUP/ORDER item is intentionally not partially traversed:
    # the legacy rule reports one unsupported item and emits no variable edge.
    return IRUnknownExpression(stripped, source_span(span, stripped), ())


def _join_kind(match):
    prefix = match.group(0).lower()
    if "left" in prefix:
        return "LEFT"
    if "right" in prefix:
        return "RIGHT"
    if "full" in prefix:
        return "FULL"
    if "cross" in prefix:
        return "CROSS"
    if "inner" in prefix:
        return "INNER"
    if "outer" in prefix:
        return "OUTER"
    return None


def _source_and_on(segment, span):
    on_match = _top_level_match(segment, _ON_RE)
    source_text = segment[:on_match.start()] if on_match else segment
    source_match = _FROM_ITEM_RE.match(source_text.strip())
    if not source_match:
        return None, "FROM/JOIN source `{}` is not a plain `name` or `name AS alias` reference; it was not resolved.".format(
            source_text.strip()
        )
    source = IRSqlSource(
        source_match.group(1),
        source_match.group(2),
        source_span(span, source_text.strip()),
    )
    if not on_match:
        return source, None
    predicate_text = segment[on_match.end():].strip()
    predicate = parse_predicate(predicate_text, span)
    return IRSqlJoin(source, None, predicate, predicate_text, source.source_span), None


def _parse_sources(masked_text, span):
    from_match = _top_level_match(masked_text, _FROM_CLAUSE_RE)
    if not from_match:
        return (), (), ()
    from_end_match = _top_level_match(
        masked_text, _FROM_CLAUSE_END_RE, from_match.end()
    )
    from_end = from_end_match.start() if from_end_match else len(masked_text)
    from_text = masked_text[from_match.end():from_end]
    # Advance past each match so the optional kind prefix cannot also yield a
    # second overlapping bare ``JOIN`` match at the same token.
    join_matches = []
    join_cursor = 0
    while True:
        join_match = _top_level_match(from_text, _JOIN_BOUNDARY_RE, join_cursor)
        if join_match is None:
            break
        join_matches.append(join_match)
        join_cursor = join_match.end()
    first_join = join_matches[0] if join_matches else None
    base_text = from_text[:first_join.start()] if first_join else from_text
    sources = []
    diagnostics = []
    for item in split_top_level_commas(base_text):
        if not item:
            continue
        match = _FROM_ITEM_RE.match(item)
        if match:
            sources.append(IRSqlSource(match.group(1), match.group(2), source_span(span, item)))
        else:
            diagnostics.append(
                IRSqlDiagnostic(
                    "proc_sql_unsupported_syntax",
                    "FROM source `{}` is not a plain `name` or `name AS alias` reference; it was not resolved.".format(item),
                    "Rewrite as a plain dataset reference to capture lineage.",
                )
            )
    joins = []
    for index, join_match in enumerate(join_matches):
        segment_start = join_match.end()
        segment_end = (
            join_matches[index + 1].start()
            if index + 1 < len(join_matches) else len(from_text)
        )
        segment = from_text[segment_start:segment_end].strip()
        parsed, error = _source_and_on(segment, span)
        if parsed is None:
            diagnostics.append(
                IRSqlDiagnostic(
                    "proc_sql_unsupported_syntax",
                    error,
                    "Rewrite as a plain dataset reference to capture lineage.",
                )
            )
            continue
        if isinstance(parsed, IRSqlJoin):
            joins.append(
                IRSqlJoin(
                    parsed.source,
                    _join_kind(join_match),
                    parsed.predicate,
                    parsed.predicate_text,
                    parsed.source_span,
                )
            )
            sources.append(parsed.source)
        else:
            joins.append(
                IRSqlJoin(
                    parsed,
                    _join_kind(join_match),
                    None,
                    "",
                    parsed.source_span,
                )
            )
            sources.append(parsed)
    return tuple(sources), tuple(joins), tuple(diagnostics)


def parse_sql(statement_text, subtype, target_raw, source, let_events=()):
    """Parse a PROC SQL statement into unresolved IR."""

    span = source_span(source, statement_text)
    masked_text = _mask_quoted(statement_text)
    sources, joins, diagnostics = _parse_sources(masked_text, span)

    select_items = []
    select_span = _select_list_span(masked_text)
    if select_span:
        list_start, list_end = select_span
        masked_list = masked_text[list_start:list_end]
        original_list = statement_text[list_start:list_end]
        distinct_match = _DISTINCT_RE.match(masked_list)
        if distinct_match:
            cut = distinct_match.end()
            masked_list, original_list = masked_list[cut:], original_list[cut:]
        for relative_start, relative_end in split_top_level_commas_spans(masked_list):
            item = _parse_select_item(
                original_list[relative_start:relative_end],
                masked_list[relative_start:relative_end],
                span,
                span.statement_order,
                let_events,
            )
            if item is not None:
                select_items.append(item)

    def clause_expression(pattern, predicate=False):
        clause = _clause_span(masked_text, pattern)
        if not clause:
            return None
        start, end = clause
        text = statement_text[start:end].strip()
        if not text:
            return None
        return parse_predicate(text, span) if predicate else _parse_clause_expression(text, span)

    where = clause_expression(_WHERE_CLAUSE_RE, predicate=True)
    having = clause_expression(_HAVING_CLAUSE_RE, predicate=True)

    group_by = []
    group_span = _clause_span(masked_text, _GROUP_BY_CLAUSE_RE)
    if group_span:
        start, end = group_span
        group_text = statement_text[start:end].strip()
        group_by = [
            _parse_clause_expression(item, span)
            for item in split_top_level_commas(group_text)
            if item
        ]

    order_by = []
    order_span = _clause_span(masked_text, _ORDER_BY_CLAUSE_RE)
    if order_span:
        start, end = order_span
        order_text = statement_text[start:end].strip()
        for item in split_top_level_commas(order_text):
            if not item:
                continue
            stripped = _ORDER_BY_DIRECTION_RE.sub("", item).strip()
            direction = None
            if stripped != item.strip():
                direction_match = re.search(r"\b(asc|desc)\b", item, re.IGNORECASE)
                direction = direction_match.group(1).upper() if direction_match else None
            order_by.append(
                IRSqlOrder(
                    _parse_clause_expression(stripped, span),
                    direction,
                    item,
                    source_span(span, item),
                )
            )

    return IRSqlStatement(
        subtype,
        target_raw,
        tuple(select_items),
        sources,
        joins,
        where,
        having,
        tuple(group_by),
        tuple(order_by),
        span,
        diagnostics,
    )


# ---------------------------------------------------------------------------
# Resolver pass.  It never mutates a graph or selects the first source when a
# reference is ambiguous.


def _expression_refs(expression):
    if isinstance(expression, IRCaseExpression):
        refs = []
        refs.extend(iter_variable_refs(expression.condition))
        refs.extend(iter_variable_refs(expression.then_expression))
        refs.extend(iter_variable_refs(expression.else_expression))
        return tuple(refs)
    return tuple(iter_variable_refs(expression))


def resolve_reference(
    reference, source_ids, alias_map, unresolved_names=(), macro_resolved_names=()
):
    """Resolve one IR reference against a statement-local alias table."""

    raw = reference.name.strip()
    unresolved = raw.lstrip("&").rstrip(".").lower() in {
        name.lower() for name in unresolved_names
    }
    if raw.startswith("&"):
        raw = raw[1:].rstrip(".")
    if "." in raw:
        qualifier, variable_name = raw.split(".", 1)
        dataset_id = alias_map.get(qualifier.lower())
        candidates = (dataset_id,) if dataset_id is not None else ()
    else:
        variable_name = raw
        dataset_id = source_ids[0] if len(source_ids) == 1 else None
        candidates = tuple(source_ids) if len(source_ids) > 1 else ()

    exact = (
        not unresolved
        and dataset_id is not None
        and str(dataset_id).lower().startswith("dataset:")
    )
    return IRSqlBinding(
        reference,
        variable_name,
        dataset_id if exact else None,
        candidates,
        exact,
        reference.name.lower() in {name.lower() for name in macro_resolved_names},
    )


def _resolve_expression(expression, source_ids, alias_map, unresolved_names=(), macro_resolved_names=()):
    bindings = tuple(
        resolve_reference(
            reference,
            source_ids,
            alias_map,
            unresolved_names,
            macro_resolved_names,
        )
        for reference in _expression_refs(expression)
    )
    return IRSqlResolvedExpression(expression, bindings)


def resolve_sql(ir, source_ids, alias_map):
    """Bind all SQL references, returning a graph-free resolved IR."""

    select_items = tuple(
        IRSqlResolvedSelect(
            item,
            _resolve_expression(
                item.expression,
                source_ids,
                alias_map,
                item.unresolved_names,
                item.macro_resolved_names,
            ),
        )
        for item in ir.select_items
    )
    joins = tuple(
        IRSqlResolvedJoin(
            join,
            _resolve_expression(join.predicate, source_ids, alias_map)
            if join.predicate is not None else None,
        )
        for join in ir.joins
    )
    where = (
        _resolve_expression(ir.where, source_ids, alias_map)
        if ir.where is not None else None
    )
    having = (
        _resolve_expression(ir.having, source_ids, alias_map)
        if ir.having is not None else None
    )
    group_by = tuple(
        _resolve_expression(expression, source_ids, alias_map)
        for expression in ir.group_by
    )
    order_by = tuple(
        IRSqlResolvedOrder(
            order,
            _resolve_expression(order.expression, source_ids, alias_map),
        )
        for order in ir.order_by
    )
    return IRSqlResolvedStatement(
        ir,
        select_items,
        joins,
        where,
        having,
        group_by,
        order_by,
        tuple(source_ids),
        alias_map,
    )


# ---------------------------------------------------------------------------
# Graph emission.  Keep this section regex-free: source parsing belongs above.


def _edge_evidence(ctx, source, *endpoint_ids, macro_resolved=False):
    extractor = source.get("rule", "rules_proc_sql") if isinstance(source, dict) else "rules_proc_sql"
    if any(str(node_id).lower().startswith(_UNKNOWN_ID_PREFIXES) for node_id in endpoint_ids):
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


def _unknown_reference(ctx, statement, binding):
    display = binding.variable_name or binding.reference.name
    source = statement.as_source("unqualified_variable_reference")
    node_id = ctx.add_unknown_variable(display, statement.statement_order, source)
    message = (
        "Column `{}` could not be resolved to exactly one FROM/JOIN source "
        "in this statement."
    ).format(display)
    if len(binding.candidates) > 1:
        message += " Candidates: {}.".format(", ".join(binding.candidates))
    ctx.add_finding(
        "unqualified_variable_reference",
        "UNQUALIFIED_VARIABLE",
        "WARNING",
        node_id,
        message,
        "Qualify the column with its source table alias, or ensure the statement has a single source table.",
        source,
        affected_nodes=[node_id],
    )
    return node_id


def materialize_reference(ctx, statement, binding):
    """Turn a resolved binding into a Variable/UnknownVariable node id."""

    if binding.exact:
        return ctx.add_variable(
            binding.dataset_id[len("dataset:"):], binding.variable_name
        )
    return _unknown_reference(ctx, statement, binding)


def _expression_reads(ctx, statement, sql_statement_id, resolved_expression, rule):
    for binding in resolved_expression.bindings:
        variable_id = materialize_reference(ctx, statement, binding)
        source = statement.as_source(rule)
        ctx.add_edge(
            "reads_variable",
            variable_id,
            sql_statement_id,
            source,
            evidence=_edge_evidence(
                ctx,
                source,
                variable_id,
                sql_statement_id,
                macro_resolved=binding.macro_resolved,
            ),
            value=None,
            operator=None,
        )


def _predicate_reads(ctx, statement, sql_statement_id, resolved_predicate, rule):
    expression = resolved_predicate.expression
    if not isinstance(expression, IRComparison):
        ctx.add_finding(
            "proc_sql_unsupported_syntax",
            "NOT_EXECUTED",
            "WARNING",
            sql_statement_id,
            "Condition `{}` is not a single `column OP literal|column` comparison; it was not parsed.".format(
                getattr(expression, "source_text", "")
            ),
            "Rewrite as a single comparison to capture variable-level lineage.",
            statement.as_source(rule),
            affected_nodes=[sql_statement_id],
        )
        return ()

    bindings = resolved_predicate.bindings
    predicate_source = statement.as_source(rule)
    emitted = []
    for index, binding in enumerate(bindings):
        variable_id = materialize_reference(ctx, statement, binding)
        literal = None
        if index == 1 and isinstance(expression.right, IRLiteral):
            literal = expression.right.value
        if index == 0 and isinstance(expression.right, IRLiteral):
            literal = expression.right.value
        ctx.add_edge(
            "reads_variable",
            variable_id,
            sql_statement_id,
            predicate_source,
            evidence=_edge_evidence(
                ctx,
                predicate_source,
                variable_id,
                sql_statement_id,
                macro_resolved=binding.macro_resolved,
            ),
            value=literal,
            operator=expression.operator,
        )
        emitted.append(binding)
    return tuple(emitted)


def _semantic_edge(ctx, statement, sql_statement_id, edge_type, binding, rule, **extra):
    if not binding.exact:
        return False
    variable_id = materialize_reference(ctx, statement, binding)
    source = statement.as_source(rule)
    ctx.add_edge(
        edge_type,
        variable_id,
        sql_statement_id,
        source,
        evidence=_edge_evidence(
            ctx,
            source,
            variable_id,
            sql_statement_id,
            macro_resolved=binding.macro_resolved,
        ),
        **extra
    )
    return True


def _emit_select(ctx, statement, sql_statement_id, target_id, resolved, target_columns):
    if not target_id.startswith("dataset:"):
        return
    target_raw = target_id[len("dataset:"):]
    for index, resolved_item in enumerate(resolved.select_items):
        item = resolved_item.item
        write_name = (
            target_columns[index]
            if target_columns is not None and index < len(target_columns) else None
        )
        if item.kind == "star":
            ctx.add_finding(
                "proc_sql_select_star_unsupported",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                "SELECT * does not enumerate source columns; no per-column variable lineage was inferred.",
                "List explicit columns to capture variable-level lineage.",
                statement.as_source("proc_sql_select_star_unsupported"),
                affected_nodes=[sql_statement_id],
            )
            continue
        if item.kind == "name_literal":
            ctx.add_finding(
                "proc_sql_unsupported_syntax",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                "SELECT expression `{}` uses SAS name-literal syntax (`'name'n`), which is not parsed.".format(
                    item.original_text.strip()
                ),
                "Rewrite without a name literal to capture variable-level lineage.",
                statement.as_source("proc_sql_unsupported_syntax"),
                affected_nodes=[sql_statement_id],
            )
            continue

        expression = resolved_item.expression.expression
        if item.kind == "case":
            if not isinstance(expression, IRCaseExpression) or not expression.supported:
                ctx.add_finding(
                    "proc_sql_unsupported_syntax",
                    "NOT_EXECUTED",
                    "WARNING",
                    sql_statement_id,
                    "CASE expression `{}` is not a single `CASE WHEN ... THEN ... [ELSE ...] END AS alias` shape.".format(
                        item.expression_text
                    ),
                    "Rewrite to the supported CASE shape to capture variable-level lineage.",
                    statement.as_source("proc_sql_select_case"),
                    affected_nodes=[sql_statement_id],
                )
                continue
            if not item.alias:
                ctx.add_finding(
                    "proc_sql_select_expression_unaliased",
                    "NOT_EXECUTED",
                    "WARNING",
                    sql_statement_id,
                    "Computed SELECT expression `{}` has no alias; its output variable could not be named.".format(
                        item.expression_text
                    ),
                    "Add an alias (`AS name`) to the computed expression.",
                    statement.as_source("proc_sql_select_case"),
                    affected_nodes=[sql_statement_id],
                )
                continue
            condition = IRSqlResolvedExpression(
                expression.condition,
                resolved_item.expression.bindings[:len(_expression_refs(expression.condition))],
            )
            if not isinstance(condition.expression, IRComparison):
                _predicate_reads(ctx, statement, sql_statement_id, condition, "proc_sql_select_case")
                continue
            _predicate_reads(ctx, statement, sql_statement_id, condition, "proc_sql_select_case")
            condition_count = len(_expression_refs(expression.condition))
            result_bindings = resolved_item.expression.bindings[condition_count:]
            for binding in result_bindings:
                _expression_reads(
                    ctx,
                    statement,
                    sql_statement_id,
                    IRSqlResolvedExpression(binding.reference, (binding,)),
                    "proc_sql_select_case",
                )
            write_name = write_name if write_name is not None else item.alias
            write_id = ctx.add_variable(target_raw, write_name)
            write_source = statement.as_source("proc_sql_select_case")
            ctx.add_edge(
                "writes_variable",
                sql_statement_id,
                write_id,
                write_source,
                evidence=_edge_evidence(
                    ctx, write_source, sql_statement_id, write_id
                ),
                value=None,
                operator=None,
            )
            continue

        if item.kind == "column":
            _expression_reads(
                ctx,
                statement,
                sql_statement_id,
                resolved_item.expression,
                "proc_sql_select_column",
            )
            if isinstance(expression, IRVariableRef):
                default_name = expression.name.rsplit(".", 1)[-1]
            else:
                default_name = item.alias
            write_name = write_name if write_name is not None else item.alias or default_name
            write_id = ctx.add_variable(target_raw, write_name)
            write_source = statement.as_source("proc_sql_select_column")
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
                    macro_resolved=(
                        item.alias is None and has_macro_ref(item.original_text)
                    ),
                ),
                value=None,
                operator=None,
            )
            continue

        if not item.alias:
            ctx.add_finding(
                "proc_sql_select_expression_unaliased",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                "Computed SELECT expression `{}` has no alias; its output variable could not be named.".format(
                    item.expression_text
                ),
                "Add an alias (`AS name`) to the computed expression.",
                statement.as_source("proc_sql_select_expression"),
                affected_nodes=[sql_statement_id],
            )
            continue
        _expression_reads(
            ctx,
            statement,
            sql_statement_id,
            resolved_item.expression,
            "proc_sql_select_expression",
        )
        write_name = write_name if write_name is not None else item.alias
        write_id = ctx.add_variable(target_raw, write_name)
        write_source = statement.as_source("proc_sql_select_expression")
        ctx.add_edge(
            "writes_variable",
            sql_statement_id,
            write_id,
            write_source,
            evidence=_edge_evidence(
                ctx, write_source, sql_statement_id, write_id
            ),
            value=item.literal_value if not resolved_item.expression.bindings else None,
            operator=None,
        )


def _emit_predicate_clause(
    ctx,
    statement,
    sql_statement_id,
    resolved_predicate,
    rule,
    semantic_edges,
    filter_semantics=False,
):
    if resolved_predicate is None:
        return
    bindings = _predicate_reads(
        ctx, statement, sql_statement_id, resolved_predicate, rule
    )
    if not filter_semantics or not isinstance(resolved_predicate.expression, IRComparison):
        return
    for binding in bindings:
        _semantic_edge(
            ctx,
            statement,
            sql_statement_id,
            "filters_dataset",
            binding,
            rule,
            predicate_text=resolved_predicate.expression.source_span.original_text
            if resolved_predicate.expression.source_span else None,
            operator=resolved_predicate.expression.operator,
        )


def emit_sql(
    resolved,
    ctx,
    statement,
    sql_statement_id,
    target_id,
    target_columns=None,
    semantic_edges=False,
):
    """Emit legacy and B2 semantic edges from resolved SQL IR.

    This function intentionally contains no regex calls.  Every source-shape
    decision was made by :func:`parse_sql`; this stage only traverses IR and
    asks the graph context to materialize exact or unknown bindings.
    """

    _emit_select(
        ctx,
        statement,
        sql_statement_id,
        target_id,
        resolved,
        target_columns,
    )

    for resolved_join in resolved.joins:
        predicate = resolved_join.predicate
        if predicate is None:
            continue
        if not isinstance(predicate.expression, IRComparison):
            _predicate_reads(
                ctx,
                statement,
                sql_statement_id,
                predicate,
                "proc_sql_join",
            )
            continue
        for binding in predicate.bindings:
            if semantic_edges:
                _semantic_edge(
                    ctx,
                    statement,
                    sql_statement_id,
                    "joins_on",
                    binding,
                    "proc_sql_join",
                    join_kind=resolved_join.join.kind,
                    predicate_text=resolved_join.join.predicate_text,
                    operator=predicate.expression.operator,
                )

    _emit_predicate_clause(
        ctx,
        statement,
        sql_statement_id,
        resolved.where,
        "proc_sql_where_condition",
        semantic_edges,
        filter_semantics=semantic_edges and len(resolved.source_ids) > 1,
    )
    for group in resolved.group_by:
        bindings = group.bindings
        if not bindings:
            ctx.add_finding(
                "proc_sql_unsupported_syntax",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                "GROUP BY item `{}` is not a bare column reference; it was not parsed.".format(
                    group.expression.source_text
                ),
                "Rewrite the clause as a comma-separated list of columns to capture lineage.",
                statement.as_source("proc_sql_group_by"),
                affected_nodes=[sql_statement_id],
            )
            continue
        for binding in bindings:
            variable_id = materialize_reference(ctx, statement, binding)
            source = statement.as_source("proc_sql_group_by")
            ctx.add_edge(
                "reads_variable",
                variable_id,
                sql_statement_id,
                source,
                evidence=_edge_evidence(
                    ctx, source, variable_id, sql_statement_id,
                    macro_resolved=binding.macro_resolved,
                ),
                value=None,
                operator=None,
            )
            if semantic_edges:
                _semantic_edge(
                    ctx,
                    statement,
                    sql_statement_id,
                    "groups_by",
                    binding,
                    "proc_sql_group_by",
                )

    _emit_predicate_clause(
        ctx,
        statement,
        sql_statement_id,
        resolved.having,
        "proc_sql_having_condition",
        semantic_edges,
        filter_semantics=semantic_edges and len(resolved.source_ids) > 1,
    )
    for resolved_order in resolved.order_by:
        bindings = resolved_order.expression.bindings
        if not bindings:
            ctx.add_finding(
                "proc_sql_unsupported_syntax",
                "NOT_EXECUTED",
                "WARNING",
                sql_statement_id,
                "ORDER BY item `{}` is not a bare column reference; it was not parsed.".format(
                    resolved_order.order.original_text
                ),
                "Rewrite the clause as a comma-separated list of columns to capture lineage.",
                statement.as_source("proc_sql_order_by"),
                affected_nodes=[sql_statement_id],
            )
            continue
        for binding in bindings:
            variable_id = materialize_reference(ctx, statement, binding)
            source = statement.as_source("proc_sql_order_by")
            ctx.add_edge(
                "reads_variable",
                variable_id,
                sql_statement_id,
                source,
                evidence=_edge_evidence(
                    ctx, source, variable_id, sql_statement_id,
                    macro_resolved=binding.macro_resolved,
                ),
                value=None,
                operator=None,
            )
            if semantic_edges:
                _semantic_edge(
                    ctx,
                    statement,
                    sql_statement_id,
                    "sorts_by",
                    binding,
                    "proc_sql_order_by",
                    direction=resolved_order.order.direction,
                )


def demo():
    """Small standalone smoke output used by ``python tests/test_sql_ir.py``."""

    source = {
        "file": "demo.sas",
        "line_start": 1,
        "line_end": 1,
        "statement_order": 1,
        "original_text": "select a.id from work.a as a",
    }
    parsed = parse_sql(
        "create table work.out as select a.id from work.a as a",
        "CREATE_TABLE",
        "work.out",
        source,
    )
    resolved = resolve_sql(parsed, ("dataset:work.a",), {"a": "dataset:work.a"})
    print("sql_ir: {} select item(s), {} source(s)".format(
        len(resolved.select_items), len(parsed.sources)
    ))


if __name__ == "__main__":
    demo()
