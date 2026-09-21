"""Standalone tests for the DATA-step assignment and condition IR."""

import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if __name__ == "__main__" and str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from sas_graph.data_step_ir import _tokenize, parse_assignment, parse_condition
from sas_graph.ir import (
    IRBinaryOp,
    IRComparison,
    IRComparisonChain,
    IRLiteral,
    IRUnaryOp,
    IRUnknownExpression,
    IRVariableRef,
    iter_variable_refs,
)
from sas_graph.rules_data_step import _literal_value, _rhs_identifiers


SOURCE = {"file": "demo.sas", "line_start": 1, "line_end": 1}


def _parse_rhs(rhs):
    assignment = parse_assignment(
        "x = " + rhs,
        SOURCE,
        set(),
        _literal_value,
        _rhs_identifiers,
    )
    assert assignment is not None, repr(rhs)
    return assignment.value


EXPRESSION_CASES = [
    ("'abc", IRUnknownExpression, []),
    ("a > 1 and b < 2", IRUnknownExpression, ["a", "b"]),
    ("-a", IRUnaryOp, ["a"]),
    ("(a + b) * c", IRBinaryOp, ["a", "b", "c"]),
    ("a + -b", IRBinaryOp, ["a", "b"]),
    ("'it''s'", IRLiteral, []),
    (".", IRLiteral, []),
    ("a.b", IRVariableRef, ["a.b"]),
    ("&mv", IRVariableRef, ["&mv"]),
    ("a ** b", IRUnknownExpression, ["a", "b"]),
    ("a || b", IRUnknownExpression, ["a", "b"]),
    ("a !! b", IRUnknownExpression, ["a", "b"]),
    ("a <> b", IRUnknownExpression, ["a", "b"]),
    ("sum(a, b)", IRUnknownExpression, ["a", "b"]),
    ("1.5e3", IRUnknownExpression, []),
    ("", IRUnknownExpression, []),
    ("(a + b", IRUnknownExpression, ["a", "b"]),
    ("a +", IRUnknownExpression, ["a"]),
    ("()", IRUnknownExpression, []),
    ("(" * 2000 + "a" + ")" * 2000, IRUnknownExpression, ["a"]),
]


def test_assignment_expression_table():
    for rhs, expected_type, expected_references in EXPRESSION_CASES:
        expression = _parse_rhs(rhs)
        assert type(expression) is expected_type, repr(rhs)
        references = [reference.name for reference in iter_variable_refs(expression)]
        assert references == expected_references, repr(rhs)


TOKEN_CASES = [
    ("'it''s'", [("atom", "'it''s'")]),
    ("'abc'n", [("atom", "'abc'n")]),
    ('"20jan2020"d', [("atom", '"20jan2020"d')]),
    (
        "(a + b",
        [("(", "("), ("word", "a"), ("operator", "+"), ("word", "b")],
    ),
    ("a +", [("word", "a"), ("operator", "+")]),
    ("()", [("(", "("), (")", ")")]),
]


def test_tokenizer_table():
    for text, expected_tokens in TOKEN_CASES:
        assert _tokenize(text) == expected_tokens, repr(text)


def test_name_and_date_literal_nodes():
    name_literal = _parse_rhs("'abc'n")
    date_literal = _parse_rhs('"20jan2020"d')

    assert isinstance(name_literal, IRVariableRef)
    assert name_literal.name == "'abc'n"
    assert isinstance(date_literal, IRLiteral)
    assert date_literal.value == "20jan2020"


def test_condition_table():
    cases = [
        ("a > 1", IRComparison, ["a"]),
        ("eq = 1", IRComparison, ["eq"]),
        ("a = ge", IRComparison, ["a", "ge"]),
        ("a > 1 and b < 2", IRUnknownExpression, ["a", "b"]),
    ]
    for text, expected_type, expected_references in cases:
        condition = parse_condition(text, SOURCE, _literal_value)
        assert type(condition) is expected_type, repr(text)
        references = [reference.name for reference in iter_variable_refs(condition)]
        assert references == expected_references, repr(text)


def test_chained_condition_keeps_each_comparison_and_middle_reference():
    condition = parse_condition("a <= b <= c", SOURCE, _literal_value)

    assert type(condition) is IRComparisonChain
    assert [comparison.operator for comparison in condition.comparisons] == [
        "<=", "<=",
    ]
    assert [
        reference.name for reference in iter_variable_refs(condition)
    ] == ["a", "b", "b", "c"]


def test_direction_inconsistent_chains_are_unknown():
    for text in ("a <= b > c", "a >= b <= c"):
        condition = parse_condition(text, SOURCE, _literal_value)
        assert type(condition) is IRUnknownExpression, repr(text)


def test_same_direction_chains_remain_comparison_chains():
    for text in ("a < b < c", "a > b > c"):
        condition = parse_condition(text, SOURCE, _literal_value)
        assert type(condition) is IRComparisonChain, repr(text)


def test_expression_and_condition_spans_keep_their_source_text():
    source = {
        "file": "demo.sas",
        "line_start": 4,
        "line_end": 4,
        "statement_order": 3,
        "original_text": "if flag = other then target = a + b;",
    }
    assignment = parse_assignment(
        "target = a + b;",
        source,
        set(),
        _literal_value,
        _rhs_identifiers,
    )
    condition = parse_condition("flag = other", source, _literal_value)

    assert assignment.value.source_span.original_text == "a + b"
    assert condition.source_span.original_text == "flag = other"


def test_iter_variable_refs_table():
    nested = IRBinaryOp(IRUnaryOp("-", IRVariableRef("a")), "+", IRLiteral("1"))
    cases = [(nested, ["a"]), (IRLiteral("text"), [])]
    for node, expected_references in cases:
        assert [
            reference.name for reference in iter_variable_refs(node)
        ] == expected_references


STANDALONE_TESTS = [
    test_assignment_expression_table,
    test_tokenizer_table,
    test_name_and_date_literal_nodes,
    test_condition_table,
    test_chained_condition_keeps_each_comparison_and_middle_reference,
    test_direction_inconsistent_chains_are_unknown,
    test_same_direction_chains_remain_comparison_chains,
    test_iter_variable_refs_table,
]


if __name__ == "__main__":
    for test in STANDALONE_TESTS:
        test()
    print(f"{len(STANDALONE_TESTS)} DATA-step IR tests passed")
