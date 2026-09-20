# ADR 0003: Add a semantic IR instead of exposing a full AST

## Context

A complete SAS AST would be large, parser-specific, and costly to keep stable
across frontends. The product needs only the facts required for lineage,
impact analysis, and evidence.

## Decision

Define a semantic intermediate representation for program, block, operation,
dataset, variable, macro, expression, predicate, join, key, binding, source
span, and extraction-diagnostic facts. Do not expose a full parser AST as the
public model; frontends translate into the IR.

## Consequences

The contract stays focused on consumer needs and permits a later parser
frontend without coupling every consumer to its concrete syntax tree. IR
coverage must be expanded deliberately when a lineage or impact use case
requires a new fact.
