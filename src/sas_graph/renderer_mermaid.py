"""Render graph.json as a Mermaid flowchart (dev plan section 17).

Review aid, not a canonical store. Shows active dataset-to-step flow, macro
calls, unknowns and conflicts; skips inactive evidence, which section 17 puts
in a separate diagram.
"""

import re

from .graph_io import nodes_by_id

# Node types that appear in the default flow diagram. Everything else
# (MacroParameter, MacroVariable, Library, Program, ...) is graph detail that
# would drown the picture.
FLOW_NODE_TYPES = {
    "Dataset",
    "UnknownDataset",
    "Step",
    "SqlStatement",
    "MacroCall",
    "UnknownMacro",
    "MacroConflict",
}

# Edges that draw the flow. depends_on is deliberately excluded: it is the
# transitive summary of reads/writes and would double every arrow.
FLOW_EDGE_TYPES = {
    "reads_dataset",
    "writes_dataset",
    "implemented_by",
    "contains_sql_statement",
    "conflicts_with",
    # Reaches an UnknownDataset from the macro call that named it. See
    # _flow_edges for why this is evidence rather than inferred dataflow.
    "resolves_to",
}

SHAPES = {
    "Dataset": ("[\"", "\"]"),
    "UnknownDataset": ("[\"", "\"]"),
    "Step": ("(\"", "\")"),
    "SqlStatement": ("(\"", "\")"),
    "MacroCall": ("{{\"", "\"}}"),
    "UnknownMacro": ("{{\"", "\"}}"),
    "MacroConflict": ("{{\"", "\"}}"),
}

CLASS_OF_TYPE = {
    "UnknownDataset": "unknown",
    "UnknownMacro": "unknown",
    "MacroConflict": "conflict",
}

CLASS_DEFS = [
    "classDef unknown fill:#ffd6dd,stroke:#c2185b,color:#3b0716;",
    "classDef conflict fill:#ffe0b2,stroke:#ef6c00,color:#3b2100;",
]


def mermaid_id(node_id):
    """Dataset ids carry dots and ampersands; Mermaid ids cannot."""
    return re.sub(r"[^0-9A-Za-z_]", "_", node_id)


def _escape(label):
    return label.replace('"', "'")


def _flow_edges(graph, drawn):
    """Yield the edges to draw, hopping over nodes the flow view hides.

    A macro call reaches an UnknownDataset through its parameter:
    MacroCall -passes_parameter-> MacroParameter -resolves_to-> UnknownDataset.
    MacroParameter is graph detail, so without a hop the unknown dataset floats
    unconnected. This is not inference — both edges are visible in the source
    text. Section 15.2 forbids inventing reads and writes for a macro whose
    source is missing, so the hop keeps the neutral `resolves_to` type rather
    than promoting it to a dataflow edge.
    """
    hops = {}
    for edge in graph.get("edges", []):
        if edge.get("type") == "passes_parameter" and edge.get("to") not in drawn:
            hops[edge["to"]] = edge["from"]

    for edge in graph.get("edges", []):
        edge_type = edge.get("type")
        source, target = edge.get("from"), edge.get("to")

        if edge_type == "resolves_to":
            source = hops.get(source, source)

        if edge_type not in FLOW_EDGE_TYPES:
            continue
        if source not in drawn or target not in drawn:
            continue

        # reads_dataset points step -> dataset in the graph, but the diagram
        # reads left to right as dataset -> step, so flip it.
        if edge_type == "reads_dataset":
            source, target = target, source

        yield {**edge, "from": source, "to": target}


def render(graph):
    """Return Mermaid source for the flow view of a loaded graph."""
    by_id = nodes_by_id(graph)
    lines = ["flowchart LR"]

    drawn = {
        node_id: node
        for node_id, node in by_id.items()
        if node.get("type") in FLOW_NODE_TYPES
    }

    for node_id, node in drawn.items():
        open_shape, close_shape = SHAPES[node["type"]]
        label = _escape(node.get("label", node_id))
        lines.append(f"  {mermaid_id(node_id)}{open_shape}{label}{close_shape}")

    for edge in _flow_edges(graph, drawn):
        source, target = edge["from"], edge["to"]
        arrow = "-.->" if edge.get("contract_derived") else "-->"
        lines.append(f"  {mermaid_id(source)} {arrow} {mermaid_id(target)}")

    styled = [
        (node_id, CLASS_OF_TYPE[node["type"]])
        for node_id, node in drawn.items()
        if node["type"] in CLASS_OF_TYPE
    ]
    if styled:
        lines.append("")
        lines.extend(f"  {definition}" for definition in CLASS_DEFS)
        for node_id, class_name in styled:
            lines.append(f"  class {mermaid_id(node_id)} {class_name};")

    return "\n".join(lines) + "\n"
