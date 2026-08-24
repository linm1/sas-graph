"""Pure queries over an already-loaded ``graph.json`` dictionary."""

from .graph_io import nodes_by_id
from .variable_lineage_walk import walk_variable_impact

_LINEAGE_EDGE_TYPES = {"reads_dataset", "writes_dataset"}
_LINEAGE_NODE_TYPES = {"Dataset", "Step", "SqlStatement"}
_IMPACT_NODE_TYPES = {"Variable", "UnknownVariable"}
_DIRECTIONS = {"upstream", "downstream", "both"}
_SOURCE_KEYS = ("file", "line_start", "line_end")


def _source(source):
    if not isinstance(source, dict):
        return None
    return {key: source.get(key) for key in _SOURCE_KEYS}


def _node_result(node):
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "label": node.get("label"),
        "source": _source(node.get("source")),
    }


def _edge_result(edge):
    return {
        "id": edge.get("id"),
        "type": edge.get("type"),
        "from": edge.get("from"),
        "to": edge.get("to"),
        "source": _source(edge.get("source")),
    }


def search_nodes(graph, query):
    """Return nodes whose label or type contains ``query`` case-insensitively."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")

    needle = query.casefold()
    matches = []
    for node in graph.get("nodes", []):
        label = str(node.get("label", ""))
        node_type = str(node.get("type", ""))
        if needle in label.casefold() or needle in node_type.casefold():
            matches.append(_node_result(node))
    return {"query": query, "matches": matches}


def _walk_lineage(start_id, edges, nodes, direction):
    """Walk oriented read/write edges, returning reached nodes and edges."""
    visited = {start_id}
    frontier = [start_id]
    reached = []
    walked_edges = []
    walked_edge_ids = set()

    while frontier:
        current = frontier.pop(0)
        for edge in edges:
            if edge.get("type") not in _LINEAGE_EDGE_TYPES:
                continue
            if direction == "upstream":
                matches = edge.get("to") == current
                neighbor = edge.get("from")
            else:
                matches = edge.get("from") == current
                neighbor = edge.get("to")
            if not matches:
                continue
            edge_id = edge.get("id")
            if edge_id not in walked_edge_ids:
                walked_edge_ids.add(edge_id)
                walked_edges.append(_edge_result(edge))
            if neighbor in visited:
                continue
            if neighbor not in nodes:
                raise ValueError(f"lineage edge {edge_id!r} points to missing node {neighbor!r}")
            visited.add(neighbor)
            reached.append(_node_result(nodes[neighbor]))
            frontier.append(neighbor)

    return reached, walked_edges


def trace_lineage(graph, start_id, direction="both"):
    """Trace unbounded structural Dataset/Step/SqlStatement lineage."""
    if direction not in _DIRECTIONS:
        raise ValueError("direction must be upstream, downstream, or both")

    nodes = nodes_by_id(graph)
    start = nodes.get(start_id)
    if start is None:
        raise ValueError(f"node not found: {start_id}")
    if start.get("type") not in _LINEAGE_NODE_TYPES:
        raise ValueError("lineage start must be Dataset, Step, or SqlStatement")

    edges = graph.get("edges", [])
    upstream_nodes, upstream_edges = ([], [])
    downstream_nodes, downstream_edges = ([], [])
    if direction in {"upstream", "both"}:
        upstream_nodes, upstream_edges = _walk_lineage(start_id, edges, nodes, "upstream")
    if direction in {"downstream", "both"}:
        downstream_nodes, downstream_edges = _walk_lineage(start_id, edges, nodes, "downstream")

    edges_by_id = {}
    for edge in (*upstream_edges, *downstream_edges):
        edges_by_id.setdefault(edge["id"], edge)
    return {
        "start": start_id,
        "upstream_nodes": upstream_nodes,
        "downstream_nodes": downstream_nodes,
        "edges": list(edges_by_id.values()),
    }


def analyze_impact(graph, variable_id):
    """Validate and run the existing variable-level impact walk."""
    node = nodes_by_id(graph).get(variable_id)
    if node is None:
        raise ValueError(f"variable not found: {variable_id}")
    if node.get("type") not in _IMPACT_NODE_TYPES:
        raise ValueError("impact start must be a Variable or UnknownVariable")
    return walk_variable_impact(graph, variable_id)
