"""Variable impact reference walk (wayfinder variable-lineage map).

Pure query over an already-reloaded graph.json dict, same posture as
renderer_mermaid.py/renderer_findings.py: no ctx.add_node/add_edge, this is
not a rule module. See
wayfinder/tickets/variable-impact-reference-walk-algorithm.md for the
contract implemented here.
"""

from .graph_io import nodes_by_id


def _index_by_from(edges, edge_type):
    index = {}
    for edge in edges:
        if edge.get("type") == edge_type:
            index.setdefault(edge["from"], []).append(edge)
    return index


def _reverse_hop(target_id, edge_types, edges):
    """Single reverse hop: edge.to == target_id, edge.type in edge_types."""
    for edge in edges:
        if edge.get("type") in edge_types and edge.get("to") == target_id:
            return edge["from"]
    return None


def _owning_program(operation_id, node_type, edges):
    """Terminal, non-expanding lookup. Never walked as part of the traversal."""
    if node_type == "SqlStatement":
        sql_block_id = _reverse_hop(operation_id, {"contains_sql_statement"}, edges)
        if sql_block_id is None:
            return None
        return _reverse_hop(sql_block_id, {"contains_step"}, edges)
    return _reverse_hop(operation_id, {"contains_step", "calls_macro"}, edges)


def _fact(edge, operation_id):
    return {
        "edge": edge["id"],
        "on": operation_id,
        "value": edge.get("value"),
        "operator": edge.get("operator"),
    }


def walk_variable_impact(graph, start_variable_id):
    """Alternate reads_variable then writes_variable forward from a Variable.

    Only reads_variable/writes_variable edges expand the walk; Dataset,
    Program, containment, macro-definition, and other value-fact edges
    never do, even when adjacent to a node already on the frontier.
    """
    edges = graph.get("edges", [])
    nodes = nodes_by_id(graph)
    reads_from = _index_by_from(edges, "reads_variable")
    writes_from = _index_by_from(edges, "writes_variable")

    visited_variables = {start_variable_id}
    reached_variables = [start_variable_id]
    frontier = [start_variable_id]

    operations_by_id = {}
    reached_operations = []
    category_facts = []

    while frontier:
        variable_id = frontier.pop(0)
        for read_edge in reads_from.get(variable_id, []):
            operation_id = read_edge["to"]
            category_facts.append(_fact(read_edge, operation_id))

            entry = operations_by_id.get(operation_id)
            if entry is None:
                node_type = nodes.get(operation_id, {}).get("type")
                entry = {
                    "id": operation_id,
                    "type": node_type,
                    "program": _owning_program(operation_id, node_type, edges),
                    "via_edges": [],
                }
                operations_by_id[operation_id] = entry
                reached_operations.append(entry)

                for write_edge in writes_from.get(operation_id, []):
                    category_facts.append(_fact(write_edge, operation_id))
                    target_variable = write_edge["to"]
                    if target_variable not in visited_variables:
                        visited_variables.add(target_variable)
                        reached_variables.append(target_variable)
                        frontier.append(target_variable)

            entry["via_edges"].append(read_edge["id"])

    return {
        "start": start_variable_id,
        "reached_variables": reached_variables,
        "reached_operations": reached_operations,
        "category_facts": category_facts,
    }
