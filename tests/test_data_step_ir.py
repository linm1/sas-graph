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
    IRFunctionCall,
    IRInList,
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
    ("a > 1 and b < 2", IRBinaryOp, ["a", "b"]),
    ("x in (1, 2)", IRInList, ["x"]),
    ("x not in (a, 'B')", IRInList, ["x", "a"]),
    ("x in (&mv)", IRUnknownExpression, ["x", "mv"]),
    ("x in (1:5)", IRUnknownExpression, ["x"]),
    ("x in ()", IRUnknownExpression, ["x"]),
    ("x in (lag(x))", IRUnknownExpression, ["x"]),
    ("x in (1,", IRUnknownExpression, ["x"]),
    ("x in (1, 2", IRUnknownExpression, ["x"]),
    ("x in y", IRUnknownExpression, ["x", "in", "y"]),
    ("a > 1 and b", IRUnknownExpression, ["a", "b"]),
    ("x = a or b", IRUnknownExpression, ["x", "a", "b"]),
    ("first.x and z > 1", IRUnknownExpression, ["x", "z"]),
    ("last.x and z > 1", IRUnknownExpression, ["x", "z"]),
    ("-a", IRUnaryOp, ["a"]),
    ("(a + b) * c", IRBinaryOp, ["a", "b", "c"]),
    ("a + -b", IRBinaryOp, ["a", "b"]),
    ("a + in", IRUnknownExpression, ["a", "in"]),
    ("a + of", IRBinaryOp, ["a", "of"]),
    ("'it''s'", IRLiteral, []),
    (".", IRLiteral, []),
    ("a.b", IRVariableRef, ["a.b"]),
    ("&mv", IRVariableRef, ["&mv"]),
    ("a ** b", IRUnknownExpression, ["a", "b"]),
    ("a || b", IRUnknownExpression, ["a", "b"]),
    ("a !! b", IRUnknownExpression, ["a", "b"]),
    ("a <> b", IRUnknownExpression, ["a", "b"]),
    ("sum(a, b)", IRFunctionCall, ["a", "b"]),
    ("upcase()", IRUnknownExpression, []),
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
        ("a > -1", IRComparison, ["a"]),
        ("eq = 1", IRComparison, ["eq"]),
        ("a = ge", IRComparison, ["a", "ge"]),
        ("a = b", IRComparison, ["a", "b"]),
        ("upcase(x) = 'A'", IRComparison, ["x"]),
        ("missing(x)", IRFunctionCall, ["x"]),
        ("not missing(x)", IRUnaryOp, ["x"]),
        ("a > 1 and b < 2", IRBinaryOp, ["a", "b"]),
        ("a > 1 OR b < 2", IRBinaryOp, ["a", "b"]),
        ("a > 1 AND b < 2", IRBinaryOp, ["a", "b"]),
        ("order = 1", IRComparison, ["order"]),
        ("android = 1", IRComparison, ["android"]),
        ("and_x = 1", IRComparison, ["and_x"]),
        ("in_flag = 1", IRComparison, ["in_flag"]),
        ("index = 1", IRComparison, ["index"]),
        ("inx = 1", IRComparison, ["inx"]),
        ("x in(1, 2)", IRInList, ["x"]),
        ("x in('A')", IRInList, ["x"]),
        ("x not in(1)", IRInList, ["x"]),
        ("xin (1)", IRUnknownExpression, []),
        ("x in (1, 2)", IRInList, ["x"]),
        ("x not in ('A', 'B')", IRInList, ["x"]),
        ("x NOT IN ('A', 'B')", IRInList, ["x"]),
        ("x in (a, b)", IRInList, ["x", "a", "b"]),
        ("x in (upcase(a), b)", IRInList, ["x", "a", "b"]),
        ("not (x in (1, 2))", IRUnaryOp, ["x"]),
        ("(a > 1 and b < 2) or c > 3", IRBinaryOp, ["a", "b", "c"]),
        ("a > 1 and b", IRUnknownExpression, ["a", "b"]),
        ("x AND y > 1", IRUnknownExpression, ["x", "y"]),
    ]
    for text, expected_type, expected_references in cases:
        condition = parse_condition(text, SOURCE, _literal_value)
        assert type(condition) is expected_type, repr(text)
        references = [reference.name for reference in iter_variable_refs(condition)]
        assert references == expected_references, repr(text)
    not_in = parse_condition("x NOT IN ('A', 'B')", SOURCE, _literal_value)
    assert not_in.negated is True
    in_list = parse_condition("x in (1)", SOURCE, _literal_value)
    assert isinstance(in_list, IRInList) and in_list.negated is False
    precedence = parse_condition("a > 1 or b < 2 and c > 3", SOURCE, _literal_value)
    assert precedence.operator == "or"
    assert precedence.right.operator == "and"


def test_new_unsupported_condition_forms_stay_unknown():
    for text in (
        "x in (1:5)",
        "x in (&mv)",
        "x in (lag(x))",
        "not x in (1)",
        "x in ()",
        "x in (1,",
        "x in (1, 2",
        "x & y",
        "x > 1 & y",
        "&mv",
        "first.x and y > 1",
        "lag(x) > 1 or y > 2",
        "x = a or b",
        "x like 'A'",
        "x ?? 'A'",
    ):
        assert isinstance(
            parse_condition(text, SOURCE, _literal_value), IRUnknownExpression
        ), repr(text)
    range_unknown = parse_condition("x in (1:5)", SOURCE, _literal_value)
    assert [ref.name for ref in iter_variable_refs(range_unknown)] == ["x"]


def test_long_boolean_chain_parses_without_recursion_error():
    text = " and ".join(f"x{i} > 0" for i in range(100))
    condition = parse_condition(text, SOURCE, _literal_value)

    assert not isinstance(condition, IRUnknownExpression)
    assert [ref.name for ref in iter_variable_refs(condition)] == [
        f"x{i}" for i in range(100)
    ]


def test_parenthesized_missing_and_automatic_variable_conditions():
    for text, expected_references in (
        ("(x > 1)", ["x"]),
        ("a ne .", ["a"]),
        # SAS _N_ is automatic and parsed as a literal, so it has no read.
        ("_n_ = 1", []),
    ):
        condition = parse_condition(text, SOURCE, _literal_value)
        assert isinstance(condition, IRComparison), text
        assert [ref.name for ref in iter_variable_refs(condition)] == expected_references


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


def test_pure_function_call_whitelist_uses_exact_names():
    functions = (
        "upcase", "lowcase", "strip", "trim", "left", "right", "compress",
        "compbl", "substr", "scan", "index", "find", "length", "lengthn",
        "cat", "cats", "catt", "catx", "tranwrd", "translate", "propcase",
        "coalesce", "coalescec", "ifn", "ifc", "missing", "sum", "mean",
        "min", "max", "n", "nmiss", "round", "int", "ceil", "floor", "abs",
        "mod", "datepart", "timepart", "year", "month", "day", "mdy", "intck",
        "intnx",
    )
    for name in functions:
        expression = _parse_rhs(f"{name}(x)")
        assert isinstance(expression, IRFunctionCall), name
        assert expression.name == name
        assert [ref.name for ref in iter_variable_refs(expression)] == ["x"]


def test_nested_calls_and_unsupported_call_shapes_stay_unknown():
    cases = (
        "upcase(lag(x))",
        "upcase2(x)",
        "sum(of x1-x3)",
        "mean(of a1-a10)",
        "cats(of _all_)",
        "sum(x1--x3)",
        "sum(of a--c)",
        "sum(OF x1-x3)",
        'upcase("&x")',
        "substr(a, &n, 1)",
        "cats(x, best32.)",
        "cats(x, $char8.)",
        "cats(x, yymmdd10.)",
        "cats(x, z3.)",
        "sum(_numeric_)",
        "cats(_all_)",
        "cats(_character_)",
    )
    for rhs in cases:
        expression = _parse_rhs(rhs)
        assert isinstance(expression, IRUnknownExpression), rhs
        assert expression.source_text == rhs


def test_dotted_call_tokens_keep_legacy_unknown_references():
    cases = (
        ("upcase(first.x)", ["x"]),
        ("upcase(last.x)", ["x"]),
        ("sum(a.b,c)", ["b", "c"]),
        ("upcase(work.x)", ["x"]),
    )
    for rhs, expected_references in cases:
        expression = _parse_rhs(rhs)
        assert isinstance(expression, IRUnknownExpression), rhs
        assert [ref.name for ref in iter_variable_refs(expression)] == expected_references


def test_plain_double_hyphen_parses_as_subtraction_of_a_negative():
    expression = _parse_rhs("x--y")

    assert isinstance(expression, IRBinaryOp)
    assert expression.operator == "-"
    assert isinstance(expression.right, IRUnaryOp)
    assert expression.right.operator == "-"
    assert [ref.name for ref in iter_variable_refs(expression)] == ["x", "y"]


def test_parsed_numeric_argument_is_not_a_format_variable_read():
    expression = _parse_rhs("cats(x, 8.2)")

    assert isinstance(expression, IRFunctionCall)
    assert [ref.name for ref in iter_variable_refs(expression)] == ["x"]


def test_double_hyphen_inside_string_argument_is_not_a_range_list():
    expression = _parse_rhs("cats('x--y')")

    assert isinstance(expression, IRFunctionCall)
    assert list(iter_variable_refs(expression)) == []


def test_assignment_rhs_boolean_and_in_require_plain_variable_refs():
    cases = (
        ("first.id and last.id", ["id"]),
        ("first.id or a", ["id", "a"]),
        ("a and of", ["a"]),
        ("a and _all_", ["a"]),
        ("a and &mv", ["a", "mv"]),
        ("(a > &mv) and b", ["a", "mv", "b"]),
        ("a & b", ["a", "b"]),
        ("a | b", ["a", "b"]),
        ("x in (lag(y))", ["x", "y"]),
        ("x in (a+1)", ["x", "a"]),
        ("a + b in (1)", ["a", "b"]),
        ("a > &mv and b < 2", ["a", "mv", "b"]),
        ("first.id > 1 and b < 2", ["id", "b"]),
        ("first.id = 1 or last.id = 1", ["id"]),
        ("a > 1 and b < of", ["a", "b"]),
        ("a > 1 and b < _all_", ["a", "b"]),
        ("a > 1 and b = &mv.", ["a", "b"]),
    )
    for rhs, expected_references in cases:
        expression = _parse_rhs(rhs)
        assert type(expression) is IRUnknownExpression, rhs
        assert [ref.name for ref in iter_variable_refs(expression)] == expected_references, rhs


def test_not_in_is_case_insensitive_and_sets_negated_flag():
    for text in ("x NOT IN (1, 2)", "x Not In (1, 2)"):
        expression = parse_condition(text, SOURCE, _literal_value)
        assert isinstance(expression, IRInList), text
        assert expression.negated is True
        assert [ref.name for ref in iter_variable_refs(expression)] == ["x"]

    compound = parse_condition("x = 1 or y NOT IN ('A')", SOURCE, _literal_value)
    assert isinstance(compound, IRBinaryOp)
    assert isinstance(compound.right, IRInList) and compound.right.negated is True
    assignment = _parse_rhs("x NOT IN (1, 2)")
    assert isinstance(assignment, IRInList) and assignment.negated is True


def test_boolean_precedence_has_the_expected_ir_tree():
    expression = parse_condition("a = 1 or b = 2 and c = 3", SOURCE, _literal_value)
    assert isinstance(expression, IRBinaryOp) and expression.operator == "or"
    assert isinstance(expression.left, IRComparison)
    assert isinstance(expression.right, IRBinaryOp) and expression.right.operator == "and"
    assert isinstance(expression.right.left, IRComparison)
    assert isinstance(expression.right.right, IRComparison)

    # SAS NOT binds above comparison; grouping is needed to negate the comparison.
    expression = parse_condition("not a = 1 and b = 2", SOURCE, _literal_value)
    assert isinstance(expression, IRBinaryOp) and expression.operator == "and"
    assert isinstance(expression.left, IRComparison)
    assert isinstance(expression.left.left, IRUnaryOp)
    assert expression.left.left.operator.lower() == "not"
    assert isinstance(expression.left.left.operand, IRVariableRef)
    assert expression.left.left.operand.name == "a"
    assert isinstance(expression.left.right, IRLiteral)
    assert isinstance(expression.right, IRComparison)


def test_parenthesized_not_operands_remain_primaries():
    cases = (
        ("not (a = 1)", IRComparison),
        ("not (a = 1 and b = 2)", IRBinaryOp),
        ("not missing(x)", IRFunctionCall),
        ("not (x in (1, 2))", IRInList),
    )
    for text, operand_type in cases:
        expression = parse_condition(text, SOURCE, _literal_value)
        assert isinstance(expression, IRUnaryOp), text
        assert expression.operator.lower() == "not", text
        assert isinstance(expression.operand, operand_type), text


def test_assignment_rhs_boolean_detection_walks_nested_nodes():
    cases = (
        ("sum(first, b and c)", IRUnknownExpression, ["first", "b", "c"]),
        ("sum(first, x in (1, 2))", IRUnknownExpression, ["first", "x"]),
        ("not (first and b)", IRUnknownExpression, ["first", "b"]),
        ("(a and b) + first", IRUnknownExpression, ["a", "b", "first"]),
        ("first + (x in (1, 2))", IRUnknownExpression, ["first", "x"]),
        ("sum(first, b)", IRFunctionCall, ["first", "b"]),
        ("first + b", IRBinaryOp, ["first", "b"]),
        ("not first", IRUnaryOp, ["first"]),
        ("sum(a, b and c)", IRFunctionCall, ["a", "b", "c"]),
    )
    for rhs, expected_type, expected_references in cases:
        expression = _parse_rhs(rhs)
        assert type(expression) is expected_type, rhs
        assert [ref.name for ref in iter_variable_refs(expression)] == expected_references, rhs


def test_expression_term_caps_have_separate_exact_boundaries():
    # Logical words count separately from the other binary operators and words.
    for operator in ("and", "or"):
        for operator_count, expected_type in (
            (200, IRFunctionCall),
            (201, IRUnknownExpression),
        ):
            operands = [f"x{index}" for index in range(operator_count + 1)]
            rhs = "sum(" + f" {operator} ".join(operands) + ")"
            assert type(_parse_rhs(rhs)) is expected_type, (operator, operator_count)

    for operator_count, expected_type in (
        (200, IRBinaryOp),
        (201, IRUnknownExpression),
    ):
        rhs = " + ".join(f"x{index}" for index in range(operator_count + 1))
        assert type(_parse_rhs(rhs)) is expected_type, ("+", operator_count)

        condition = " < ".join(f"x{index}" for index in range(operator_count + 1))
        assert type(parse_condition(condition, SOURCE, _literal_value)) is (
            IRComparisonChain if operator_count == 200 else IRUnknownExpression
        ), ("<", operator_count)

    rhs = "sum(" + " + ".join("x" for _ in range(202)) + " and y)"
    assert isinstance(_parse_rhs(rhs), IRUnknownExpression)


def test_large_boolean_arithmetic_and_not_chains_fall_back_unknown():
    for size in (1000, 1500, 3000):
        for operator in ("and", "or"):
            condition = f" {operator} ".join(
                f"x{index} > 0" for index in range(size)
            )
            assert isinstance(
                parse_condition(condition, SOURCE, _literal_value),
                IRUnknownExpression,
            ), (size, operator)
            assert isinstance(
                _parse_rhs(condition), IRUnknownExpression,
            ), ("rhs", size, operator)

        arithmetic = " + ".join("x" for _ in range(size))
        assert isinstance(_parse_rhs(arithmetic), IRUnknownExpression), (
            "rhs +", size,
        )

        not_expression = "not " * size + "x = 1"
        assert isinstance(
            parse_condition(not_expression, SOURCE, _literal_value),
            IRUnknownExpression,
        ), ("condition not", size)
        assert isinstance(_parse_rhs(not_expression), IRUnknownExpression), (
            "rhs not", size,
        )



STANDALONE_TESTS = [
    test_assignment_expression_table,
    test_tokenizer_table,
    test_name_and_date_literal_nodes,
    test_condition_table,
    test_new_unsupported_condition_forms_stay_unknown,
    test_long_boolean_chain_parses_without_recursion_error,
    test_chained_condition_keeps_each_comparison_and_middle_reference,
    test_direction_inconsistent_chains_are_unknown,
    test_same_direction_chains_remain_comparison_chains,
    test_iter_variable_refs_table,
    test_pure_function_call_whitelist_uses_exact_names,
    test_nested_calls_and_unsupported_call_shapes_stay_unknown,
    test_dotted_call_tokens_keep_legacy_unknown_references,
    test_plain_double_hyphen_parses_as_subtraction_of_a_negative,
    test_parsed_numeric_argument_is_not_a_format_variable_read,
    test_double_hyphen_inside_string_argument_is_not_a_range_list,
    test_assignment_rhs_boolean_and_in_require_plain_variable_refs,
    test_not_in_is_case_insensitive_and_sets_negated_flag,
    test_boolean_precedence_has_the_expected_ir_tree,
    test_assignment_rhs_boolean_detection_walks_nested_nodes,
    test_expression_term_caps_have_separate_exact_boundaries,
    test_large_boolean_arithmetic_and_not_chains_fall_back_unknown,
]


if __name__ == "__main__":
    for test in STANDALONE_TESTS:
        test()
    print(f"{len(STANDALONE_TESTS)} DATA-step IR tests passed")
