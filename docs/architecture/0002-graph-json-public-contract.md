# ADR 0002: Keep `graph.json` as the public contract

## Context

The tool is intended to run locally with no graph database or service
infrastructure. CLI renderers, the VS Code extension, webview conversion, and
language-model tools all consume the graph artifact.

## Decision

Keep `graph.json` as the public contract. Consumers may build in-memory indexes
over the JSON, but the core must not require Neo4j, NetworkX, or another graph
database at runtime.

## Consequences

Schema changes must preserve a readable JSON artifact and be checked against
its consumers. Query performance improvements belong in bounded in-memory
indexes, while persistence and infrastructure remain outside the core.
