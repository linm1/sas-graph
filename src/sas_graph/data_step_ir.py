"""Parse and emit the DATA-step assignment/condition IR slice.

The parser in this module is intentionally small.  It recognizes literals,
variable references, pure function calls, and ordinary unary/binary
expressions; anything else is retained verbatim as ``IRUnknownExpression``.
Graph mutation is confined to the two emit functions, which consume IR objects
and never parse source text.
"""

import re

from .evidence import EvidenceKind, ResolutionStatus
from .ir import (
    IRAssignment,
    IRBinaryOp,
    IRComparison,
    IRComparisonChain,
    IRFunctionCall,
    IRInList,
    IRLiteral,
    IRUnaryOp,
    IRUnknownExpression,
    IRVariableRef,
    SourceSpan,
    iter_variable_refs,
)


_ASSIGNMENT_RE = re.compile(r"^(&?[A-Za-z_]\w*\.?)\s*=\s*(.+?);?$")
_COMPARISON_SYMBOLS = {"=>", "=<", "~=", "^=", ">=", "<=", ">", "<", "="}
_COMPARISON_OPERATOR_ALIASES = {"=>": ">=", "=<": "<="}
_COMPARISON_WORDS = {"eq", "ne", "gt", "lt", "ge", "le"}
_BARE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")
_CONDITION_IDENTIFIER_EXCLUDED = {
    "not", "and", "or", "in", "eq", "ne", "gt", "lt", "ge", "le", "of",
    "_n_", "_error_",
}
_MAX_PARENTHESIS_DEPTH = 100
_MAX_EXPRESSION_TERMS = 200
_MACRO_REF_RE = re.compile(r"&[A-Za-z_]\w*\.?", re.IGNORECASE)
_UNKNOWN_ID_PREFIXES = ("unknowndataset:", "unknownvariable:", "unknownmacro:")
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*(?:'|$)", re.DOTALL)
_QUOTED_LITERAL_RE = re.compile(
    r'"(?:""|[^"])*(?:"|$)|\'(?:\'\'|[^\'])*(?:\'|$)', re.DOTALL
)
_PURE_FUNCTIONS = frozenset({
    "upcase", "lowcase", "strip", "trim", "left", "right", "compress",
    "compbl", "substr", "scan", "index", "find", "length", "lengthn",
    "cat", "cats", "catt", "catx", "tranwrd", "translate", "propcase",
    "coalesce", "coalescec", "ifn", "ifc", "missing", "sum", "mean",
    "min", "max", "n", "nmiss", "round", "int", "ceil", "floor", "abs",
    "mod", "datepart", "timepart", "year", "month", "day", "mdy", "intck",
    "intnx",
})


def _has_macro_ref(text):
    return bool(_MACRO_REF_RE.search(_SINGLE_QUOTED_RE.sub("", str(text or ""))))


def _edge_evidence(ctx, source, *endpoint_ids):
    extractor = source.get("rule", "data_step_assignment") if isinstance(source, dict) else "data_step_assignment"
    if any(str(node_id).lower().startswith(_UNKNOWN_ID_PREFIXES) for node_id in endpoint_ids):
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

_BINARY_PRECEDENCE = {
    "=": 3,
    "eq": 3,
    "ne": 3,
    "gt": 3,
    "lt": 3,
    "ge": 3,
    "le": 3,
    "~=": 3,
    "^=": 3,
    "<": 3,
    ">": 3,
    "<=": 3,
    "in": 3,
    ">=": 3,
    "=>": 3,
    "=<": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
    "and": 2,
    "or": 1,
}


def source_span(source, original_text=None):
    """Convert an existing source mapping into the dependency-free IR span.

    Expression and condition nodes carry their own source text while the
    assignment operation keeps the complete statement span.  Keeping that
    text on the IR means graph emitters can expose source expressions without
    re-parsing or re-rendering the expression tree.
    """

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


def _is_macro_unresolved(name, unresolved_names):
    return name.lstrip("&").rstrip(".").lower() in unresolved_names


def _variable(name, unresolved_names, span):
    return IRVariableRef(
        name=name,
        macro_unresolved=_is_macro_unresolved(name, unresolved_names),
        source_span=span,
    )


def _tokenize(text):
    """Return a tiny expression token stream, preserving quoted source."""

    tokens = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "'\"":
            quote = char
            start = index
            index += 1
            while index < length:
                if text[index] != quote:
                    index += 1
                    continue
                if index + 1 < length and text[index + 1] == quote:
                    index += 2
                    continue
                index += 1
                break
            while index < length and text[index].isalpha():
                index += 1
            tokens.append(("atom", text[start:index]))
            continue
        if char.isalpha() or char == "_" or char == "&":
            start = index
            index += 1
            while index < length and (text[index].isalnum() or text[index] in "_.&"):
                index += 1
            tokens.append(("word", text[start:index]))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < length and (text[index].isdigit() or text[index] == "."):
                index += 1
            tokens.append(("number", text[start:index]))
            continue
        if char == ".":
            start = index
            index += 1
            while index < length and text[index].isalpha():
                index += 1
            tokens.append(("missing", text[start:index]))
            continue
        if index + 1 < length and text[index:index + 2] in {
            "<=", ">=", "=>", "=<", "~=", "^=",
        }:
            tokens.append(("operator", text[index:index + 2]))
            index += 2
            continue
        if char in "+-*/=<>(),":
            kind = "operator" if char not in "()," else char
            tokens.append((kind, char))
            index += 1
            continue
        tokens.append(("unknown", char))
        index += 1
    return tokens


class _UnsupportedExpression(Exception):
    pass


class _ExpressionParser:
    def __init__(self, text, unresolved_names, span, literal_value, condition=False):
        self.text = text
        self.tokens = _tokenize(text)
        self.unresolved_names = unresolved_names
        self.span = span
        self.literal_value = literal_value
        self.condition = condition
        self.index = 0
        self.parenthesis_depth = 0

    def parse(self):
        if not self.tokens:
            raise _UnsupportedExpression
        logical_terms = sum(
            kind == "word" and token.lower() in {"and", "or"}
            for kind, token in self.tokens
        )
        binary_terms = sum(
            kind == "operator"
            or kind == "word" and token.lower() in _BINARY_PRECEDENCE
            for kind, token in self.tokens
        ) - logical_terms
        if (
            logical_terms > _MAX_EXPRESSION_TERMS
            or binary_terms > _MAX_EXPRESSION_TERMS
        ):
            raise _UnsupportedExpression
        expression = self._parse_binary(1)
        if self.index != len(self.tokens):
            raise _UnsupportedExpression
        return expression

    def _parse_binary(self, minimum_precedence):
        left = self._parse_unary()
        while self.index < len(self.tokens):
            kind, token = self.tokens[self.index]
            negated_in = (
                kind == "word"
                and token.lower() == "not"
                and self.index + 1 < len(self.tokens)
                and self.tokens[self.index + 1][0] == "word"
                and self.tokens[self.index + 1][1].lower() == "in"
            )
            if negated_in:
                operator = "in"
                consumed = 2
            elif kind != "operator":
                if kind == "word" and token.lower() in _BINARY_PRECEDENCE:
                    operator = token.lower()
                else:
                    break
                consumed = 1
            else:
                operator = token.lower()
                consumed = 1
            precedence = _BINARY_PRECEDENCE.get(operator)
            if precedence is None or precedence < minimum_precedence:
                break
            self.index += consumed
            if operator == "in":
                left = self._parse_in_list(left, negated=negated_in)
            else:
                right = self._parse_binary(precedence + 1)
                left = IRBinaryOp(
                    left,
                    operator.upper() if operator in {
                        "eq", "ne", "gt", "lt", "ge", "le",
                    } else operator,
                    right,
                    self.span,
                )
        return left

    def _parse_in_list(self, left, negated=False):
        if not _is_plain_variable_ref(left):
            raise _UnsupportedExpression
        if self.index >= len(self.tokens) or self.tokens[self.index][0] != "(":
            raise _UnsupportedExpression
        self.index += 1
        self.parenthesis_depth += 1
        try:
            if self.parenthesis_depth > _MAX_PARENTHESIS_DEPTH:
                raise _UnsupportedExpression
            if self.index >= len(self.tokens) or self.tokens[self.index][0] == ")":
                raise _UnsupportedExpression
            members = []
            while True:
                member = self._parse_binary(1)
                if not (
                    isinstance(member, IRLiteral)
                    or _is_plain_variable_ref(member)
                    or isinstance(member, IRFunctionCall) and _calls_are_pure(member)
                ):
                    raise _UnsupportedExpression
                members.append(member)
                if self.index >= len(self.tokens):
                    raise _UnsupportedExpression
                kind = self.tokens[self.index][0]
                if kind == ")":
                    self.index += 1
                    break
                if kind != ",":
                    raise _UnsupportedExpression
                self.index += 1
                if self.index >= len(self.tokens) or self.tokens[self.index][0] == ")":
                    raise _UnsupportedExpression
            return IRInList(left, tuple(members), negated, self.span)
        finally:
            self.parenthesis_depth -= 1

    def _parse_unary(self):
        if self.index < len(self.tokens):
            kind, token = self.tokens[self.index]
            if kind == "word" and token.lower() == "not":
                if (
                    self.index + 2 < len(self.tokens)
                    and self.tokens[self.index + 1][0] == "word"
                    and self.tokens[self.index + 2][0] == "word"
                    and self.tokens[self.index + 2][1].lower() == "in"
                ):
                    raise _UnsupportedExpression
                self.index += 1
                return IRUnaryOp(token, self._parse_unary(), self.span)
            if kind == "operator" and token in {"+", "-", "~", "^"}:
                self.index += 1
                return IRUnaryOp(token, self._parse_unary(), self.span)
        return self._parse_primary()

    def _parse_primary(self):
        if self.index >= len(self.tokens):
            raise _UnsupportedExpression
        kind, token = self.tokens[self.index]
        if kind == "(":
            self.index += 1
            self.parenthesis_depth += 1
            try:
                if self.parenthesis_depth > _MAX_PARENTHESIS_DEPTH:
                    raise _UnsupportedExpression
                expression = self._parse_binary(1)
                if (
                    self.index >= len(self.tokens)
                    or self.tokens[self.index][0] != ")"
                ):
                    raise _UnsupportedExpression
                self.index += 1
                return expression
            finally:
                self.parenthesis_depth -= 1
        if kind == "atom":
            self.index += 1
            if token.lower().endswith("n"):
                return _variable(token, self.unresolved_names, self.span)
            value = self.literal_value(token)
            if value is None:
                raise _UnsupportedExpression
            return IRLiteral(value, self.span)
        if kind == "number":
            self.index += 1
            return IRLiteral(token, self.span)
        if kind == "missing":
            self.index += 1
            return IRLiteral(None, self.span)
        if kind == "word":
            if token.lower() in {"_n_", "_error_"}:
                self.index += 1
                return IRLiteral(None, self.span)
            if token.startswith("&"):
                self.index += 1
                return _variable(token, self.unresolved_names, self.span)
            if token.lower() in {"and", "or"} or (
                not self.condition
                and token.lower() in {"eq", "ne", "gt", "lt", "ge", "le"}
            ):
                raise _UnsupportedExpression
            if (
                self.index + 1 < len(self.tokens)
                and self.tokens[self.index + 1][0] == "("
            ):
                return self._parse_call(token)
            self.index += 1
            return _variable(token, self.unresolved_names, self.span)
        raise _UnsupportedExpression

    def _parse_call(self, name):
        self.index += 2
        self.parenthesis_depth += 1
        try:
            if self.parenthesis_depth > _MAX_PARENTHESIS_DEPTH:
                raise _UnsupportedExpression
            if (
                self.index >= len(self.tokens)
                or self.tokens[self.index][0] == ")"
            ):
                raise _UnsupportedExpression

            arguments = []
            while True:
                arguments.append(self._parse_binary(1))
                if self.index >= len(self.tokens):
                    raise _UnsupportedExpression
                kind = self.tokens[self.index][0]
                if kind == ")":
                    self.index += 1
                    break
                if kind != ",":
                    raise _UnsupportedExpression
                self.index += 1
                if (
                    self.index >= len(self.tokens)
                    or self.tokens[self.index][0] == ")"
                ):
                    raise _UnsupportedExpression
            return IRFunctionCall(name.lower(), tuple(arguments), self.span)
        finally:
            self.parenthesis_depth -= 1


def _contains_call(tokens):
    return any(
        kind == "word"
        and index + 1 < len(tokens)
        and tokens[index + 1][0] == "("
        for index, (kind, _token) in enumerate(tokens)
    )


def _unsupported_call_shape(text, tokens):
    if not _contains_call(tokens):
        return False
    return (
        _has_macro_ref(text)
        or "--" in _QUOTED_LITERAL_RE.sub("", text)
        or any(
            kind == "word"
            and (
                token.lower() in {"of", "_all_", "_numeric_", "_character_"}
                or "." in token
            )
            for kind, token in tokens
        )
    )


def _is_plain_variable_ref(expression):
    return (
        isinstance(expression, IRVariableRef)
        and bool(_BARE_IDENTIFIER_RE.fullmatch(expression.name))
        and not expression.macro_unresolved
        and expression.name.lower() not in {
            "first", "last", "of", "_all_", "_numeric_", "_character_", "_n_", "_error_",
        }
    )


def _contains_boolean_or_in(expression):
    pending = [expression]
    while pending:
        node = pending.pop()
        if isinstance(node, IRInList):
            return True
        if isinstance(node, IRBinaryOp):
            if node.operator.lower() in {"and", "or"}:
                return True
            pending.extend((node.left, node.right))
        elif isinstance(node, IRUnaryOp):
            pending.append(node.operand)
        elif isinstance(node, IRFunctionCall):
            pending.extend(node.args)
    return False


def _calls_are_pure(expression):
    if isinstance(expression, IRFunctionCall):
        return expression.name in _PURE_FUNCTIONS and all(
            _calls_are_pure(argument) for argument in expression.args
        )
    if isinstance(expression, IRInList):
        return _calls_are_pure(expression.left) and all(
            _calls_are_pure(member) for member in expression.members
        )
    if isinstance(expression, IRUnaryOp):
        return _calls_are_pure(expression.operand)
    if isinstance(expression, (IRBinaryOp, IRComparison)):
        return (
            _calls_are_pure(expression.left)
            and _calls_are_pure(expression.right)
        )
    if isinstance(expression, IRComparisonChain):
        return all(_calls_are_pure(comparison) for comparison in expression.comparisons)
    return True


def _unknown_refs(rhs, unresolved_names, span, rhs_identifiers):
    return tuple(
        _variable(name, unresolved_names, span)
        for name in rhs_identifiers(rhs)
    )


def _condition_identifiers(text):
    """Return condition variable names without treating calls as variables."""

    tokens = _tokenize(text)
    references = []
    for index, (kind, token) in enumerate(tokens):
        if kind != "word" or token.lower() in _CONDITION_IDENTIFIER_EXCLUDED:
            continue
        if index + 1 < len(tokens) and tokens[index + 1][0] == "(":
            continue
        references.append(token)
    return list(dict.fromkeys(references))


def _unknown_condition(text, condition_span, unresolved_names):
    return IRUnknownExpression(
        text,
        condition_span,
        _unknown_refs(
            text, unresolved_names, condition_span, _condition_identifiers,
        ),
    )


def parse_assignment(
    statement_text,
    source,
    unresolved_names,
    literal_value,
    rhs_identifiers,
    condition=None,
):
    """Parse one resolved assignment statement into ``IRAssignment``."""

    match = _ASSIGNMENT_RE.match(statement_text)
    if not match:
        return None
    span = source_span(source)
    target, rhs = match.group(1), match.group(2).strip()
    target_ref = _variable(target, unresolved_names, span)
    expression_span = source_span(span, rhs)
    value = literal_value(rhs)
    if value is not None:
        expression = IRLiteral(value, expression_span)
    else:
        tokens = _tokenize(rhs)
        try:
            if _unsupported_call_shape(rhs, tokens):
                raise _UnsupportedExpression
            expression = _ExpressionParser(
                rhs, unresolved_names, expression_span, literal_value,
            ).parse()
            if any(
                kind == "word" and token.lower() == "in"
                for kind, token in tokens
            ) and any(
                reference.name.lower() == "in"
                for reference in iter_variable_refs(expression)
            ):
                raise _UnsupportedExpression
            if _contains_boolean_or_in(expression):
                if any(
                    not _is_plain_variable_ref(reference)
                    for reference in iter_variable_refs(expression)
                ):
                    raise _UnsupportedExpression
                expression = _normalize_condition_expression(
                    expression, literal_value,
                )
            if not _calls_are_pure(expression):
                raise _UnsupportedExpression
        except (_UnsupportedExpression, RecursionError):
            expression = IRUnknownExpression(
                rhs,
                expression_span,
                _unknown_refs(rhs, unresolved_names, span, rhs_identifiers),
            )
    return IRAssignment(target_ref, expression, condition, span)


def _is_comparison_operator(operator):
    return (
        operator.upper() in _COMPARISON_SYMBOLS
        or operator.lower() in _COMPARISON_WORDS
    )


def _condition_operand(expression, literal_value):
    if (
        isinstance(expression, IRUnaryOp)
        and expression.operator == "-"
        and isinstance(expression.operand, IRLiteral)
    ):
        value = literal_value("-" + str(expression.operand.value))
        if value is not None:
            return IRLiteral(value, expression.source_span)
    return expression


def _condition_comparisons(expression, literal_value):
    if (
        not isinstance(expression, IRBinaryOp)
        or not _is_comparison_operator(expression.operator)
    ):
        return None

    operator = _COMPARISON_OPERATOR_ALIASES.get(
        expression.operator.lower(), expression.operator,
    ).upper()
    span = expression.source_span
    left = expression.left
    if isinstance(left, IRBinaryOp) and _is_comparison_operator(left.operator):
        comparisons = _condition_comparisons(left, literal_value)
        if comparisons is None:
            return None
        return comparisons + (
            IRComparison(
                _condition_operand(left.right, literal_value),
                operator,
                _condition_operand(expression.right, literal_value),
                span,
            ),
        )
    return (
        IRComparison(
            _condition_operand(left, literal_value),
            operator,
            _condition_operand(expression.right, literal_value),
            span,
        ),
    )


def _is_supported_condition_operand(operand):
    return (
        isinstance(operand, (IRVariableRef, IRLiteral))
        or isinstance(operand, IRFunctionCall) and _calls_are_pure(operand)
        or isinstance(operand, IRUnaryOp)
        and operand.operator.lower() == "not"
        and _is_supported_condition_operand(operand.operand)
    )


def _comparisons_are_supported(comparisons):
    if not comparisons or any(
        not _is_supported_condition_operand(operand)
        for comparison in comparisons
        for operand in (comparison.left, comparison.right)
    ):
        return False
    if len(comparisons) == 1:
        return True
    operators = {comparison.operator.upper() for comparison in comparisons}
    ascending = {"<", "<=", "LT", "LE"}
    descending = {">", ">=", "GT", "GE"}
    return operators <= ascending or operators <= descending


def _normalize_condition_expression(expression, literal_value):
    if (
        isinstance(expression, IRBinaryOp)
        and expression.operator.lower() in {"and", "or"}
    ):
        return IRBinaryOp(
            _normalize_condition_expression(expression.left, literal_value),
            expression.operator.lower(),
            _normalize_condition_expression(expression.right, literal_value),
            expression.source_span,
        )
    if (
        isinstance(expression, IRUnaryOp)
        and expression.operator.lower() == "not"
    ):
        return IRUnaryOp(
            expression.operator,
            _normalize_condition_expression(expression.operand, literal_value),
            expression.source_span,
        )
    if isinstance(expression, IRInList):
        if not (
            _is_plain_variable_ref(expression.left)
            and all(
                isinstance(member, IRLiteral)
                or _is_plain_variable_ref(member)
                or isinstance(member, IRFunctionCall) and _calls_are_pure(member)
                for member in expression.members
            )
        ):
            raise _UnsupportedExpression
        return expression
    if isinstance(expression, IRFunctionCall):
        if not _calls_are_pure(expression):
            raise _UnsupportedExpression
        return expression

    comparisons = _condition_comparisons(expression, literal_value)
    if not _comparisons_are_supported(comparisons):
        raise _UnsupportedExpression
    if len(comparisons) == 1:
        return comparisons[0]
    return IRComparisonChain(comparisons, expression.source_span)


def parse_condition(condition_text, source, literal_value, unresolved_names=()):
    """Parse supported comparisons, boolean combinations and IN lists."""

    span = source_span(source)
    text = condition_text.strip()
    condition_span = source_span(span, text)
    try:
        tokens = _tokenize(text)
        if _unsupported_call_shape(text, tokens):
            raise _UnsupportedExpression
        expression = _ExpressionParser(
            text, unresolved_names, condition_span, literal_value,
            condition=True,
        ).parse()
        if any(
            not _is_plain_variable_ref(reference)
            for reference in iter_variable_refs(expression)
        ):
            raise _UnsupportedExpression
        return _normalize_condition_expression(expression, literal_value)
    except (_UnsupportedExpression, RecursionError):
        return _unknown_condition(text, condition_span, unresolved_names)


def _add_unknown_finding(ctx, source, expression, step_id):
    finding_source = dict(source)
    finding_source["rule"] = "data_step_unknown_expression"
    ctx.add_finding(
        "unknown_expression",
        "NOT_EXECUTED",
        "WARNING",
        expression.source_text,
        f"Expression `{expression.source_text}` is outside the supported "
        "DATA-step IR grammar; no unsupported semantics were inferred.",
        "Review the expression manually or extend the DATA-step IR grammar.",
        finding_source,
        affected_nodes=[step_id],
    )


def emit_assignment(
    assignment, ctx, step_id, source, bind_variable, bind_condition_variable=None,
):
    """Emit the existing variable edges from an ``IRAssignment``."""

    value = assignment.value
    if isinstance(value, IRUnknownExpression):
        _add_unknown_finding(ctx, source, value, step_id)

    emitted = set()
    for reference in iter_variable_refs(value):
        key = (reference.name, reference.macro_unresolved)
        if key in emitted:
            continue
        emitted.add(key)
        variable_id = bind_variable(reference)
        ctx.add_edge(
            "reads_variable", variable_id, step_id, source,
            evidence=_edge_evidence(ctx, source, variable_id, step_id),
            value=None, operator=None,
        )

    target_id = bind_variable(assignment.target)
    literal = value.value if isinstance(value, IRLiteral) else None
    ctx.add_edge(
        "writes_variable", step_id, target_id, source,
        evidence=_edge_evidence(ctx, source, step_id, target_id),
        value=literal, operator=None,
    )

    if not ctx.derivation_v1:
        return

    expression_span = getattr(value, "source_span", None)
    expression_text = (
        expression_span.original_text if expression_span is not None else None
    )
    if isinstance(value, IRUnknownExpression):
        expression_text = value.source_text
    ctx.add_edge(
        "derives", step_id, target_id, source,
        evidence=_edge_evidence(ctx, source, step_id, target_id),
        expression=expression_text,
    )

    condition = assignment.condition
    if not isinstance(
        condition, (
            IRComparison, IRComparisonChain, IRFunctionCall, IRUnaryOp,
            IRBinaryOp, IRInList,
        )
    ):
        return

    condition_span = getattr(condition, "source_span", None)
    condition_text = (
        condition_span.original_text if condition_span is not None else None
    )
    condition_binder = bind_condition_variable or bind_variable
    emitted = set()
    for reference in iter_variable_refs(condition):
        key = (reference.name.lower(), reference.macro_unresolved)
        if key in emitted:
            continue
        emitted.add(key)
        variable_id = condition_binder(reference)
        ctx.add_edge(
            "conditioned_by", variable_id, step_id, source,
            evidence=_edge_evidence(ctx, source, variable_id, step_id),
            condition_text=condition_text,
        )


def emit_condition(condition, ctx, step_id, source, bind_variable):
    """Emit condition reads, or report an unsupported condition."""

    if isinstance(condition, IRUnknownExpression):
        _add_unknown_finding(ctx, source, condition, step_id)
        return

    deduplicate = not isinstance(condition, (IRComparison, IRComparisonChain))
    emitted = set()

    def emit_read(reference, value=None, operator=None):
        key = (reference.name.lower(), reference.macro_unresolved, value, operator)
        if deduplicate and key in emitted:
            return
        emitted.add(key)
        variable_id = bind_variable(reference)
        ctx.add_edge(
            "reads_variable", variable_id, step_id, source,
            evidence=_edge_evidence(ctx, source, variable_id, step_id),
            value=value, operator=operator,
        )

    def visit(expression):
        if isinstance(expression, IRComparison):
            for operand, other, is_left in (
                (expression.left, expression.right, True),
                (expression.right, expression.left, False),
            ):
                if isinstance(operand, IRVariableRef):
                    emit_read(
                        operand,
                        other.value if is_left and isinstance(other, IRLiteral) else None,
                        expression.operator,
                    )
                else:
                    for reference in iter_variable_refs(operand):
                        emit_read(reference)
        elif isinstance(expression, IRComparisonChain):
            for comparison in expression.comparisons:
                visit(comparison)
        elif (
            isinstance(expression, IRBinaryOp)
            and expression.operator.lower() in {"and", "or"}
        ):
            visit(expression.left)
            visit(expression.right)
        elif isinstance(expression, IRUnaryOp):
            visit(expression.operand)
        elif isinstance(expression, (IRFunctionCall, IRInList)):
            for reference in iter_variable_refs(expression):
                emit_read(reference)

    visit(condition)
