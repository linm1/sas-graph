"""Validate the structural graph contract without rendering it.

The validator checks the envelope version, the core keys on nodes and edges,
node-id uniqueness, edge referential integrity, registered types, and legal
edge endpoint pairs.
"""

from .graph_io import SUPPORTED_SCHEMA_VERSIONS
from .graph_schema import EDGE_ENDPOINTS, EDGE_TYPES, NODE_TYPES


_NODE_KEYS = ("id", "type", "label")
_EDGE_KEYS = ("id", "type", "from", "to")


def _reference(value):
    """Format a value so a problem message identifies the offending value."""
    return repr(value)


def _records(graph, key, problems):
    """Return a graph collection when it has the expected container shape.

    Graphs normally come from JSON and therefore contain lists.  Treat a
    malformed or absent collection as empty here so one bad collection does
    not prevent the validator from reporting the other contract problems.
    """
    if key not in graph:
        problems.append("graph key '{0}' is missing; expected a list".format(key))
        return []

    records = graph[key]
    if not isinstance(records, list):
        problems.append(
            "graph key '{0}' must be a list; found {1}".format(
                key, type(records).__name__
            )
        )
        return []
    return records


def validate_graph(graph):
    """Return every structural problem found in a loaded graph dictionary.

    The return value is a list of actionable message strings.  Validation is
    intentionally a single pass over all nodes and edges: callers can show
    every problem at once instead of handling one exception at a time.
    """
    problems = []

    if not isinstance(graph, dict):
        return ["graph must be a dictionary"]

    if "schema_version" not in graph:
        problems.append("graph is missing required key 'schema_version'")
    elif graph["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
        recognized = ", ".join(
            _reference(version) for version in sorted(SUPPORTED_SCHEMA_VERSIONS)
        )
        problems.append(
            "unsupported schema_version {0}; recognized versions: {1}".format(
                _reference(graph["schema_version"]), recognized
            )
        )

    node_ids = []
    node_types_by_id = {}
    for index, node in enumerate(_records(graph, "nodes", problems)):
        if not isinstance(node, dict):
            problems.append(
                "node at index {0} must be a dictionary with keys {1}".format(
                    index, ", ".join(_NODE_KEYS)
                )
            )
            continue

        node_id = node.get("id", "node at index {0}".format(index))
        for key in _NODE_KEYS:
            if key not in node:
                problems.append(
                    "node {0} is missing required key '{1}'".format(
                        _reference(node_id), key
                    )
                )

        if "type" in node:
            node_type = node["type"]
            if not isinstance(node_type, str) or node_type not in NODE_TYPES:
                problems.append(
                    "node {0} has unknown type {1}".format(
                        _reference(node_id), _reference(node_type)
                    )
                )

        if "id" in node:
            if any(existing == node["id"] for existing in node_ids):
                problems.append(
                    "duplicate node id {0}".format(_reference(node["id"]))
                )
            node_ids.append(node["id"])
            node_types_by_id.setdefault(node["id"], node.get("type"))

    for index, edge in enumerate(_records(graph, "edges", problems)):
        if not isinstance(edge, dict):
            problems.append(
                "edge at index {0} must be a dictionary with keys {1}".format(
                    index, ", ".join(_EDGE_KEYS)
                )
            )
            continue

        edge_id = edge.get("id", "edge at index {0}".format(index))
        for key in _EDGE_KEYS:
            if key not in edge:
                problems.append(
                    "edge {0} is missing required key '{1}'".format(
                        _reference(edge_id), key
                    )
                )

        edge_type = edge.get("type")
        if "type" in edge:
            if not isinstance(edge_type, str) or edge_type not in EDGE_TYPES:
                problems.append(
                    "edge {0} has unknown type {1}".format(
                        _reference(edge_id), _reference(edge_type)
                    )
                )

        dangling = False
        for endpoint in ("from", "to"):
            if endpoint not in edge:
                continue
            endpoint_id = edge[endpoint]
            if endpoint_id not in node_types_by_id:
                dangling = True
                problems.append(
                    "edge {0} has dangling {1} reference {2}; no node with "
                    "that id exists".format(
                        _reference(edge_id), endpoint, _reference(endpoint_id)
                    )
                )

        if (
            dangling
            or not isinstance(edge_type, str)
            or edge_type not in EDGE_ENDPOINTS
        ):
            continue
        if "from" not in edge or "to" not in edge:
            continue

        from_type = node_types_by_id[edge["from"]]
        to_type = node_types_by_id[edge["to"]]
        if not isinstance(from_type, str) or not isinstance(to_type, str):
            continue
        endpoint_pair = (from_type, to_type)
        if endpoint_pair not in EDGE_ENDPOINTS[edge_type]:
            problems.append(
                "edge {0} of type {1} has illegal endpoint pair ({2}, {3})".format(
                    _reference(edge_id),
                    _reference(edge_type),
                    _reference(from_type),
                    _reference(to_type),
                )
            )

    return problems
