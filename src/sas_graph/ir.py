"""Small, dependency-free intermediate representation for DATA-step lineage.

This module intentionally models only the assignment and condition slice used by
the DATA-step rules.  It is not a parser AST: unsupported source remains an
``IRUnknownExpression`` so callers can report it without guessing.
"""

from dataclasses import dataclass
from typing import Iterator, Optional, Protocol, Tuple, Union


@dataclass(frozen=True)
class SourceSpan:
    """The source location carried by an IR operation or expression."""

    file: str = ""
    line_start: int = 0
    line_end: int = 0
    statement_order: int = 0
    original_text: str = ""


@dataclass(frozen=True)
class IRVariableRef:
    """A variable name as it appeared in the resolved statement text."""

    name: str
    macro_unresolved: bool = False
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRLiteral:
    """A literal value represented by the current DATA-step contract."""

    value: Optional[str]
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRUnaryOp:
    operator: str
    operand: "IRExpression"
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRBinaryOp:
    left: "IRExpression"
    operator: str
    right: "IRExpression"
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRFunctionCall:
    """A function call and its parsed argument expressions."""

    name: str
    args: Tuple["IRExpression", ...]
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRComparison:
    left: "IRExpression"
    operator: str
    right: "IRExpression"
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRComparisonChain:
    """A sequence of comparisons sharing adjacent operands."""

    comparisons: Tuple[IRComparison, ...]
    source_span: Optional[SourceSpan] = None


@dataclass(frozen=True)
class IRInList:
    """A variable tested against a list of values."""

    left: "IRExpression"
    members: Tuple["IRExpression", ...]
    negated: bool = False
    source_span: Optional[SourceSpan] = None


class IRCaseExpression(Protocol):
    """Structural shape of the SQL IR's case expression node."""

    condition: "IRExpression"
    then_expression: "IRExpression"
    else_expression: Optional["IRExpression"]
    source_span: Optional[SourceSpan]
    supported: bool


@dataclass(frozen=True)
class IRUnknownExpression:
    """An expression outside the deliberately small supported grammar."""

    source_text: str
    source_span: Optional[SourceSpan] = None
    # Assignment fallback references preserve the existing conservative edge
    # set while the exact expression remains explicitly unknown.
    references: Tuple[IRVariableRef, ...] = ()

    @property
    def text(self):
        """Compatibility spelling for consumers that call it ``text``."""
        return self.source_text


@dataclass(frozen=True)
class IRAssignment:
    target: IRVariableRef
    value: "IRExpression"
    condition: Optional[
        Union[
            IRComparison, IRComparisonChain, IRUnaryOp, IRFunctionCall,
            IRBinaryOp, IRInList,
        ]
    ] = None
    source_span: Optional[SourceSpan] = None

    @property
    def expression(self):
        """The plan calls the RHS an expression; ``value`` is the edge term."""
        return self.value


IRExpression = Union[
    IRVariableRef,
    IRLiteral,
    IRUnaryOp,
    IRBinaryOp,
    IRFunctionCall,
    IRComparison,
    IRComparisonChain,
    IRInList,
    IRCaseExpression,
    IRUnknownExpression,
]


def iter_variable_refs(node) -> Iterator[IRVariableRef]:
    """Yield every variable reference reachable from an IR subtree."""

    if node is None:
        return
    if isinstance(node, IRVariableRef):
        yield node
    elif isinstance(node, IRAssignment):
        yield from iter_variable_refs(node.target)
        yield from iter_variable_refs(node.value)
        yield from iter_variable_refs(node.condition)
    elif isinstance(node, IRInList):
        yield from iter_variable_refs(node.left)
        for member in node.members:
            yield from iter_variable_refs(member)
    elif isinstance(node, (IRUnaryOp,)):
        yield from iter_variable_refs(node.operand)
    elif isinstance(node, IRFunctionCall):
        for argument in node.args:
            yield from iter_variable_refs(argument)
    elif isinstance(node, IRComparisonChain):
        for comparison in node.comparisons:
            yield from iter_variable_refs(comparison)
    elif isinstance(node, (IRBinaryOp, IRComparison)):
        yield from iter_variable_refs(node.left)
        yield from iter_variable_refs(node.right)
    # SQL owns IR case-node traversal, so this helper deliberately skips it.
    elif isinstance(node, IRUnknownExpression):
        yield from node.references
