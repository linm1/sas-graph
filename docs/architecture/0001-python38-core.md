# ADR 0001: Keep the runtime core Python 3.8-compatible

## Context

The company SAS server is the limiting deployment environment. The core CLI
must remain usable with the standard library and the repository's existing
runtime dependency, while parser experiments may need newer Python versions or
native binaries.

## Decision

Keep the runtime core compatible with Python 3.8. Optional parser adapters may
target newer Python versions or native packages, but they cannot become core
requirements. This decision is verified by
`.scratch/sas-graph-abcd/python38-verification.md`; `ci.yml` also pins Python
3.8 for the core job.

## Consequences

Core changes must avoid syntax, APIs, and dependencies unavailable on Python
3.8. New parser experiments need an isolated optional environment and cannot
raise the core deployment floor.
