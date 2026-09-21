"""Standalone tests for the DATA-step assignment IR."""

import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if __name__ == "__main__" and str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from sas_graph.ir import (
    IRAssignment,
    IRBinaryOp,
    IRComparison,
    IRComparisonChain,
    IRLiteral,
    IRUnknownExpression,
    IRVariableRef,
    SourceSpan,
    iter_variable_refs,
)


def test_iter_variable_refs_walks_assignment_and_condition():
    span = SourceSpan("demo.sas", 3, 3, 2, "x = y + 1;")
    target = IRVariableRef("x", source_span=span)
    value = IRBinaryOp(
        IRVariableRef("y", source_span=span), "+", IRLiteral("1", span), span
    )
    condition = IRComparison(
        IRVariableRef("flag", source_span=span), "=", IRLiteral("Y", span), span
    )
    assignment = IRAssignment(target, value, condition, span)

    assert list(iter_variable_refs(assignment)) == [target, value.left, condition.left]


def test_iter_variable_refs_walks_comparison_chain_links():
    span = SourceSpan("demo.sas", 3, 3, 2, "if a <= b <= c then x = 1;")
    chain = IRComparisonChain(
        (
            IRComparison(IRVariableRef("a"), "<=", IRVariableRef("b"), span),
            IRComparison(IRVariableRef("b"), "<=", IRVariableRef("c"), span),
        ),
        span,
    )

    assert [reference.name for reference in iter_variable_refs(chain)] == [
        "a", "b", "b", "c",
    ]


def test_unknown_expression_keeps_exact_text_and_span():
    span = SourceSpan("demo.sas", 8, 8, 6, "if a > 1 and b < 2 then x = 1;")
    unknown = IRUnknownExpression("a > 1 and b < 2", span)

    assert unknown.source_text == "a > 1 and b < 2"
    assert unknown.text == unknown.source_text
    assert unknown.source_span == span
    assert list(iter_variable_refs(unknown)) == []


def _run_standalone():
    tests = [
        test_iter_variable_refs_walks_assignment_and_condition,
        test_unknown_expression_keeps_exact_text_and_span,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} IR tests passed")


if __name__ == "__main__":
    _run_standalone()
