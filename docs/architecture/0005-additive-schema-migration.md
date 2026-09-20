# ADR 0005: Make schema changes additive until a deliberate migration

## Context

Existing `0.2.0` graph consumers must remain useful while the next contract is
being designed. Renaming current edge types or maintaining multiple shapes in
every downstream consumer would make the transition ambiguous.

## Decision

Keep schema changes additive until a deliberate major migration. Introduce
`0.3.0` only with a fixture, validator, migration note, and consumer
compatibility check, and do not rename existing edge types in the first
migration. Ticket 02 refines the compatibility path to a normalizer: a
`0.2.0` graph is up-converted once, and pre-evidence edges receive
`UNKNOWN`/`NOT_ATTEMPTED`/`confidence: null` rather than invented provenance.
The normalizer must preserve `main_programs` and `setup_file`.

## Consequences

The old shape is handled at the boundary while downstream code sees one target
shape. Additive fields and explicit migration tests protect existing consumers,
but a major migration still requires a deliberate compatibility decision.
