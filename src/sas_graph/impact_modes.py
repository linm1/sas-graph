"""Bounded, evidence-aware downstream impact traversal.

The graph remains a plain JSON dictionary.  This module projects a
``GraphIndex`` into one traversal for the requested named edge filters and
returns each impacted node with representative paths and edge evidence.  It
does not mutate the graph or infer facts that are absent from an edge.
"""

from collections import deque
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .graph_index import DEFAULT_DEPTH, DEFAULT_LIMIT, GraphIndex, validate_bounds


IMPACT_MODES = ("structural", "value_flow", "selection")
IMPACT_EDGE_TYPES = {
    "structural": frozenset(),
    "value_flow": frozenset({"reads_variable", "writes_variable", "derives"}),
    "selection": frozenset(
        {
            "conditioned_by",
            "filters_dataset",
            "joins_on",
            "groups_by",
            "sorts_by",
        }
    ),
}

_SEMANTIC_NODE_TYPES = frozenset(
    {"Dataset", "UnknownDataset", "Variable", "UnknownVariable"}
)
_UNKNOWN_BOUNDARY = "unknown_node"
_BOUNDARY_ORDER = (
    _UNKNOWN_BOUNDARY,
    "external_file",
    "depth",
    "limit",
    "sink",
    "cycle",
)

_KNOWN = 0
_POSSIBLE = 1
_UNRESOLVED = 2


def _source(source: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(source, dict):
        return None
    return {
        "file": source.get("file"),
        "line_start": source.get("line_start"),
        "line_end": source.get("line_end"),
    }


def _node_result(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "label": node.get("label"),
        "source": _source(node.get("source")),
    }


def _edge_result(edge: Dict[str, Any], cycle: bool = False) -> Dict[str, Any]:
    result = {
        "id": edge.get("id"),
        "type": edge.get("type"),
        "from": edge.get("from"),
        "to": edge.get("to"),
        "source": _source(edge.get("source")),
        "evidence": deepcopy(edge.get("evidence")),
    }
    if cycle:
        result["cycle"] = True
    return result


def _normalize_modes(
    mode: Optional[Union[str, Iterable[str]]] = None,
    modes: Optional[Union[str, Iterable[str]]] = None,
) -> Tuple[str, ...]:
    if mode is not None and modes is not None:
        raise ValueError("provide mode or modes, not both")
    requested = modes if modes is not None else mode
    if requested is None:
        requested = IMPACT_MODES
    elif isinstance(requested, str):
        requested = (requested,)
    else:
        try:
            requested = tuple(requested)
        except TypeError as exc:
            raise ValueError(
                "mode must be structural, value_flow, or selection"
            ) from exc

    unknown = [name for name in requested if name not in IMPACT_MODES]
    if unknown:
        raise ValueError("mode must be structural, value_flow, or selection")
    normalized = tuple(name for name in IMPACT_MODES if name in requested)
    if not normalized:
        raise ValueError("at least one impact mode is required")
    return normalized


def _active_edges(index: GraphIndex, node_id: str, mode: str) -> List[Dict[str, Any]]:
    by_type = index.outgoing_edges_by_node.get(node_id, {})
    if mode == "structural":
        edge_types = by_type.keys()
    else:
        edge_types = IMPACT_EDGE_TYPES[mode]
    edges = []
    for edge_type in edge_types:
        edges.extend(by_type.get(edge_type, []))
    return sorted(edges, key=lambda edge: str(edge.get("id", "")))


def _is_unknown_node(node: Dict[str, Any]) -> bool:
    return str(node.get("type", "")).lower().startswith("unknown")


def _depth_increment(node: Dict[str, Any]) -> int:
    """Count semantic object hops while structural nodes pass through."""
    return 1 if node.get("type") in _SEMANTIC_NODE_TYPES else 0


def _edge_certainty(edge: Dict[str, Any]) -> int:
    """Return the weakest evidence rank contributed by one edge.

    Rank 0 is known, rank 1 is possible, and rank 2 is unresolved.  A
    RESOLVED edge is known only with EXACT resolution; PARTIAL and all other
    non-exact resolutions remain possible.
    """
    evidence = edge.get("evidence")
    if not isinstance(evidence, dict):
        return _UNRESOLVED
    kind = evidence.get("kind")
    if kind == "UNKNOWN":
        return _UNRESOLVED
    if kind in {"CONTRACT_DERIVED", "HEURISTIC"}:
        return _POSSIBLE
    if kind == "RESOLVED":
        return _KNOWN if evidence.get("resolution") == "EXACT" else _POSSIBLE
    if kind == "OBSERVED":
        return _KNOWN
    return _UNRESOLVED


def _classification(rank: int, node: Dict[str, Any]) -> str:
    if _is_unknown_node(node) or rank >= _UNRESOLVED:
        return "unresolved_boundary"
    if rank >= _POSSIBLE:
        return "possibly_impacted"
    return "known_impacted"


def _path_key(path_edges: Iterable[Dict[str, Any]]) -> Tuple[str, ...]:
    return tuple(str(edge.get("id", "")) for edge in path_edges)


def _path_result(
    mode: str,
    target_id: str,
    path_nodes: Iterable[str],
    path_edges: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "mode": mode,
        "target": target_id,
        "nodes": list(path_nodes),
        "edges": list(path_edges),
        "evidence": [deepcopy(edge.get("evidence")) for edge in path_edges],
    }


def _sort_reasons(reasons: Iterable[str]) -> List[str]:
    return sorted(
        reasons,
        key=lambda reason: (
            (
                _BOUNDARY_ORDER.index(reason)
                if reason in _BOUNDARY_ORDER
                else len(_BOUNDARY_ORDER)
            ),
            reason,
        ),
    )


def _continuation_ids(values: Iterable[Any]) -> List[Any]:
    return sorted(set(values), key=str)


def _empty_state() -> Dict[str, Any]:
    return {
        "rank": _KNOWN,
        "path_key": (),
        "path_nodes": (),
        "path_edges": (),
    }


def impact(
    graph: Dict[str, Any],
    seed_id: str,
    mode: Optional[Union[str, Iterable[str]]] = None,
    depth: int = DEFAULT_DEPTH,
    limit: int = DEFAULT_LIMIT,
    index: Optional[GraphIndex] = None,
    modes: Optional[Union[str, Iterable[str]]] = None,
) -> Dict[str, Any]:
    """Return bounded downstream impact for one or more named modes.

    ``structural``, ``value_flow`` and ``selection`` are overlapping filters
    over the same outgoing traversal.  A returned node therefore carries all
    modes that reached it.  Each node has one deterministic representative path
    per matching mode; paths carry every edge's evidence object.

    The result limit counts unique returned nodes (the seed is not counted),
    and is checked before adding a candidate node or edge.  Depth counts
    semantic Dataset/Variable hops; operation and containment nodes pass
    through without consuming it.  ``output`` is intentionally not a mode.
    """
    depth, limit = validate_bounds(depth, limit)
    normalized_modes = _normalize_modes(mode=mode, modes=modes)
    if index is None:
        index = GraphIndex(graph)

    seed = index.nodes_by_id.get(seed_id)
    if seed is None:
        raise ValueError(f"node not found: {seed_id}")

    # Each mode has at most one retained path per node.  The retained path is
    # the weakest-evidence path discovered so far; this is enough to implement
    # the precedence rule without enumerating exponentially many simple paths.
    best = {}
    queue = deque()
    for current_mode in normalized_modes:
        state = _empty_state()
        state["path_nodes"] = (seed_id,)
        best[(current_mode, seed_id)] = state
        queue.append((current_mode, seed_id, 0, _KNOWN, (seed_id,), ()))

    reached_ids = set()
    visited_ids = {seed_id}
    boundary_by_node = {}
    cycle_paths = {}
    continuation = []
    limit_hit = False
    depth_hit = False

    def mark_boundary(node_id, current_mode, reason):
        by_mode = boundary_by_node.setdefault(node_id, {})
        by_mode.setdefault(current_mode, set()).add(reason)

    def remember_candidate(node_id):
        nonlocal limit_hit
        if node_id in reached_ids:
            return True
        if len(reached_ids) >= limit:
            limit_hit = True
            continuation.append(node_id)
            return False
        reached_ids.add(node_id)
        visited_ids.add(node_id)
        return True

    while queue and not limit_hit:
        (
            current_mode,
            current_id,
            current_depth,
            current_rank,
            path_nodes,
            path_edges,
        ) = queue.popleft()
        saved = best.get((current_mode, current_id))
        if saved is None:
            continue
        if saved["rank"] != current_rank or saved["path_key"] != _path_key(path_edges):
            continue

        current = index.nodes_by_id.get(current_id)
        if current is None:
            raise ValueError(f"impact path references missing node {current_id!r}")

        if _is_unknown_node(current):
            mark_boundary(current_id, current_mode, _UNKNOWN_BOUNDARY)
            continue
        if current.get("type") == "ExternalFile":
            mark_boundary(current_id, current_mode, "external_file")
            continue

        edges = _active_edges(index, current_id, current_mode)
        if not edges:
            mark_boundary(current_id, current_mode, "sink")
            continue

        expanded = False
        blocked_for_depth = False
        blocked_for_cycle = False
        for edge in edges:
            neighbor_id = edge.get("to")
            neighbor = index.nodes_by_id.get(neighbor_id)
            if neighbor is None:
                raise ValueError(
                    f"impact edge {edge.get('id')!r} points to missing node {neighbor_id!r}"
                )

            if neighbor_id in path_nodes:
                blocked_for_cycle = True
                cycle_edge = _edge_result(edge, cycle=True)
                cycle_path_edges = path_edges + (cycle_edge,)
                cycle_rank = max(current_rank, _edge_certainty(edge))
                cycle_path_nodes = path_nodes + (neighbor_id,)
                previous = best.get((current_mode, neighbor_id))
                cycle_key = _path_key(cycle_path_edges)
                cycle_paths.setdefault((current_mode, neighbor_id), {})[cycle_key] = (
                    cycle_path_nodes,
                    cycle_path_edges,
                )
                if previous is None or (
                    cycle_rank > previous["rank"]
                    or (
                        cycle_rank == previous["rank"]
                        and cycle_key < previous["path_key"]
                    )
                ):
                    best[(current_mode, neighbor_id)] = {
                        "rank": cycle_rank,
                        "path_key": cycle_key,
                        "path_nodes": cycle_path_nodes,
                        "path_edges": cycle_path_edges,
                    }
                continue

            next_depth = current_depth + _depth_increment(neighbor)
            if next_depth > depth:
                blocked_for_depth = True
                depth_hit = True
                continuation.append(current_id)
                continue

            if not remember_candidate(neighbor_id):
                mark_boundary(current_id, current_mode, "limit")
                break

            edge_result = _edge_result(edge)
            next_path_edges = path_edges + (edge_result,)
            next_path_nodes = path_nodes + (neighbor_id,)
            next_rank = max(current_rank, _edge_certainty(edge))
            next_key = _path_key(next_path_edges)
            previous = best.get((current_mode, neighbor_id))
            should_update = previous is None or (
                next_rank > previous["rank"]
                or (next_rank == previous["rank"] and next_key < previous["path_key"])
            )
            if should_update:
                best[(current_mode, neighbor_id)] = {
                    "rank": next_rank,
                    "path_key": next_key,
                    "path_nodes": next_path_nodes,
                    "path_edges": next_path_edges,
                }
                queue.append(
                    (
                        current_mode,
                        neighbor_id,
                        next_depth,
                        next_rank,
                        next_path_nodes,
                        next_path_edges,
                    )
                )
            expanded = True

        if blocked_for_depth:
            mark_boundary(current_id, current_mode, "depth")
        if blocked_for_cycle and not expanded:
            mark_boundary(current_id, current_mode, "cycle")

    impacted = []
    paths = []
    for node_id in sorted(reached_ids, key=str):
        node = index.nodes_by_id[node_id]
        matching_modes = [
            current_mode
            for current_mode in normalized_modes
            if (current_mode, node_id) in best
        ]
        node_paths = []
        ranks = []
        for current_mode in matching_modes:
            state = best[(current_mode, node_id)]
            ranks.append(state["rank"])
            path = _path_result(
                current_mode,
                node_id,
                state["path_nodes"],
                state["path_edges"],
            )
            node_paths.append(path)
            paths.append(path)
            for cycle_key in sorted(cycle_paths.get((current_mode, node_id), {})):
                if cycle_key == state["path_key"]:
                    continue
                cycle_nodes, cycle_edges = cycle_paths[(current_mode, node_id)][
                    cycle_key
                ]
                cycle_path = _path_result(
                    current_mode,
                    node_id,
                    cycle_nodes,
                    cycle_edges,
                )
                node_paths.append(cycle_path)
                paths.append(cycle_path)

        reasons_by_mode = boundary_by_node.get(node_id, {})
        all_reasons = set()
        boundary_modes = {}
        for current_mode, reasons in reasons_by_mode.items():
            ordered = _sort_reasons(reasons)
            all_reasons.update(ordered)
            boundary_modes[current_mode] = ordered
        ordered_reasons = _sort_reasons(all_reasons)
        entry = _node_result(node)
        entry.update(
            {
                "modes": matching_modes,
                "classification": _classification(max(ranks), node),
                "boundary": bool(ordered_reasons),
                "boundary_reason": ordered_reasons[0] if ordered_reasons else None,
                "boundary_reasons": ordered_reasons,
                "boundary_modes": boundary_modes,
                "paths": node_paths,
            }
        )
        impacted.append(entry)

    paths.sort(
        key=lambda path: (
            str(path["target"]),
            path["mode"],
            _path_key(path["edges"]),
        )
    )
    result = {
        "start": seed_id,
        "modes": list(normalized_modes),
        "impacted": impacted,
        "paths": paths,
        "truncated": limit_hit or depth_hit,
        "visited_count": len(visited_ids),
        "limit_hit": limit_hit,
        "depth_hit": depth_hit,
        "continuation": _continuation_ids(continuation),
    }
    if len(normalized_modes) == 1:
        result["mode"] = normalized_modes[0]
    return result


__all__ = ["IMPACT_EDGE_TYPES", "IMPACT_MODES", "impact"]
