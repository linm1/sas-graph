"""Parse and emit the DATA-step assignment/condition IR slice.

The parser in this module is intentionally small.  It recognizes literals,
variable references, and ordinary unary/binary expressions; anything else is
retained verbatim as ``IRUnknownExpression``.  Graph mutation is confined to
the two emit functions, which consume IR objects and never parse source text.
"""

import re

from .ir import (
    IRAssignment,
    IRBinaryOp,
    IRComparison,
    IRLiteral,
    IRUnaryOp,
    IRUnknownExpression,
    IRVariableRef,
    SourceSpan,
    iter_variable_refs,
)


_ASSIGNMENT_RE = re.compile(r"^(&?[A-Za-z_]\w*\.?)\s*=\s*(.+?);?$")
_COMPARISON_RE = re.compile(
    r"^([A-Za-z_]\w*)\s*(=>|=<|~=|\^=|>=|<=|>|<|=|\beq\b|\bne\b|\bgt\b|\blt\b|\bge\b|\ble\b)\s*(.+)$",
    re.IGNORECASE,
)
_COMPARISON_OPERATOR_ALIASES = {"=>": ">=", "=<": "<="}
_BARE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")

_BINARY_PRECEDENCE = {
    "or": 1,
    "and": 2,
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
    ">=": 3,
    "=>": 3,
    "=<": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
}


def source_span(source):
    """Convert an existing source mapping into the dependency-free IR span."""

    if isinstance(source, SourceSpan):
        return source
    return SourceSpan(
        file=source.get("file", ""),
        line_start=source.get("line_start", 0),
        line_end=source.get("line_end", 0),
        statement_order=source.get("statement_order", 0),
        original_text=source.get("original_text", ""),
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
    def __init__(self, text, unresolved_names, span, literal_value):
        self.text = text
        self.tokens = _tokenize(text)
        self.unresolved_names = unresolved_names
        self.span = span
        self.literal_value = literal_value
        self.index = 0

    def parse(self):
        if not self.tokens:
            raise _UnsupportedExpression
        expression = self._parse_binary(1)
        if self.index != len(self.tokens):
            raise _UnsupportedExpression
        return expression

    def _parse_binary(self, minimum_precedence):
        left = self._parse_unary()
        while self.index < len(self.tokens):
            kind, token = self.tokens[self.index]
            if kind != "operator":
                if kind == "word" and token.lower() in _BINARY_PRECEDENCE:
                    operator = token.lower()
                else:
                    break
            else:
                operator = token.lower()
            precedence = _BINARY_PRECEDENCE.get(operator)
            if precedence is None or precedence < minimum_precedence:
                break
            self.index += 1
            right = self._parse_binary(precedence + 1)
            left = IRBinaryOp(left, operator.upper() if operator in {
                "eq", "ne", "gt", "lt", "ge", "le",
            } else operator, right, self.span)
        return left

    def _parse_unary(self):
        if self.index < len(self.tokens):
            kind, token = self.tokens[self.index]
            if (kind == "operator" and token in {"+", "-", "~", "^"}) or (
                kind == "word" and token.lower() == "not"
            ):
                self.index += 1
                return IRUnaryOp(token, self._parse_unary(), self.span)
        return self._parse_primary()

    def _parse_primary(self):
        if self.index >= len(self.tokens):
            raise _UnsupportedExpression
        kind, token = self.tokens[self.index]
        if kind == "(":
            self.index += 1
            expression = self._parse_binary(1)
            if self.index >= len(self.tokens) or self.tokens[self.index][0] != ")":
                raise _UnsupportedExpression
            self.index += 1
            return expression
        if kind == "atom":
            self.index += 1
            if token.lower().endswith("n"):
                return _variable(token, self.unresolved_names, self.span)
            return IRLiteral(self.literal_value(token), self.span)
        if kind == "number":
            self.index += 1
            return IRLiteral(token, self.span)
        if kind == "missing":
            self.index += 1
            return IRLiteral(None, self.span)
        if kind == "word":
            self.index += 1
            if token.lower() in {"_n_", "_error_"}:
                return IRLiteral(None, self.span)
            if self.index < len(self.tokens) and self.tokens[self.index][0] == "(":
                raise _UnsupportedExpression
            if token.startswith("&"):
                return _variable(token, self.unresolved_names, self.span)
            if token.lower() in {"and", "or", "eq", "ne", "gt", "lt", "ge", "le"}:
                raise _UnsupportedExpression
            return _variable(token, self.unresolved_names, self.span)
        raise _UnsupportedExpression


def _unknown_refs(rhs, unresolved_names, span, rhs_identifiers):
    return tuple(
        _variable(name, unresolved_names, span)
        for name in rhs_identifiers(rhs)
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
    value = literal_value(rhs)
    if value is not None:
        expression = IRLiteral(value, span)
    else:
        try:
            expression = _ExpressionParser(
                rhs, unresolved_names, span, literal_value,
            ).parse()
        except _UnsupportedExpression:
            expression = IRUnknownExpression(
                rhs,
                span,
                _unknown_refs(rhs, unresolved_names, span, rhs_identifiers),
            )
    return IRAssignment(target_ref, expression, condition, span)


def parse_condition(condition_text, source, literal_value):
    """Parse the one-comparison condition grammar used by the old emitter."""

    span = source_span(source)
    text = condition_text.strip()
    match = _COMPARISON_RE.match(text)
    if not match:
        return IRUnknownExpression(text, span)
    lhs, operator, rhs = match.group(1), match.group(2), match.group(3).strip()
    operator = _COMPARISON_OPERATOR_ALIASES.get(operator, operator).upper()
    lhs_ref = IRVariableRef(lhs, False, span)
    value = literal_value(rhs)
    if value is not None:
        return IRComparison(lhs_ref, operator, IRLiteral(value, span), span)
    if _BARE_IDENTIFIER_RE.match(rhs):
        return IRComparison(lhs_ref, operator, IRVariableRef(rhs, False, span), span)
    return IRUnknownExpression(text, span)


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


def emit_assignment(assignment, ctx, step_id, source, bind_variable):
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
            value=None, operator=None,
        )

    target_id = bind_variable(assignment.target)
    literal = value.value if isinstance(value, IRLiteral) else None
    ctx.add_edge(
        "writes_variable", step_id, target_id, source,
        value=literal, operator=None,
    )


def emit_condition(condition, ctx, step_id, source, bind_variable):
    """Emit condition reads, or report an unsupported condition."""

    if isinstance(condition, IRUnknownExpression):
        _add_unknown_finding(ctx, source, condition, step_id)
        return
    if not isinstance(condition, IRComparison):
        return
    literal = condition.right.value if isinstance(condition.right, IRLiteral) else None
    left_id = bind_variable(condition.left)
    ctx.add_edge(
        "reads_variable", left_id, step_id, source,
        value=literal, operator=condition.operator,
    )
    if isinstance(condition.right, IRVariableRef):
        right_id = bind_variable(condition.right)
        ctx.add_edge(
            "reads_variable", right_id, step_id, source,
            value=None, operator=condition.operator,
        )
