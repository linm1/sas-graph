import conftest  # noqa: F401

from sas_graph.graph_queries import analyze_impact, search_nodes, trace_lineage


def _node(node_id, node_type, label=None, source=None):
    return {
        "id": node_id,
        "type": node_type,
        "label": label or node_id,
        "source": source,
    }


def _edge(edge_id, edge_type, source_id, target_id):
    return {
        "id": edge_id,
        "type": edge_type,
        "from": source_id,
        "to": target_id,
        "source": None,
    }


def _graph():
    return {
        "schema_version": "0.2.0",
        "nodes": [
            _node("dataset:raw.a", "Dataset", "raw.a"),
            _node("step:001", "Step", "build a", {"file": "main.sas", "line_start": 1, "line_end": 2}),
            _node("dataset:work.a", "Dataset", "work.a"),
            _node("step:002", "Step", "build b", {"file": "main.sas", "line_start": 3, "line_end": 4}),
            _node("dataset:work.b", "Dataset", "work.b"),
            _node("variable:work.b.flag", "Variable", "work.b.flag"),
        ],
        "edges": [
            _edge("edge:001", "reads_dataset", "dataset:raw.a", "step:001"),
            _edge("edge:002", "writes_dataset", "step:001", "dataset:work.a"),
            _edge("edge:003", "reads_dataset", "dataset:work.a", "step:002"),
            _edge("edge:004", "writes_dataset", "step:002", "dataset:work.b"),
            _edge("edge:005", "contains_step", "program:main", "step:002"),
        ],
    }


def test_search_matches_label_and_type_case_insensitively_with_curated_source():
    graph = _graph()

    result = search_nodes(graph, "STEP")

    assert result == {
        "query": "STEP",
        "matches": [
            {
                "id": "step:001",
                "type": "Step",
                "label": "build a",
                "source": {"file": "main.sas", "line_start": 1, "line_end": 2},
            },
            {
                "id": "step:002",
                "type": "Step",
                "label": "build b",
                "source": {"file": "main.sas", "line_start": 3, "line_end": 4},
            },
        ],
    }


def test_search_returns_empty_success_for_no_match():
    assert search_nodes(_graph(), "missing") == {"query": "missing", "matches": []}


def test_trace_lineage_from_dataset_both_directions_is_unbounded_and_cycle_safe():
    result = trace_lineage(_graph(), "dataset:work.a", "both")

    assert result["start"] == "dataset:work.a"
    assert [node["id"] for node in result["upstream_nodes"]] == [
        "step:001", "dataset:raw.a"
    ]
    assert [node["id"] for node in result["downstream_nodes"]] == [
        "step:002", "dataset:work.b"
    ]
    assert [edge["id"] for edge in result["edges"]] == [
        "edge:002", "edge:001", "edge:003", "edge:004"
    ]


def test_trace_lineage_from_step_and_direction_leaves_unused_list_empty():
    result = trace_lineage(_graph(), "step:002", "upstream")

    assert [node["id"] for node in result["upstream_nodes"]] == [
        "dataset:work.a", "step:001", "dataset:raw.a"
    ]
    assert result["downstream_nodes"] == []
    assert [edge["id"] for edge in result["edges"]] == [
        "edge:003", "edge:002", "edge:001"
    ]


def test_trace_lineage_rejects_missing_or_ineligible_start_node():
    import pytest

    graph = _graph()
    graph["nodes"].append(_node("program:main", "Program", "main"))

    with pytest.raises(ValueError, match="node not found"):
        trace_lineage(graph, "dataset:missing", "both")
    with pytest.raises(ValueError, match="must be Dataset, Step, or SqlStatement"):
        trace_lineage(graph, "variable:work.b.flag", "both")
    with pytest.raises(ValueError, match="must be Dataset, Step, or SqlStatement"):
        trace_lineage(graph, "program:main", "both")


def test_trace_lineage_rejects_unknown_direction():
    import pytest

    with pytest.raises(ValueError, match="direction"):
        trace_lineage(_graph(), "dataset:work.a", "sideways")


def test_analyze_impact_delegates_to_the_existing_variable_walk():
    graph = _graph()
    graph["edges"] += [
        _edge("edge:006", "reads_variable", "variable:work.b.flag", "step:002"),
        _edge("edge:007", "writes_variable", "step:002", "variable:work.b.flag"),
    ]

    result = analyze_impact(graph, "variable:work.b.flag")

    assert result["start"] == "variable:work.b.flag"
    assert result["reached_variables"] == ["variable:work.b.flag"]
    assert [operation["id"] for operation in result["reached_operations"]] == ["step:002"]


def test_analyze_impact_rejects_missing_or_non_variable_ids():
    import pytest

    with pytest.raises(ValueError, match="variable not found"):
        analyze_impact(_graph(), "variable:missing")
    with pytest.raises(ValueError, match="must be a Variable"):
        analyze_impact(_graph(), "step:001")


def test_analyze_impact_accepts_unknown_variable_start_ids():
    graph = _graph()
    graph["nodes"].append(_node("unknownvariable:flag@12", "UnknownVariable", "flag"))

    result = analyze_impact(graph, "unknownvariable:flag@12")

    assert result == {
        "start": "unknownvariable:flag@12",
        "reached_variables": ["unknownvariable:flag@12"],
        "reached_operations": [],
        "category_facts": [],
    }


def test_trace_lineage_handles_branch_merge_and_cycle_without_duplicates():
    nodes = [
        _node("dataset:start", "Dataset"),
        _node("step:left", "Step"),
        _node("dataset:left", "Dataset"),
        _node("step:right", "Step"),
        _node("dataset:right", "Dataset"),
        _node("step:merge", "Step"),
        _node("dataset:merged", "Dataset"),
        _node("step:cycle", "Step"),
    ]
    edges = [
        _edge("edge:branch-left-read", "reads_dataset", "dataset:start", "step:left"),
        _edge("edge:branch-left-write", "writes_dataset", "step:left", "dataset:left"),
        _edge("edge:branch-right-read", "reads_dataset", "dataset:start", "step:right"),
        _edge("edge:branch-right-write", "writes_dataset", "step:right", "dataset:right"),
        _edge("edge:merge-left", "reads_dataset", "dataset:left", "step:merge"),
        _edge("edge:merge-right", "reads_dataset", "dataset:right", "step:merge"),
        _edge("edge:merge-write", "writes_dataset", "step:merge", "dataset:merged"),
        _edge("edge:cycle-read", "reads_dataset", "dataset:merged", "step:cycle"),
        _edge("edge:cycle-write", "writes_dataset", "step:cycle", "dataset:start"),
    ]

    result = trace_lineage({"nodes": nodes, "edges": edges}, "dataset:start", "downstream")

    reached_ids = [node["id"] for node in result["downstream_nodes"]]
    assert set(reached_ids) == {
        "step:left",
        "dataset:left",
        "step:right",
        "dataset:right",
        "step:merge",
        "dataset:merged",
        "step:cycle",
    }
    assert len(reached_ids) == len(set(reached_ids))
    assert {edge["id"] for edge in result["edges"]} == {edge["id"] for edge in edges}
