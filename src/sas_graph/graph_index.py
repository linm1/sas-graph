"""Read-only indexes over a loaded ``graph.json`` dictionary.

The query layer deliberately keeps the graph contract as plain dictionaries.
``GraphIndex`` is a projection of those dictionaries, not a second graph model:
it records the existing node and edge objects without changing them and keeps
all traversal lookups in ordinary dictionaries of lists.
"""

from typing import Any, Dict, Iterable


DEFAULT_DEPTH = 5
DEFAULT_LIMIT = 200
MAX_DEPTH = 20
MAX_LIMIT = 5000


def validate_bounds(depth=DEFAULT_DEPTH, limit=DEFAULT_LIMIT):
    """Validate and return the bounded query parameters.

    ``None`` is intentionally rejected.  Ticket 07 removed the old implicit
    unbounded mode, so callers must omit a parameter to receive its default.
    """
    if not isinstance(depth, int) or isinstance(depth, bool):
        raise ValueError("depth must be an integer")
    if depth < 0:
        raise ValueError("depth must be at least 0")
    if depth > MAX_DEPTH:
        raise ValueError(f"depth must be at most {MAX_DEPTH}")

    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("limit must be an integer")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > MAX_LIMIT:
        raise ValueError(f"limit must be at most {MAX_LIMIT}")
    return depth, limit


def _records(graph: Dict[str, Any], key: str) -> Iterable[Dict[str, Any]]:
    records = graph.get(key, []) if isinstance(graph, dict) else []
    if not isinstance(records, list):
        return ()
    return (record for record in records if isinstance(record, dict))


def _id_sort_key(record: Dict[str, Any]):
    return str(record.get("id", ""))


class GraphIndex:
    """Build plain-dict projections for nodes, edges, findings, and sources."""

    def __init__(self, graph: Dict[str, Any]):
        self.nodes_by_id = {}
        self.outgoing_edges_by_node = {}
        self.incoming_edges_by_node = {}
        self.findings_by_affected_object = {}
        self.source_file_index = {}

        # Nodes are indexed independently of graph_io.nodes_by_id so this
        # projection owns its shape and cannot drift with that helper.
        for node in _records(graph, "nodes"):
            node_id = node.get("id")
            self.nodes_by_id[node_id] = node
            self.outgoing_edges_by_node.setdefault(node_id, {})
            self.incoming_edges_by_node.setdefault(node_id, {})
            self._index_source(node, "nodes", node_id)

        for edge in _records(graph, "edges"):
            edge_type = edge.get("type")
            from_id = edge.get("from")
            to_id = edge.get("to")
            self.outgoing_edges_by_node.setdefault(from_id, {}).setdefault(
                edge_type, []
            ).append(edge)
            self.incoming_edges_by_node.setdefault(to_id, {}).setdefault(
                edge_type, []
            ).append(edge)
            self._index_source(edge, "edges", edge.get("id"))

        for finding in _records(graph, "findings"):
            for field in ("affected_nodes", "affected_edges", "affected_objects"):
                affected = finding.get(field, [])
                if not isinstance(affected, list):
                    continue
                for object_id in affected:
                    self.findings_by_affected_object.setdefault(object_id, []).append(
                        finding
                    )

        self._sort_edge_lists()
        self._sort_source_lists()

        # These aliases keep the shape discoverable for callers while all
        # names continue to point at the same plain dictionaries.
        self.outgoing_edges = self.outgoing_edges_by_node
        self.incoming_edges = self.incoming_edges_by_node
        self.outgoing_by_node = self.outgoing_edges_by_node
        self.incoming_by_node = self.incoming_edges_by_node
        self.findings_by_object = self.findings_by_affected_object
        self.source_files = self.source_file_index

    def _index_source(self, record, category, record_id):
        source = record.get("source")
        if not isinstance(source, dict):
            return
        source_file = source.get("file")
        if source_file is None:
            return
        bucket = self.source_file_index.setdefault(
            source_file, {"nodes": [], "edges": []}
        )
        bucket[category].append(record_id)

    def _sort_edge_lists(self):
        for by_type in self.outgoing_edges_by_node.values():
            for edges in by_type.values():
                edges.sort(key=_id_sort_key)
        for by_type in self.incoming_edges_by_node.values():
            for edges in by_type.values():
                edges.sort(key=_id_sort_key)

    def _sort_source_lists(self):
        for bucket in self.source_file_index.values():
            bucket["nodes"].sort(key=str)
            bucket["edges"].sort(key=str)


__all__ = [
    "DEFAULT_DEPTH",
    "DEFAULT_LIMIT",
    "MAX_DEPTH",
    "MAX_LIMIT",
    "GraphIndex",
    "validate_bounds",
]
