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
    IRComparisonChain,
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
    "not", "and", "or", "eq", "ne", "gt", "lt", "ge", "le", "of",
    "_n_", "_error_",
}
_MAX_PARENTHESIS_DEPTH = 100

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
    ">=": 3,
    "=>": 3,
    "=<": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
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
    def __init__(self, text, unresolved_names, span, literal_value):
        self.text = text
        self.tokens = _tokenize(text)
        self.unresolved_names = unresolved_names
        self.span = span
        self.literal_value = literal_value
        self.index = 0
        self.parenthesis_depth = 0

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


def _condition_operand(tokens, index, condition_span, literal_value):
    if index >= len(tokens):
        return None, index
    kind, token = tokens[index]
    if (
        kind == "operator"
        and token == "-"
        and index + 1 < len(tokens)
        and tokens[index + 1][0] == "number"
    ):
        token = "-" + tokens[index + 1][1]
        index += 1
        kind = "number"
    if kind in {"atom", "number"}:
        value = literal_value(token)
        if value is not None:
            return IRLiteral(value, condition_span), index + 1
        return None, index + 1
    if kind == "word" and _BARE_IDENTIFIER_RE.match(token):
        return IRVariableRef(token, False, condition_span), index + 1
    return None, index + 1


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
        try:
            expression = _ExpressionParser(
                rhs, unresolved_names, expression_span, literal_value,
            ).parse()
        except _UnsupportedExpression:
            expression = IRUnknownExpression(
                rhs,
                expression_span,
                _unknown_refs(rhs, unresolved_names, span, rhs_identifiers),
            )
    return IRAssignment(target_ref, expression, condition, span)


def parse_condition(condition_text, source, literal_value, unresolved_names=()):
    """Parse a single comparison or a chained range comparison."""

    span = source_span(source)
    text = condition_text.strip()
    condition_span = source_span(span, text)
    tokens = _tokenize(text)
    if not tokens:
        return _unknown_condition(text, condition_span, unresolved_names)

    operands = []
    index = 0
    operand, index = _condition_operand(
        tokens, index, condition_span, literal_value,
    )
    if operand is None:
        return _unknown_condition(text, condition_span, unresolved_names)
    operands.append(operand)
    operators = []
    while index < len(tokens):
        kind, token = tokens[index]
        if kind == "operator" and token in _COMPARISON_SYMBOLS:
            operator = token
        elif kind == "word" and token.lower() in _COMPARISON_WORDS:
            operator = token
        else:
            return _unknown_condition(text, condition_span, unresolved_names)
        operators.append(operator)
        operand, index = _condition_operand(
            tokens, index + 1, condition_span, literal_value,
        )
        if operand is None:
            return _unknown_condition(text, condition_span, unresolved_names)
        operands.append(operand)
    if not operators:
        return _unknown_condition(text, condition_span, unresolved_names)

    comparisons = tuple(
        IRComparison(
            operands[index],
            _COMPARISON_OPERATOR_ALIASES.get(
                operators[index].lower(), operators[index],
            ).upper(),
            operands[index + 1],
            condition_span,
        )
        for index in range(len(operators))
    )
    if len(comparisons) == 1:
        return comparisons[0]
    return IRComparisonChain(comparisons, condition_span)


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
            value=None, operator=None,
        )

    target_id = bind_variable(assignment.target)
    literal = value.value if isinstance(value, IRLiteral) else None
    ctx.add_edge(
        "writes_variable", step_id, target_id, source,
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
        expression=expression_text,
    )

    condition = assignment.condition
    if not isinstance(condition, (IRComparison, IRComparisonChain)):
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
            condition_text=condition_text,
        )


def emit_condition(condition, ctx, step_id, source, bind_variable):
    """Emit condition reads, or report an unsupported condition."""

    if isinstance(condition, IRUnknownExpression):
        _add_unknown_finding(ctx, source, condition, step_id)
        return
    if isinstance(condition, IRComparison):
        comparisons = (condition,)
    elif isinstance(condition, IRComparisonChain):
        comparisons = condition.comparisons
    else:
        return
    for comparison in comparisons:
        literal = comparison.right.value if isinstance(comparison.right, IRLiteral) else None
        if isinstance(comparison.left, IRVariableRef):
            left_id = bind_variable(comparison.left)
            ctx.add_edge(
                "reads_variable", left_id, step_id, source,
                value=literal, operator=comparison.operator,
            )
        if isinstance(comparison.right, IRVariableRef):
            right_id = bind_variable(comparison.right)
            ctx.add_edge(
                "reads_variable", right_id, step_id, source,
                value=None, operator=comparison.operator,
            )
