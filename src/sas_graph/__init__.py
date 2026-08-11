"""Source-only static dependency graph for SAS task programs.

`graph.json` is canonical; Mermaid and findings are derived views (dev plan
section 4.1). Renderers always reload from JSON rather than reading an
in-memory graph, per the generation order in section 4.2.
"""

SCHEMA_VERSION = "0.2.0"
