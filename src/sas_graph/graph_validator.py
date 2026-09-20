"""Validate the structural graph contract without rendering it.

Phase 0 keeps this validator deliberately small.  It checks the envelope
version, the core keys on nodes and edges, node-id uniqueness, and edge
referential integrity.  Type and endpoint legality belong to the later type
registry and are intentionally not checked here.
"""

from .graph_io import SUPPORTED_SCHEMA_VERSIONS


_NODE_KEYS = ("id", "type", "label")
_EDGE_KEYS = ("id", "type", "from", "to")


def _reference(value):
    """Format a value so a problem message identifies the offending value."""
    return repr(value)


def _records(graph, key):
    """Return a graph collection when it has the expected container shape.

    Graphs normally come from JSON and therefore contain lists.  Treat a
    malformed or absent collection as empty here so one bad collection does
    not prevent the validator from reporting the other contract problems.
    """
    records = graph.get(key, [])
    return records if isinstance(records, list) else []


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
    for index, node in enumerate(_records(graph, "nodes")):
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

        if "id" in node:
            if any(existing == node["id"] for existing in node_ids):
                problems.append(
                    "duplicate node id {0}".format(_reference(node["id"]))
                )
            node_ids.append(node["id"])

    for index, edge in enumerate(_records(graph, "edges")):
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

        for endpoint in ("from", "to"):
            if endpoint not in edge:
                continue
            endpoint_id = edge[endpoint]
            if not any(node_id == endpoint_id for node_id in node_ids):
                problems.append(
                    "edge {0} has dangling {1} reference {2}; no node with "
                    "that id exists".format(
                        _reference(edge_id), endpoint, _reference(endpoint_id)
                    )
                )

    return problems
