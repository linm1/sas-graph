"""Variable impact reference walk (wayfinder variable-lineage map).

Pure query over an already-reloaded graph.json dict, same posture as
renderer_mermaid.py/renderer_findings.py: no ctx.add_node/add_edge, this is
not a rule module.  The optional bounds are used by ``analyze_impact``;
leaving them out preserves the helper's original direct-call shape for code
that uses the reference walk independently.
"""

from collections import deque

from .graph_index import DEFAULT_DEPTH, DEFAULT_LIMIT, GraphIndex, validate_bounds


def _incoming_edges(index, node_id, edge_types):
    by_type = index.incoming_edges_by_node.get(node_id, {})
    result = []
    for edge_type in sorted(edge_types):
        result.extend(by_type.get(edge_type, []))
    return sorted(result, key=lambda edge: str(edge.get("id", "")))


def _owning_program(operation_id, node_type, index):
    """Terminal, non-expanding lookup. Never walked as part of the traversal."""
    if node_type == "SqlStatement":
        sql_block_edges = _incoming_edges(
            index, operation_id, {"contains_sql_statement"}
        )
        if not sql_block_edges:
            return None
        sql_block_id = sql_block_edges[0].get("from")
        program_edges = _incoming_edges(index, sql_block_id, {"contains_step"})
        return program_edges[0].get("from") if program_edges else None

    program_edges = _incoming_edges(
        index, operation_id, {"contains_step", "calls_macro"}
    )
    return program_edges[0].get("from") if program_edges else None


def _fact(edge, operation_id, cycle=False):
    fact = {
        "edge": edge["id"],
        "on": operation_id,
        "value": edge.get("value"),
        "operator": edge.get("operator"),
    }
    if cycle:
        fact["cycle"] = True
    return fact


def _metadata(truncated, visited_count, continuation, limit_hit, depth_hit):
    frontier = []
    for node_id in continuation:
        if node_id not in frontier:
            frontier.append(node_id)
    return {
        "truncated": truncated,
        "visited_count": visited_count,
        "limit_hit": limit_hit,
        "depth_hit": depth_hit,
        "continuation": frontier,
    }


def walk_variable_impact(
    graph,
    start_variable_id,
    index=None,
    depth=None,
    limit=None,
    include_metadata=False,
):
    """Alternate reads_variable then writes_variable forward from a Variable.

    Only reads_variable/writes_variable edges expand the walk; Dataset,
    Program, containment, macro-definition, and other value-fact edges never
    do, even when adjacent to a node already on the frontier.

    ``depth`` and ``limit`` are optional for compatibility with the original
    helper.  ``analyze_impact`` supplies both, which turns on the ticket-07
    bounds and metadata for the public query.
    """
    if index is None:
        index = GraphIndex(graph)

    bounded = include_metadata or depth is not None or limit is not None
    if bounded:
        depth = DEFAULT_DEPTH if depth is None else depth
        limit = DEFAULT_LIMIT if limit is None else limit
        depth, limit = validate_bounds(depth, limit)

    nodes = index.nodes_by_id
    reads_by_from = index.outgoing_edges_by_node
    writes_by_from = index.outgoing_edges_by_node

    visited_variables = {start_variable_id}
    reached_variables = [start_variable_id]
    frontier = deque([(start_variable_id, 0)])

    operations_by_id = {}
    reached_operations = []
    category_facts = []

    visited_nodes = {start_variable_id}
    continuation = []
    limit_hit = False
    depth_hit = False

    def result_count():
        # The start node is not one of the returned result records, so the
        # caller's limit describes returned variables plus operations.
        return len(reached_variables) - 1 + len(reached_operations)

    while frontier:
        variable_id, variable_depth = frontier.popleft()
        read_edges = reads_by_from.get(variable_id, {}).get("reads_variable", [])
        if bounded and variable_depth >= depth:
            if read_edges:
                depth_hit = True
                continuation.append(variable_id)
            continue

        for read_edge in read_edges:
            operation_id = read_edge["to"]
            entry = operations_by_id.get(operation_id)
            if entry is not None:
                category_facts.append(_fact(read_edge, operation_id))
                entry["via_edges"].append(read_edge["id"])
                continue

            if bounded and result_count() >= limit:
                limit_hit = True
                continuation.append(operation_id)
                break

            category_facts.append(_fact(read_edge, operation_id))
            node_type = nodes.get(operation_id, {}).get("type")
            entry = {
                "id": operation_id,
                "type": node_type,
                "program": _owning_program(operation_id, node_type, index),
                "via_edges": [],
            }
            operations_by_id[operation_id] = entry
            reached_operations.append(entry)
            visited_nodes.add(operation_id)
            entry["via_edges"].append(read_edge["id"])

            operation_depth = variable_depth + 1
            write_edges = writes_by_from.get(operation_id, {}).get(
                "writes_variable", []
            )
            if bounded and operation_depth >= depth:
                has_unexpanded_write = False
                for write_edge in write_edges:
                    target_variable = write_edge["to"]
                    if target_variable in visited_variables:
                        category_facts.append(
                            _fact(write_edge, operation_id, cycle=True)
                        )
                    else:
                        has_unexpanded_write = True
                if has_unexpanded_write:
                    depth_hit = True
                    continuation.append(operation_id)
                continue

            for write_edge in write_edges:
                target_variable = write_edge["to"]
                is_cycle = target_variable in visited_variables
                category_facts.append(_fact(write_edge, operation_id, cycle=is_cycle))
                if is_cycle:
                    continue
                if bounded and result_count() >= limit:
                    limit_hit = True
                    continuation.append(target_variable)
                    break
                visited_variables.add(target_variable)
                visited_nodes.add(target_variable)
                reached_variables.append(target_variable)
                frontier.append((target_variable, operation_depth + 1))

            if limit_hit:
                break

        if limit_hit:
            break

    continuation.extend(variable_id for variable_id, _ in frontier)
    result = {
        "start": start_variable_id,
        "reached_variables": reached_variables,
        "reached_operations": reached_operations,
        "category_facts": category_facts,
    }
    if bounded:
        result.update(
            _metadata(
                limit_hit or depth_hit,
                len(visited_nodes),
                continuation,
                limit_hit,
                depth_hit,
            )
        )
    return result
