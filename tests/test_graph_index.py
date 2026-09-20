"""GraphIndex and bounded-query acceptance tests.

The small hand-built graphs exercise index shape and traversal boundaries;
the real-pipeline test keeps the query contract tied to graph.json output.
"""

import copy
import gc
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
BASIC_ADAE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "basic_adae"


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


def _basic_adae_graph():
    result = load_config(BASIC_ADAE_FIXTURE_DIR / "project.yaml")
    assert result.ok, result.findings
    graph = run(result, run_id="graph-index-basic-adae")
    with tempfile.TemporaryDirectory() as temporary:
        return load_graph(save_graph(graph, Path(temporary) / "graph.json"))


def synthetic_graph(node_count=10000, seed=1337):
    """Build a deterministic graph for the optional local latency benchmark."""
    if node_count < 4:
        raise ValueError("node_count must be at least 4")

    rng = random.Random(seed)
    chain_length = (node_count - 1) // 3
    fanout = 4
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

    downstream_targets = {}
    for index in range(chain_length - 1):
        target_count = min(fanout, chain_length - index - 1)
        downstream_targets[index] = rng.sample(
            range(index + 1, chain_length), target_count
        )

    edges = []
    for index in range(chain_length - 1):
        base = index * (2 * fanout + 2)
        edges.append(
            _edge(
                f"edge:{base:05d}",
                "reads_dataset",
                f"dataset:{index:05d}",
                f"step:{index:05d}",
            )
        )
        for offset, target_index in enumerate(downstream_targets[index], start=1):
            edges.append(
                _edge(
                    f"edge:{base + offset:05d}",
                    "writes_dataset",
                    f"step:{index:05d}",
                    f"dataset:{target_index:05d}",
                )
            )
        edges.append(
            _edge(
                f"edge:{base + fanout + 1:05d}",
                "reads_variable",
                f"variable:{index:05d}",
                f"step:{index:05d}",
            )
        )
        for offset, target_index in enumerate(
            downstream_targets[index], start=fanout + 2
        ):
            edges.append(
                _edge(
                    f"edge:{base + offset:05d}",
                    "writes_variable",
                    f"step:{index:05d}",
                    f"variable:{target_index:05d}",
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


def test_graph_index_skips_nodes_without_ids():
    graph = _indexed_graph()
    graph["nodes"].append({"type": "Dataset", "label": "missing id"})

    index = GraphIndex(graph)

    assert None not in index.nodes_by_id


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
    graph = _linear_graph(12)

    default_result = trace_lineage(graph, "dataset:000", "downstream")
    assert len(default_result["downstream_nodes"]) == 11
    assert default_result["truncated"] is True
    assert default_result["visited_count"] == 12
    assert default_result["depth_hit"] is True
    assert default_result["limit_hit"] is False
    assert default_result["continuation"] == ["step:011"]

    zero_depth_result = trace_lineage(
        _linear_graph(2), "dataset:000", "downstream", depth=0
    )
    assert [node["id"] for node in zero_depth_result["downstream_nodes"]] == [
        "step:001"
    ]
    assert [edge["id"] for edge in zero_depth_result["edges"]] == ["edge:001"]
    assert zero_depth_result["depth_hit"] is True

    limited_result = trace_lineage(
        _linear_graph(8), "dataset:000", "downstream", depth=20, limit=2
    )
    assert len(limited_result["downstream_nodes"]) == 2
    assert limited_result["truncated"] is True
    assert limited_result["visited_count"] == 3
    assert limited_result["limit_hit"] is True
    assert limited_result["depth_hit"] is False
    returned_ids = {
        limited_result["start"],
        *(node["id"] for node in limited_result["downstream_nodes"]),
    }
    assert all(
        edge.get("cycle")
        or {edge["from"], edge["to"]} <= returned_ids
        for edge in limited_result["edges"]
    )


def test_continuation_deduplicates_repeated_frontier_ids_in_order():
    graph = {
        "nodes": [
            _node("dataset:start", "Dataset"),
            _node("step:a", "Step"),
            _node("step:b", "Step"),
            _node("dataset:a1", "Dataset"),
            _node("dataset:a2", "Dataset"),
            _node("dataset:b1", "Dataset"),
            _node("dataset:b2", "Dataset"),
        ],
        "edges": [
            _edge("edge:1", "reads_dataset", "dataset:start", "step:a"),
            _edge("edge:2", "reads_dataset", "dataset:start", "step:b"),
            _edge("edge:3", "writes_dataset", "step:a", "dataset:a1"),
            _edge("edge:4", "writes_dataset", "step:a", "dataset:a2"),
            _edge("edge:5", "writes_dataset", "step:b", "dataset:b1"),
            _edge("edge:6", "writes_dataset", "step:b", "dataset:b2"),
        ],
    }

    result = trace_lineage(
        graph, "dataset:start", "downstream", depth=0, limit=20
    )

    assert result["continuation"] == ["step:a", "step:b"]


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
    returned_variables = set(impact_result["reached_variables"])
    edge_by_id = {edge["id"]: edge for edge in graph["edges"]}
    assert all(
        returned_variables
        & {edge_by_id[fact["edge"]]["from"], edge_by_id[fact["edge"]]["to"]}
        for fact in impact_result["category_facts"]
    )
    assert {fact["edge"] for fact in impact_result["category_facts"]} == {"edge:1"}


def test_query_limits_bound_wide_graph_results_and_frontier():
    graph = synthetic_graph(node_count=300, seed=2026)
    index = GraphIndex(graph)
    limit = 5

    lineage_result = trace_lineage(
        graph,
        "dataset:00000",
        "downstream",
        depth=20,
        limit=limit,
        index=index,
    )
    assert len(lineage_result["downstream_nodes"]) == limit
    assert lineage_result["limit_hit"] is True
    lineage_returned_ids = {
        lineage_result["start"],
        *(node["id"] for node in lineage_result["downstream_nodes"]),
    }
    assert set(lineage_result["continuation"]) - lineage_returned_ids
    assert all(
        {edge["from"], edge["to"]} <= lineage_returned_ids
        for edge in lineage_result["edges"]
    )

    impact_result = analyze_impact(
        graph,
        "variable:00000",
        depth=20,
        limit=limit,
        index=index,
    )
    assert (
        len(impact_result["reached_variables"])
        - 1
        + len(impact_result["reached_operations"])
        == limit
    )
    assert impact_result["limit_hit"] is True
    impact_returned_ids = set(impact_result["reached_variables"]) | {
        operation["id"] for operation in impact_result["reached_operations"]
    }
    assert set(impact_result["continuation"]) - impact_returned_ids
    edge_by_id = {edge["id"]: edge for edge in graph["edges"]}
    assert all(
        {
            edge_by_id[fact["edge"]]["from"],
            edge_by_id[fact["edge"]]["to"],
            fact["on"],
        }
        <= impact_returned_ids
        for fact in impact_result["category_facts"]
    )


def test_default_lineage_depth_counts_dataset_hops_on_basic_adae_fixture():
    result = trace_lineage(_basic_adae_graph(), "dataset:adam.adae", "both")

    assert {node["id"] for node in result["upstream_nodes"]} == {
        "step:003",
        "macrocall:001",
        "dataset:work.adae_srt",
        "step:002",
        "dataset:work.adae_pre",
        "step:001",
        "dataset:sdtm.ae",
        "dataset:adam.adsl",
    }
    assert result["downstream_nodes"] == []
    assert result["truncated"] is False
    assert result["depth_hit"] is False


def test_impact_depth_counts_variable_hops_and_returns_structural_operations():
    graph = {
        "nodes": [
            _node("variable:a", "Variable"),
            _node("step:1", "Step"),
            _node("variable:b", "Variable"),
            _node("step:2", "Step"),
            _node("variable:c", "Variable"),
        ],
        "edges": [
            _edge("edge:1", "reads_variable", "variable:a", "step:1"),
            _edge("edge:2", "writes_variable", "step:1", "variable:b"),
            _edge("edge:3", "reads_variable", "variable:b", "step:2"),
            _edge("edge:4", "writes_variable", "step:2", "variable:c"),
        ],
    }

    result = analyze_impact(graph, "variable:a", depth=1)

    assert result["reached_variables"] == ["variable:a", "variable:b"]
    assert [operation["id"] for operation in result["reached_operations"]] == [
        "step:1",
        "step:2",
    ]
    assert {fact["edge"] for fact in result["category_facts"]} == {
        "edge:1",
        "edge:2",
        "edge:3",
    }
    assert result["depth_hit"] is True
    assert result["continuation"] == ["step:2"]


def test_queries_accept_a_reusable_graph_index():
    graph = {
        "nodes": [
            _node("dataset:a", "Dataset", "alpha"),
            _node("step:1", "Step", "build"),
            _node("dataset:b", "Dataset", "beta"),
            _node("variable:a", "Variable", "alpha"),
            _node("variable:b", "Variable", "beta"),
        ],
        "edges": [
            _edge("dataset-edge:1", "reads_dataset", "dataset:a", "step:1"),
            _edge("dataset-edge:2", "writes_dataset", "step:1", "dataset:b"),
            _edge("variable-edge:1", "reads_variable", "variable:a", "step:1"),
            _edge("variable-edge:2", "writes_variable", "step:1", "variable:b"),
        ],
    }
    index = GraphIndex(graph)

    assert search_nodes(graph, "a") == search_nodes(graph, "a", index=index)
    assert trace_lineage(graph, "dataset:a", "downstream") == trace_lineage(
        graph, "dataset:a", "downstream", index=index
    )
    assert analyze_impact(graph, "variable:a") == analyze_impact(
        graph, "variable:a", index=index
    )


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
    index = GraphIndex(graph)
    queries = {
        "search_nodes": lambda: search_nodes(
            graph, "dataset", limit=5000, index=index
        ),
        "trace_lineage": lambda: trace_lineage(
            graph,
            "dataset:00000",
            "downstream",
            depth=20,
            limit=5000,
            index=index,
        ),
        "analyze_impact": lambda: analyze_impact(
            graph, "variable:00000", depth=20, limit=5000, index=index
        ),
    }
    truncated_queries = {
        "search_nodes": lambda: search_nodes(
            graph, "dataset", limit=200, index=index
        ),
        "trace_lineage": lambda: trace_lineage(
            graph,
            "dataset:00000",
            "downstream",
            depth=20,
            limit=200,
            index=index,
        ),
        "analyze_impact": lambda: analyze_impact(
            graph, "variable:00000", depth=20, limit=200, index=index
        ),
    }
    warm_results = {name: query() for name, query in queries.items()}
    truncated_results = {
        name: query() for name, query in truncated_queries.items()
    }
    reached_counts = {
        "search_nodes": len(warm_results["search_nodes"]["matches"]),
        "trace_lineage": len(warm_results["trace_lineage"]["downstream_nodes"]),
        "analyze_impact": (
            len(warm_results["analyze_impact"]["reached_variables"])
            - 1
            + len(warm_results["analyze_impact"]["reached_operations"])
        ),
    }
    continuation_lengths = {
        name: len(result["continuation"])
        for name, result in truncated_results.items()
    }
    assert all(result["truncated"] for result in truncated_results.values())
    assert all(length > 0 for length in continuation_lengths.values())
    assert reached_counts["trace_lineage"] >= 1000
    assert reached_counts["analyze_impact"] >= 1000

    sample_count = 30
    gc.collect()
    cold_samples = []
    for _ in range(sample_count):
        start = time.perf_counter()
        GraphIndex(graph)
        cold_samples.append(time.perf_counter() - start)
    cold_p95 = _p95_ms(cold_samples)
    assert cold_p95 < 200

    for name, query in queries.items():
        query()
        gc.collect()
        samples = []
        for _ in range(sample_count):
            start = time.perf_counter()
            query()
            samples.append(time.perf_counter() - start)
        warm_p95 = _p95_ms(samples)
        truncated_query = truncated_queries[name]
        truncated_query()
        gc.collect()
        truncated_samples = []
        for _ in range(sample_count):
            start = time.perf_counter()
            truncated_query()
            truncated_samples.append(time.perf_counter() - start)
        truncated_p95 = _p95_ms(truncated_samples)
        print(
            f"benchmark {name} warm_p95_ms={warm_p95:.3f} "
            f"truncated_p95_ms={truncated_p95:.3f} "
            f"cold_p95_ms={cold_p95:.3f} reached={reached_counts[name]} "
            f"continuation_len={continuation_lengths[name]}"
        )
        assert warm_p95 < 200
        assert truncated_p95 < 200


def demo():
    """Run the non-benchmark tests without requiring pytest fixtures."""
    tests = [
        test_graph_index_builds_type_and_source_projections_without_mutating_graph,
        test_graph_index_skips_nodes_without_ids,
        test_trace_lineage_marks_cycle_and_terminates,
        test_query_bounds_defaults_apply_and_report_truncation,
        test_continuation_deduplicates_repeated_frontier_ids_in_order,
        test_query_bounds_reject_values_above_ticket_ceilings,
        test_search_and_impact_include_bounded_metadata,
        test_query_limits_bound_wide_graph_results_and_frontier,
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
