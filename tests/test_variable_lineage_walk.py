"""Variable impact reference walk, exercised against hand-built graph dicts.

No live SAS parse needed: the walk operates purely on the graph.json shape,
so fixtures here are minimal nodes/edges lists, not parser output.
"""

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.variable_lineage_walk import walk_variable_impact


def _edge(edge_id, edge_type, src, dst, value=None, operator=None):
    return {
        "id": edge_id,
        "type": edge_type,
        "from": src,
        "to": dst,
        "value": value,
        "operator": operator,
    }


def test_straight_line_walk():
    graph = {
        "nodes": [{"id": "step:1", "type": "Step"}, {"id": "step:2", "type": "Step"}],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "step:1"),
            _edge("e2", "writes_variable", "step:1", "variable:b"),
            _edge("e3", "reads_variable", "variable:b", "step:2"),
            _edge("e4", "writes_variable", "step:2", "variable:c"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    assert result["start"] == "variable:a"
    assert result["reached_variables"] == ["variable:a", "variable:b", "variable:c"]
    assert [op["id"] for op in result["reached_operations"]] == ["step:1", "step:2"]
    assert len(result["category_facts"]) == 4


def test_branch_merge_via_edges_accumulate():
    # a -> step:1 -> b, a -> step:2 -> c, b -> step:3, c -> step:3 -> d
    graph = {
        "nodes": [
            {"id": "step:1", "type": "Step"},
            {"id": "step:2", "type": "Step"},
            {"id": "step:3", "type": "Step"},
        ],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "step:1"),
            _edge("e2", "writes_variable", "step:1", "variable:b"),
            _edge("e3", "reads_variable", "variable:a", "step:2"),
            _edge("e4", "writes_variable", "step:2", "variable:c"),
            _edge("e5", "reads_variable", "variable:b", "step:3"),
            _edge("e6", "reads_variable", "variable:c", "step:3"),
            _edge("e7", "writes_variable", "step:3", "variable:d"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    assert result["reached_variables"] == [
        "variable:a",
        "variable:b",
        "variable:c",
        "variable:d",
    ]
    step3 = next(op for op in result["reached_operations"] if op["id"] == "step:3")
    assert sorted(step3["via_edges"]) == ["e5", "e6"]


def test_cycle_terminates_without_duplicates():
    graph = {
        "nodes": [{"id": "step:1", "type": "Step"}, {"id": "step:2", "type": "Step"}],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "step:1"),
            _edge("e2", "writes_variable", "step:1", "variable:b"),
            _edge("e3", "reads_variable", "variable:b", "step:2"),
            _edge("e4", "writes_variable", "step:2", "variable:a"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    assert result["reached_variables"] == ["variable:a", "variable:b"]
    assert [op["id"] for op in result["reached_operations"]] == ["step:1", "step:2"]
    assert len(result["category_facts"]) == 4


def test_non_expanding_edges_are_ignored():
    graph = {
        "nodes": [
            {"id": "step:1", "type": "Step"},
            {"id": "dataset:ds1", "type": "Dataset"},
            {"id": "program:p1", "type": "Program"},
        ],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "step:1"),
            _edge("e2", "writes_variable", "step:1", "variable:b"),
            # Adjacent to the frontier but must never expand or appear.
            _edge("e3", "writes_dataset", "step:1", "dataset:ds1"),
            _edge("e4", "contains_step", "program:p1", "step:1"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    assert result["reached_variables"] == ["variable:a", "variable:b"]
    assert [op["id"] for op in result["reached_operations"]] == ["step:1"]
    walked_edge_ids = {fact["edge"] for fact in result["category_facts"]}
    assert walked_edge_ids == {"e1", "e2"}


def test_sql_statement_owning_program_via_sql_block():
    graph = {
        "nodes": [{"id": "sql:1", "type": "SqlStatement"}],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "sql:1"),
            _edge("e2", "writes_variable", "sql:1", "variable:b"),
            _edge("e3", "contains_sql_statement", "sqlblock:1", "sql:1"),
            _edge("e4", "contains_step", "program:p1", "sqlblock:1"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    sql_op = result["reached_operations"][0]
    assert sql_op["type"] == "SqlStatement"
    assert sql_op["program"] == "program:p1"


def test_macro_call_owning_program_via_calls_macro():
    graph = {
        "nodes": [{"id": "macrocall:1", "type": "MacroCall"}],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "macrocall:1"),
            _edge("e2", "writes_variable", "macrocall:1", "variable:b"),
            _edge("e3", "calls_macro", "program:p1", "macrocall:1"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    op = result["reached_operations"][0]
    assert op["type"] == "MacroCall"
    assert op["program"] == "program:p1"


def test_operation_without_owning_program_is_null():
    graph = {
        "nodes": [{"id": "step:1", "type": "Step"}],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "step:1"),
            _edge("e2", "writes_variable", "step:1", "variable:b"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    assert result["reached_operations"][0]["program"] is None


def test_category_facts_echo_value_and_operator_verbatim():
    graph = {
        "nodes": [{"id": "step:1", "type": "Step"}],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "step:1", value="Y", operator="="),
            _edge("e2", "writes_variable", "step:1", "variable:b"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    facts_by_edge = {fact["edge"]: fact for fact in result["category_facts"]}
    assert facts_by_edge["e1"]["value"] == "Y"
    assert facts_by_edge["e1"]["operator"] == "="
    assert facts_by_edge["e2"]["value"] is None
    assert facts_by_edge["e2"]["operator"] is None


def test_unknown_variable_terminates_the_walk():
    graph = {
        "nodes": [
            {"id": "step:1", "type": "Step"},
            {"id": "unknownvariable:flag@1", "type": "UnknownVariable"},
        ],
        "edges": [
            _edge("e1", "reads_variable", "variable:a", "step:1"),
            _edge("e2", "writes_variable", "step:1", "unknownvariable:flag@1"),
        ],
    }

    result = walk_variable_impact(graph, "variable:a")

    assert result["reached_variables"] == ["variable:a", "unknownvariable:flag@1"]
    assert [op["id"] for op in result["reached_operations"]] == ["step:1"]


def demo():
    test_straight_line_walk()
    test_branch_merge_via_edges_accumulate()
    test_cycle_terminates_without_duplicates()
    test_non_expanding_edges_are_ignored()
    test_sql_statement_owning_program_via_sql_block()
    test_macro_call_owning_program_via_calls_macro()
    test_operation_without_owning_program_is_null()
    test_category_facts_echo_value_and_operator_verbatim()
    test_unknown_variable_terminates_the_walk()
    print("ok")


if __name__ == "__main__":
    demo()
