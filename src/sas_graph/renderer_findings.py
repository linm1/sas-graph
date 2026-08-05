"""Render findings.md from graph.json only (dev plan section 18)."""

from .graph_io import nodes_by_id

# Section 18 fixes both the sections and their order. Findings whose status is
# not listed here would silently vanish, so they get their own tail section.
STATUS_SECTIONS = [
    ("BLOCKED", "BLOCKED"),
    ("REQUIRES_DECISION", "REQUIRES_DECISION"),
    ("UNRESOLVED_MACRO_VARIABLE", "UNRESOLVED_MACRO_VARIABLE"),
    ("UNRESOLVED_MACRO_SOURCE", "UNRESOLVED_MACRO_SOURCE"),
    ("CONTRACT_INCOMPLETE", "CONTRACT_INCOMPLETE"),
    ("MACRO_CONFLICT", "MACRO_CONFLICT"),
    ("NOT_EXECUTED", "NOT_EXECUTED"),
    ("SUPPORTED", "SUPPORTED INFORMATIONAL PATTERNS"),
]

KNOWN_STATUSES = {status for status, _ in STATUS_SECTIONS}
STATUS_GROUPS = {
    "REQUIRES_DECISION": {"CONDITIONAL_BRANCH_UNRESOLVED"},
    "SUPPORTED": {
        "STATIC_CONDITION_SUPPORTED",
        "STATIC_LOOP_SUPPORTED",
        "STATIC_LIST_LOOP_SUPPORTED",
    },
}
KNOWN_STATUSES |= {status for statuses in STATUS_GROUPS.values() for status in statuses}


def _format_source(source):
    if not source:
        return "_no source location recorded_"

    location = f"`{source.get('file')}`"
    start, end = source.get("line_start"), source.get("line_end")
    if start is not None:
        location += f" line {start}" if end in (None, start) else f" lines {start}-{end}"

    order = source.get("statement_order")
    if order is not None:
        location += f", statement {order}"

    rule = source.get("rule")
    if rule:
        location += f" (rule `{rule}`)"

    return location


def _format_finding(finding, by_id):
    lines = [
        f"### {finding.get('id')} — {finding.get('object')}",
        "",
        f"- **Type:** {finding.get('type')}",
        f"- **Severity:** {finding.get('severity')}",
        f"- **Source:** {_format_source(finding.get('source'))}",
    ]

    affected_nodes = finding.get("affected_nodes") or []
    if affected_nodes:
        labels = ", ".join(
            f"`{by_id.get(node_id, {}).get('label', node_id)}`"
            for node_id in affected_nodes
        )
        lines.append(f"- **Affected nodes:** {labels}")

    affected_edges = finding.get("affected_edges") or []
    if affected_edges:
        lines.append(
            "- **Affected edges:** "
            + ", ".join(f"`{edge_id}`" for edge_id in affected_edges)
        )

    lines += ["", finding.get("message", "")]

    action = finding.get("suggested_action")
    if action:
        lines += ["", f"**Suggested action:** {action}"]

    return lines


def render(graph):
    """Return findings.md source for a loaded graph."""
    by_id = nodes_by_id(graph)
    findings = graph.get("findings", [])

    lines = [
        "# SAS graph findings",
        "",
        f"Run status: {graph.get('run_status')}",
        "",
    ]

    for status, heading in STATUS_SECTIONS:
        matching = [
            finding for finding in findings
            if finding.get("status") in {status, *STATUS_GROUPS.get(status, ())}
        ]

        lines += [f"## {heading}", ""]
        if not matching:
            lines += ["_None._", ""]
            continue

        for finding in matching:
            lines += _format_finding(finding, by_id) + [""]

    unknown = [f for f in findings if f.get("status") not in KNOWN_STATUSES]
    if unknown:
        lines += ["## UNRECOGNISED STATUS", ""]
        for finding in unknown:
            lines += [f"_Status `{finding.get('status')}` is not a known status._", ""]
            lines += _format_finding(finding, by_id) + [""]

    return "\n".join(lines).rstrip("\n") + "\n"
