# ADR 0004: Separate evidence classes and preserve unknowns

## Context

An edge confidence number alone cannot explain how a fact was obtained or what
was unresolved. Static analysis must expose uncertainty instead of inventing a
probability or concrete identity.

## Decision

Represent graph facts with distinct observed, resolved, inferred, and unknown
evidence classes plus a derivation trail. Deterministic facts may have
certainty `1.0`; unresolved facts remain unknown, with `confidence: null`.
Per ticket 11, `evidence` is fenced behind a `GraphContext` factory, and the
validator is the enforcement point for the resulting shape.

## Consequences

Consumers can distinguish direct observations from derived or unresolved facts
without guessing from a number. New evidence writes must use the factory, and
malformed evidence will be rejected by the graph validator when that field is
introduced.
