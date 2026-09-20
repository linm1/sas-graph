"""Structural graph-contract validator tests for Phase 0."""

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.graph_validator import validate_graph


def graph(nodes=None, edges=None, schema_version="0.2.0"):
    return {
        "schema_version": schema_version,
        "nodes": [] if nodes is None else nodes,
        "edges": [] if edges is None else edges,
    }


def node(node_id="node:001", node_type="Dataset", label="work.a"):
    return {"id": node_id, "type": node_type, "label": label}


def edge(edge_id="edge:001", edge_type="reads_dataset", from_id="node:001", to_id="node:001"):
    return {"id": edge_id, "type": edge_type, "from": from_id, "to": to_id}


def test_valid_graph_has_no_problems():
    assert validate_graph(
        graph(
            nodes=[node(), node("step:001", node_type="Step", label="DATA step")],
            edges=[edge(from_id="node:001", to_id="step:001")],
        )
    ) == []


def test_missing_nodes_collection_is_reported():
    problems = validate_graph({"schema_version": "0.2.0", "edges": []})

    assert len(problems) == 1
    assert "nodes" in problems[0]
    assert "missing" in problems[0]


def test_non_list_nodes_collection_is_reported():
    problems = validate_graph(
        {"schema_version": "0.2.0", "nodes": "oops", "edges": []}
    )

    assert len(problems) == 1
    assert "nodes" in problems[0]
    assert "str" in problems[0]


def test_non_list_collections_are_reported():
    problems = validate_graph(
        {"schema_version": "0.2.0", "nodes": {}, "edges": {}}
    )

    assert len(problems) == 2
    assert any("nodes" in problem and "dict" in problem for problem in problems)
    assert any("edges" in problem and "dict" in problem for problem in problems)


def test_bad_nodes_collection_does_not_suppress_dangling_edge_problem():
    problems = validate_graph(
        {
            "schema_version": "0.2.0",
            "nodes": "oops",
            "edges": [edge(from_id="missing:node", to_id="missing:node")],
        }
    )

    assert any("nodes" in problem and "str" in problem for problem in problems)
    assert any(
        "dangling" in problem and "missing:node" in problem for problem in problems
    )


def test_dangling_edge_reference_names_edge_and_missing_node_ids():
    problems = validate_graph(
        graph(nodes=[node()], edges=[edge(from_id="missing:node")])
    )

    assert len(problems) == 1
    assert "edge:001" in problems[0]
    assert "missing:node" in problems[0]


def test_duplicate_node_id_is_reported_with_the_offending_id():
    problems = validate_graph(graph(nodes=[node("node:001"), node("node:001")]))

    assert len(problems) == 1
    assert "duplicate" in problems[0]
    assert "node:001" in problems[0]


def test_missing_core_keys_are_all_reported_in_one_pass():
    problems = validate_graph(
        graph(
            nodes=[{"id": "node:broken"}],
            edges=[{"id": "edge:broken", "from": "node:broken"}],
        )
    )

    assert any("node:broken" in problem and "type" in problem for problem in problems)
    assert any("node:broken" in problem and "label" in problem for problem in problems)
    assert any("edge:broken" in problem and "type" in problem for problem in problems)
    assert any("edge:broken" in problem and "to" in problem for problem in problems)


def test_missing_schema_version_is_reported():
    invalid = graph(nodes=[node()])
    del invalid["schema_version"]

    problems = validate_graph(invalid)

    assert problems == ["graph is missing required key 'schema_version'"]


def test_unsupported_schema_version_is_reported():
    problems = validate_graph(graph(schema_version="9.9.9"))

    assert len(problems) == 1
    assert "schema_version" in problems[0]
    assert "9.9.9" in problems[0]
    assert "0.2.0" in problems[0]


def test_unknown_node_type_is_reported_with_the_offending_id_and_type():
    problems = validate_graph(
        graph(
            nodes=[node("mystery:001", node_type="FutureNode")],
        )
    )

    assert len(problems) == 1
    assert "mystery:001" in problems[0]
    assert "FutureNode" in problems[0]


def test_unknown_edge_type_is_reported_with_the_offending_id_and_type():
    problems = validate_graph(
        graph(
            nodes=[node("left"), node("right", node_type="Step")],
            edges=[edge(edge_id="mystery:001", edge_type="future_edge", from_id="left", to_id="right")],
        )
    )

    assert len(problems) == 1
    assert "mystery:001" in problems[0]
    assert "future_edge" in problems[0]


def test_illegal_endpoint_pair_is_reported_with_the_offending_id_and_type():
    problems = validate_graph(
        graph(
            nodes=[node("left"), node("right")],
            edges=[edge(edge_id="illegal:001", from_id="left", to_id="right")],
        )
    )

    assert len(problems) == 1
    assert "illegal:001" in problems[0]
    assert "reads_dataset" in problems[0]


def test_dangling_endpoint_skips_endpoint_pair_check():
    problems = validate_graph(
        graph(
            nodes=[node("present")],
            edges=[edge(edge_id="dangling:001", from_id="missing", to_id="present")],
        )
    )

    assert len(problems) == 1
    assert "dangling" in problems[0]
    assert "missing" in problems[0]


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print("ok  {0}".format(name))
    print("graph_validator: all checks passed")


if __name__ == "__main__":
    demo()
