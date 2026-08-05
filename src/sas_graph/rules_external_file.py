"""Shared ExternalFile parsing helpers for PROC IMPORT and PROC EXPORT."""

from .macro_state import resolve_text


def block_text(block):
    return " ".join(statement.text for statement in block.statements)


def blank_span(text, match):
    """Blank one matched option before searching the remaining options."""
    start, end = match.span()
    return text[:start] + " " * (end - start) + text[end:]


def dataset_id(raw, statement, ctx, let_events, rule, finding_type):
    """Resolve a dataset token or preserve an unresolved one as evidence."""
    resolved, unresolved = resolve_text(
        raw, statement.statement_order, let_events, return_unresolved=True
    )
    if not unresolved:
        return ctx.add_dataset(resolved)

    source = statement.as_source(rule)
    node_id = ctx.add_unknown_dataset(raw, source)
    ctx.add_finding(
        finding_type,
        "UNRESOLVED_MACRO_VARIABLE",
        "WARNING",
        raw,
        f"Macro variable in `{raw}` has no value at this statement.",
        "Define the macro variable earlier, or confirm it is created at "
        "runtime and therefore out of static scope.",
        source,
        affected_nodes=[node_id],
    )
    return node_id


def external_file_id(raw_path, statement, ctx, let_events, rule):
    """Resolve an external path or preserve an unresolved one as evidence."""
    resolved, unresolved = resolve_text(
        raw_path, statement.statement_order, let_events, return_unresolved=True
    )
    text = raw_path if unresolved else resolved
    node_id = f"externalfile:{text}"
    ctx.add_node(node_id, "ExternalFile", text, source=None)

    if unresolved:
        source = statement.as_source(rule)
        ctx.add_finding(
            "unresolved_macro_variable_in_external_file",
            "UNRESOLVED_MACRO_VARIABLE",
            "WARNING",
            raw_path,
            f"Macro variable in `{raw_path}` has no value at this statement.",
            "Define the macro variable earlier, or confirm it is created at "
            "runtime and therefore out of static scope.",
            source,
            affected_nodes=[node_id],
        )
    return node_id
