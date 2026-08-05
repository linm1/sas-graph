"""LIBNAME rule (dev plan section 11.4, 21 Phase 4).

Captures `libname libref "path";` as a `Library` node, while the XLSX
engine shape (`libname libref XLSX "path";`) is an `ExternalFile`. Both
record `ctx.libref_map[libref] = resolved_path` -- section 11.5 forbids
validating physical dataset existence, so paths are stored for display only,
never opened.
"""

import re

from .macro_state import resolve_text
from .rules_external_file import external_file_id

_LIBNAME_RE = re.compile(
    r"""^libname\s+(\w+)\s+(?:
        (xlsx)\s+["'](.+?)["'](?:\s+[^;]+)? |
        ["'](.+?)["']
    )\s*;?$""",
    re.IGNORECASE | re.VERBOSE,
)


def is_libname(statement_text):
    return _LIBNAME_RE.match(statement_text) is not None


def apply(statement, ctx, let_events):
    """Apply section 11.4 to one `libname` statement. Mutates ctx."""
    match = _LIBNAME_RE.match(statement.text)
    if not match:
        return

    libref, engine, engine_path, plain_path = match.groups()
    raw_path = engine_path if engine else plain_path
    resolved_path, unresolved = resolve_text(
        raw_path, statement.statement_order, let_events, return_unresolved=True
    )

    if engine:
        external_file_id(
            raw_path, statement, ctx, let_events, "libname_xlsx_engine"
        )
        ctx.libref_map[libref.lower()] = resolved_path
        return

    node_id = f"library:{libref.lower()}"
    ctx.add_node(
        node_id,
        "Library",
        libref.lower(),
        path_expression=f'"{raw_path}"',
        resolved_path=resolved_path,
        source=statement.as_source("libname_statement"),
    )
    ctx.libref_map[libref.lower()] = resolved_path

    if unresolved:
        ctx.add_finding(
            "unresolved_macro_variable_in_libname",
            "UNRESOLVED_MACRO_VARIABLE",
            "WARNING",
            libref,
            f"Macro variable in LIBNAME path for `{libref}` has no value at "
            "this statement.",
            "Define the macro variable earlier in setup.sas.",
            statement.as_source("unresolved_macro_variable_in_libname"),
            affected_nodes=[node_id],
        )
