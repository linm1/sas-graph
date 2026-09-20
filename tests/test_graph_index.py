"""GraphIndex and bounded-query acceptance tests.

The small hand-built graphs exercise index shape and traversal boundaries;
the real-pipeline test keeps the query contract tied to graph.json output.
"""

import copy
import math
import os
import random
import tempfile
import time
from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)
import pytest

from sas_graph.config import load_config
from sas_graph.graph_index import GraphIndex
from sas_graph.graph_io import load_graph, save_graph
from sas_graph.graph_queries import analyze_impact, search_nodes, trace_lineage
from sas_graph.run_pipeline import run


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "variable_lineage"


def _node(node_id, node_type, label=None, source=None):
    return {
        "id": node_id,
        "type": node_type,
        "label": label or node_id,
        "source": source,
    }


def _edge(edge_id, edge_type, source_id, target_id, source=None):
    return {
        "id": edge_id,
        "type": edge_type,
        "from": source_id,
        "to": target_id,
        "source": source,
    }


def _indexed_graph():
    return {
        "schema_version": "0.2.0",
        "nodes": [
            _node("dataset:b", "Dataset", source={"file": "b.sas"}),
            _node("dataset:a", "Dataset", source={"file": "main.sas"}),
            _node("step:1", "Step", source={"file": "main.sas"}),
        ],
        "edges": [
            _edge(
                "edge:2",
                "writes_dataset",
                "step:1",
                "dataset:b",
                source={"file": "main.sas"},
            ),
            _edge(
                "edge:1",
                "reads_dataset",
                "dataset:a",
                "step:1",
                source={"file": "main.sas"},
            ),
        ],
        "findings": [
            {
                "id": "finding:1",
                "affected_nodes": ["dataset:a"],
                "affected_edges": ["edge:2"],
            }
        ],
    }


def _linear_graph(length):
    nodes = [_node("dataset:000", "Dataset")]
    edges = []
    previous_id = "dataset:000"
    previous_type = "Dataset"
    for index in range(1, length + 1):
        node_type = "Step" if index % 2 else "Dataset"
        node_id = f"{node_type.lower()}:{index:03d}"
        edge_type = "reads_dataset" if previous_type == "Dataset" else "writes_dataset"
        nodes.append(_node(node_id, node_type))
        edges.append(_edge(f"edge:{index:03d}", edge_type, previous_id, node_id))
        previous_id = node_id
        previous_type = node_type
    return {"nodes": nodes, "edges": edges, "findings": []}


def _cycle_graph():
    return {
        "nodes": [
            _node("dataset:start", "Dataset"),
            _node("step:loop", "Step"),
            _node("dataset:end", "Dataset"),
        ],
        "edges": [
            _edge("edge:1", "reads_dataset", "dataset:start", "step:loop"),
            _edge("edge:2", "writes_dataset", "step:loop", "dataset:end"),
            _edge("edge:3", "reads_dataset", "dataset:end", "step:loop"),
        ],
    }


def _real_pipeline_graph():
    result = load_config(FIXTURE_DIR / "project.yaml")
    assert result.ok, result.findings
    graph = run(result, run_id="graph-index-acceptance")
    with tempfile.TemporaryDirectory() as temporary:
        return load_graph(save_graph(graph, Path(temporary) / "graph.json"))


def synthetic_graph(node_count=10000, seed=1337):
    """Build a deterministic graph for the optional local latency benchmark."""
    if node_count < 4:
        raise ValueError("node_count must be at least 4")

    rng = random.Random(seed)
    chain_length = (node_count - 1) // 3
    nodes = []
    for index in range(chain_length):
        nodes.extend(
            (
                _node(
                    f"dataset:{index:05d}",
                    "Dataset",
                    label=f"dataset-{rng.randrange(1 << 30):08x}",
                ),
                _node(
                    f"step:{index:05d}",
                    "Step",
                    label=f"step-{rng.randrange(1 << 30):08x}",
                ),
                _node(
                    f"variable:{index:05d}",
                    "Variable",
                    label=f"variable-{rng.randrange(1 << 30):08x}",
                ),
            )
        )
    while len(nodes) < node_count:
        index = len(nodes)
        nodes.append(_node(f"program:{index:05d}", "Program"))

    edges = []
    for index in range(chain_length - 1):
        base = index * 4
        edges.extend(
            (
                _edge(
                    f"edge:{base:05d}",
                    "reads_dataset",
                    f"dataset:{index:05d}",
                    f"step:{index:05d}",
                ),
                _edge(
                    f"edge:{base + 1:05d}",
                    "writes_dataset",
                    f"step:{index:05d}",
                    f"dataset:{index + 1:05d}",
                ),
                _edge(
                    f"edge:{base + 2:05d}",
                    "reads_variable",
                    f"variable:{index:05d}",
                    f"step:{index:05d}",
                ),
                _edge(
                    f"edge:{base + 3:05d}",
                    "writes_variable",
                    f"step:{index:05d}",
                    f"variable:{index + 1:05d}",
                ),
            )
        )
    return {"nodes": nodes, "edges": edges, "findings": []}


def test_graph_index_builds_type_and_source_projections_without_mutating_graph():
    graph = _indexed_graph()
    original = copy.deepcopy(graph)

    index = GraphIndex(graph)

    assert index.nodes_by_id["dataset:a"] is graph["nodes"][1]
    assert [
        edge["id"]
        for edge in index.outgoing_edges_by_node["dataset:a"]["reads_dataset"]
    ] == ["edge:1"]
    assert [
        edge["id"] for edge in index.incoming_edges_by_node["step:1"]["reads_dataset"]
    ] == ["edge:1"]
    assert index.findings_by_affected_object["dataset:a"][0]["id"] == "finding:1"
    assert index.findings_by_affected_object["edge:2"][0]["id"] == "finding:1"
    assert index.source_file_index["main.sas"]["nodes"] == ["dataset:a", "step:1"]
    assert index.source_file_index["main.sas"]["edges"] == ["edge:1", "edge:2"]
    assert graph == original


def test_graph_index_exposes_clear_aliases_for_plain_dict_projections():
    index = GraphIndex(_indexed_graph())

    assert index.outgoing_edges is index.outgoing_edges_by_node
    assert index.incoming_edges is index.incoming_edges_by_node
    assert index.findings_by_object is index.findings_by_affected_object
    assert index.source_files is index.source_file_index


def test_trace_lineage_marks_cycle_and_terminates():
    result = trace_lineage(_cycle_graph(), "dataset:start", "downstream")

    assert [node["id"] for node in result["downstream_nodes"]] == [
        "step:loop",
        "dataset:end",
    ]
    closing_edge = next(edge for edge in result["edges"] if edge["id"] == "edge:3")
    assert closing_edge["cycle"] is True
    assert result["truncated"] is False
    assert result["visited_count"] == 3


def test_query_bounds_defaults_apply_and_report_truncation():
    graph = _linear_graph(8)

    default_result = trace_lineage(graph, "dataset:000", "downstream")
    assert len(default_result["downstream_nodes"]) == 5
    assert default_result["truncated"] is True
    assert default_result["visited_count"] == 6
    assert default_result["depth_hit"] is True
    assert default_result["limit_hit"] is False
    assert default_result["continuation"]

    limited_result = trace_lineage(
        _linear_graph(8), "dataset:000", "downstream", depth=20, limit=2
    )
    assert len(limited_result["downstream_nodes"]) == 2
    assert limited_result["truncated"] is True
    assert limited_result["visited_count"] == 3
    assert limited_result["limit_hit"] is True
    assert limited_result["depth_hit"] is False


def test_query_bounds_reject_values_above_ticket_ceilings():
    graph = _linear_graph(2)

    with pytest.raises(ValueError, match="depth"):
        trace_lineage(graph, "dataset:000", "downstream", depth=21)
    with pytest.raises(ValueError, match="limit"):
        trace_lineage(graph, "dataset:000", "downstream", limit=5001)
    with pytest.raises(ValueError, match="depth"):
        analyze_impact(
            {
                "nodes": [_node("variable:a", "Variable")],
                "edges": [],
            },
            "variable:a",
            depth=21,
        )


def test_search_and_impact_include_bounded_metadata():
    graph = {
        "nodes": [
            _node("variable:a", "Variable", "alpha"),
            _node("step:1", "Step", "alpha step"),
            _node("variable:b", "Variable", "beta"),
        ],
        "edges": [
            _edge("edge:1", "reads_variable", "variable:a", "step:1"),
            _edge("edge:2", "writes_variable", "step:1", "variable:b"),
        ],
    }

    search_result = search_nodes(graph, "alpha", limit=1)
    assert len(search_result["matches"]) == 1
    assert search_result["truncated"] is True
    assert search_result["visited_count"] == 3
    assert search_result["limit_hit"] is True

    impact_result = analyze_impact(graph, "variable:a", depth=20, limit=1)
    assert impact_result["start"] == "variable:a"
    assert impact_result["truncated"] is True
    assert impact_result["limit_hit"] is True
    assert impact_result["visited_count"] == 2


def test_existing_query_keys_survive_on_real_pipeline_graph():
    graph = _real_pipeline_graph()
    dataset_id = next(
        node["id"] for node in graph["nodes"] if node["type"] == "Dataset"
    )
    variable_id = next(
        node["id"] for node in graph["nodes"] if node["type"] == "Variable"
    )

    search_result = search_nodes(graph, "Dataset")
    lineage_result = trace_lineage(graph, dataset_id, "both")
    impact_result = analyze_impact(graph, variable_id)

    assert {"query", "matches"}.issubset(search_result)
    assert {"start", "upstream_nodes", "downstream_nodes", "edges"}.issubset(
        lineage_result
    )
    assert {
        "start",
        "reached_variables",
        "reached_operations",
        "category_facts",
    }.issubset(impact_result)
    assert all(
        not result["truncated"]
        for result in (search_result, lineage_result, impact_result)
    )


def _p95_ms(samples):
    rank = max(1, int(math.ceil(len(samples) * 0.95))) - 1
    return sorted(samples)[rank] * 1000


@pytest.mark.skipif(
    os.environ.get("SAS_GRAPH_BENCH") != "1",
    reason="set SAS_GRAPH_BENCH=1 to run the local latency benchmark",
)
def test_bench_10k_graph_p95_per_primitive():
    graph = synthetic_graph()
    queries = {
        "search_nodes": lambda: search_nodes(graph, "dataset"),
        "trace_lineage": lambda: trace_lineage(graph, "dataset:00000", "downstream"),
        "analyze_impact": lambda: analyze_impact(graph, "variable:00000"),
    }
    figures = {}
    for name, query in queries.items():
        query()
        samples = []
        for _ in range(15):
            start = time.perf_counter()
            query()
            samples.append(time.perf_counter() - start)
        figures[name] = _p95_ms(samples)
        print(f"benchmark {name} p95_ms={figures[name]:.3f}")
        assert figures[name] < 200


def demo():
    """Run the non-benchmark tests without requiring pytest fixtures."""
    tests = [
        test_graph_index_builds_type_and_source_projections_without_mutating_graph,
        test_graph_index_exposes_clear_aliases_for_plain_dict_projections,
        test_trace_lineage_marks_cycle_and_terminates,
        test_query_bounds_defaults_apply_and_report_truncation,
        test_query_bounds_reject_values_above_ticket_ceilings,
        test_search_and_impact_include_bounded_metadata,
        test_existing_query_keys_survive_on_real_pipeline_graph,
    ]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    if os.environ.get("SAS_GRAPH_BENCH") == "1":
        test_bench_10k_graph_p95_per_primitive()
    print("graph index: all checks passed")


if __name__ == "__main__":
    demo()
