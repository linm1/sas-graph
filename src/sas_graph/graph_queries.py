"""Pure, bounded queries over an already-loaded ``graph.json`` dictionary."""

from collections import deque

from .graph_index import (
    DEFAULT_DEPTH,
    DEFAULT_LIMIT,
    GraphIndex,
    validate_bounds,
)
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


def _edge_result(edge, cycle=False):
    result = {
        "id": edge.get("id"),
        "type": edge.get("type"),
        "from": edge.get("from"),
        "to": edge.get("to"),
        "source": _source(edge.get("source")),
    }
    if cycle:
        result["cycle"] = True
    return result


def _metadata(truncated, visited_count, continuation, limit_hit, depth_hit):
    frontier = []
    seen = set()
    for node_id in continuation:
        if node_id not in seen:
            seen.add(node_id)
            frontier.append(node_id)
    return {
        "truncated": truncated,
        "visited_count": visited_count,
        "limit_hit": limit_hit,
        "depth_hit": depth_hit,
        "continuation": frontier,
    }


def _record_id(record):
    return str(record.get("id", ""))


def search_nodes(graph, query, limit=DEFAULT_LIMIT, index=None):
    """Return nodes whose label or type contains ``query`` case-insensitively.

    Search is bounded by result count.  ``visited_count`` records the number
    of indexed nodes examined, while the continuation list contains matching
    node ids omitted after the result limit.
    """
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    validate_bounds(DEFAULT_DEPTH, limit)

    if index is None:
        index = GraphIndex(graph)
    needle = query.casefold()
    matches = []
    for node in sorted(index.nodes_by_id.values(), key=_record_id):
        label = str(node.get("label", ""))
        node_type = str(node.get("type", ""))
        if needle in label.casefold() or needle in node_type.casefold():
            matches.append(node)

    truncated = len(matches) > limit
    selected = matches[:limit]
    continuation = [node.get("id") for node in matches[limit:]] if truncated else []
    result = {
        "query": query,
        "matches": [_node_result(node) for node in selected],
    }
    result.update(
        _metadata(
            truncated,
            len(index.nodes_by_id),
            continuation,
            truncated,
            False,
        )
    )
    return result


def _lineage_edges(index, node_id, direction):
    by_type = (
        index.incoming_edges_by_node.get(node_id, {})
        if direction == "upstream"
        else index.outgoing_edges_by_node.get(node_id, {})
    )
    edges = []
    for edge_type in _LINEAGE_EDGE_TYPES:
        edges.extend(by_type.get(edge_type, []))
    return sorted(edges, key=_record_id)


def _lineage_depth_increment(node):
    return 1 if node.get("type") == "Dataset" else 0


def _walk_lineage(index, start_id, direction, depth, limit):
    """Walk indexed lineage edges with ticket-07 bounds.

    Only Dataset nodes consume the depth budget.  Structural nodes remain in
    the result and are traversed through without advancing the budget.
    """
    nodes = index.nodes_by_id
    visited = {start_id}
    frontier = deque([(start_id, 0)])
    reached = []
    walked_edges = []
    walked_edge_ids = set()
    continuation = []
    limit_hit = False
    depth_hit = False

    while frontier:
        current, current_depth = frontier.popleft()
        edges = _lineage_edges(index, current, direction)
        for edge in edges:
            edge_id = edge.get("id")
            if edge_id in walked_edge_ids:
                continue
            neighbor = edge.get("from") if direction == "upstream" else edge.get("to")
            if neighbor not in visited and neighbor not in nodes:
                raise ValueError(
                    f"lineage edge {edge_id!r} points to missing node {neighbor!r}"
                )

            is_cycle = neighbor in visited
            walked_edge_ids.add(edge_id)
            if is_cycle:
                walked_edges.append(_edge_result(edge, cycle=True))
                continue

            if len(reached) >= limit:
                limit_hit = True
                continuation.append(neighbor)
                break

            next_depth = current_depth + _lineage_depth_increment(nodes[neighbor])
            if next_depth > depth:
                depth_hit = True
                continuation.append(current)
                continue

            visited.add(neighbor)
            reached.append(_node_result(nodes[neighbor]))
            walked_edges.append(_edge_result(edge))
            frontier.append((neighbor, next_depth))

        if limit_hit:
            break

    continuation.extend(node_id for node_id, _ in frontier)
    return (
        reached,
        walked_edges,
        _metadata(
            limit_hit or depth_hit,
            len(visited),
            continuation,
            limit_hit,
            depth_hit,
        ),
    )


def trace_lineage(
    graph,
    start_id,
    direction="both",
    depth=DEFAULT_DEPTH,
    limit=DEFAULT_LIMIT,
    index=None,
):
    """Trace bounded structural Dataset/Step/SqlStatement lineage."""
    if direction not in _DIRECTIONS:
        raise ValueError("direction must be upstream, downstream, or both")
    depth, limit = validate_bounds(depth, limit)

    if index is None:
        index = GraphIndex(graph)
    start = index.nodes_by_id.get(start_id)
    if start is None:
        raise ValueError(f"node not found: {start_id}")
    if start.get("type") not in _LINEAGE_NODE_TYPES:
        raise ValueError("lineage start must be Dataset, Step, or SqlStatement")

    upstream_nodes, upstream_edges, upstream_metadata = ([], [], None)
    downstream_nodes, downstream_edges, downstream_metadata = ([], [], None)
    if direction in {"upstream", "both"}:
        upstream_nodes, upstream_edges, upstream_metadata = _walk_lineage(
            index, start_id, "upstream", depth, limit
        )
    if direction in {"downstream", "both"}:
        downstream_nodes, downstream_edges, downstream_metadata = _walk_lineage(
            index, start_id, "downstream", depth, limit
        )

    edges_by_id = {}
    for edge in (*upstream_edges, *downstream_edges):
        existing = edges_by_id.get(edge["id"])
        if existing is None or edge.get("cycle"):
            edges_by_id[edge["id"]] = edge

    if direction == "upstream":
        metadata = upstream_metadata
    elif direction == "downstream":
        metadata = downstream_metadata
    else:
        continuation = (
            upstream_metadata["continuation"] + downstream_metadata["continuation"]
        )
        visited_count = len(
            {start_id}
            | {node["id"] for node in upstream_nodes}
            | {node["id"] for node in downstream_nodes}
        )
        metadata = _metadata(
            upstream_metadata["truncated"] or downstream_metadata["truncated"],
            visited_count,
            continuation,
            upstream_metadata["limit_hit"] or downstream_metadata["limit_hit"],
            upstream_metadata["depth_hit"] or downstream_metadata["depth_hit"],
        )

    return {
        "start": start_id,
        "upstream_nodes": upstream_nodes,
        "downstream_nodes": downstream_nodes,
        "edges": list(edges_by_id.values()),
        **metadata,
    }


def analyze_impact(
    graph,
    variable_id,
    depth=DEFAULT_DEPTH,
    limit=DEFAULT_LIMIT,
    index=None,
):
    """Validate and run the bounded variable-level impact walk."""
    depth, limit = validate_bounds(depth, limit)
    if index is None:
        index = GraphIndex(graph)
    node = index.nodes_by_id.get(variable_id)
    if node is None:
        raise ValueError(f"variable not found: {variable_id}")
    if node.get("type") not in _IMPACT_NODE_TYPES:
        raise ValueError("impact start must be a Variable or UnknownVariable")
    return walk_variable_impact(
        graph,
        variable_id,
        index=index,
        depth=depth,
        limit=limit,
        include_metadata=True,
    )
