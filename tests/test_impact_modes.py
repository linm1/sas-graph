import json

import conftest  # noqa: F401
import pytest

from sas_graph.cli import main
from sas_graph.graph_index import GraphIndex
from sas_graph.graph_queries import analyze_impact, impact


def _node(node_id, node_type, label=None):
    return {
        "id": node_id,
        "type": node_type,
        "label": label or node_id,
        "source": None,
    }


def _edge(
    edge_id, edge_type, source_id, target_id, kind="OBSERVED", resolution="EXACT"
):
    return {
        "id": edge_id,
        "type": edge_type,
        "from": source_id,
        "to": target_id,
        "source": None,
        "evidence": {
            "kind": kind,
            "resolution": resolution,
        },
    }


def _graph():
    nodes = [
        _node("variable:seed", "Variable"),
        _node("step:known", "Step"),
        _node("variable:known", "Variable"),
        _node("step:selection", "SqlStatement"),
        _node("step:sink", "Step"),
        _node("unknownvariable:missing@1", "UnknownVariable"),
        _node("variable:partial", "Variable"),
        _node("dataset:out", "Dataset"),
        _node("step:uncertain", "Step"),
        _node("variable:uncertain", "Variable"),
        _node("external:file", "ExternalFile"),
    ]
    edges = [
        _edge("edge:01", "reads_variable", "variable:seed", "step:known"),
        _edge("edge:02", "writes_variable", "step:known", "variable:known"),
        _edge("edge:03", "joins_on", "variable:seed", "step:selection"),
        _edge(
            "edge:04",
            "writes_variable",
            "step:known",
            "variable:partial",
            "RESOLVED",
            "PARTIAL",
        ),
        _edge("edge:05", "reads_variable", "variable:seed", "step:sink"),
        _edge(
            "edge:06",
            "reads_variable",
            "variable:seed",
            "unknownvariable:missing@1",
            "UNKNOWN",
            "UNRESOLVED",
        ),
        _edge(
            "edge:07",
            "reads_variable",
            "variable:seed",
            "step:uncertain",
            "UNKNOWN",
            "UNRESOLVED",
        ),
        _edge("edge:08", "writes_variable", "step:uncertain", "variable:uncertain"),
        _edge("edge:09", "writes_dataset", "step:known", "dataset:out"),
        _edge("edge:12", "writes_external_file", "dataset:out", "external:file"),
    ]
    return {"nodes": nodes, "edges": edges}


def _by_id(result):
    return {entry["id"]: entry for entry in result["impacted"]}


def test_impact_modes_overlap_and_filter_one_traversal():
    result = impact(
        _graph(),
        "variable:seed",
        modes=("structural", "value_flow", "selection"),
    )

    entries = _by_id(result)
    assert entries["step:known"]["modes"] == ["structural", "value_flow"]
    assert "step:selection" in entries
    assert entries["step:selection"]["modes"] == ["structural", "selection"]
    assert "dataset:out" in entries
    assert {path["mode"] for path in entries["step:selection"]["paths"]} == {
        "structural",
        "selection",
    }


def test_impact_returns_paths_with_per_edge_evidence_and_all_nodes():
    result = impact(_graph(), "variable:seed", mode="structural")
    entries = _by_id(result)
    path = next(
        path
        for path in entries["variable:known"]["paths"]
        if path["mode"] == "structural"
    )

    assert path["nodes"] == ["variable:seed", "step:known", "variable:known"]
    assert [edge["id"] for edge in path["edges"]] == ["edge:01", "edge:02"]
    assert [edge["evidence"]["kind"] for edge in path["edges"]] == [
        "OBSERVED",
        "OBSERVED",
    ]
    returned_ids = {result["start"]} | set(entries)
    assert all(
        {edge["from"], edge["to"]} <= returned_ids
        for path in result["paths"]
        for edge in path["edges"]
    )


def test_impact_classification_uses_weakest_path_evidence():
    graph = _graph()
    graph["edges"].extend(
        [
            _edge("edge:10", "reads_variable", "variable:seed", "step:uncertain"),
            _edge("edge:11", "writes_variable", "step:uncertain", "variable:uncertain"),
        ]
    )
    # The UNKNOWN path to step:uncertain outranks the all-OBSERVED path.
    result = impact(graph, "variable:seed", mode="structural")
    assert _by_id(result)["step:uncertain"]["classification"] == "unresolved_boundary"


def test_impact_resolved_partial_is_possible_not_known():
    result = impact(_graph(), "variable:seed", mode="structural")

    assert _by_id(result)["variable:partial"]["classification"] == "possibly_impacted"


def test_impact_returns_unknown_and_sink_boundaries():
    result = impact(_graph(), "variable:seed", mode="value_flow")
    entries = _by_id(result)

    assert entries["unknownvariable:missing@1"]["boundary"] is True
    assert entries["unknownvariable:missing@1"]["boundary_reason"] == "unknown_node"
    assert (
        entries["unknownvariable:missing@1"]["classification"] == "unresolved_boundary"
    )
    assert entries["step:sink"]["boundary"] is True
    assert entries["step:sink"]["boundary_reason"] == "sink"

    structural = _by_id(impact(_graph(), "variable:seed", mode="structural"))
    assert structural["external:file"]["boundary_reason"] == "external_file"


def test_impact_depth_counts_semantic_hops_and_reports_boundary():
    result = impact(_graph(), "variable:seed", mode="structural", depth=0)
    entries = _by_id(result)

    assert "step:known" in entries
    assert "variable:known" not in entries
    assert entries["step:known"]["boundary_reason"] == "depth"
    assert result["depth_hit"] is True


def test_impact_cycle_closing_edge_is_marked_without_revisiting_nodes():
    graph = _graph()
    graph["edges"].append(_edge("edge:13", "derives", "variable:known", "step:known"))
    result = impact(graph, "variable:seed", mode="structural")
    known = _by_id(result)["step:known"]

    assert any(edge.get("cycle") for path in known["paths"] for edge in path["edges"])


def test_impact_limit_is_checked_before_appending_edge_or_node():
    result = impact(_graph(), "variable:seed", mode="structural", depth=20, limit=1)
    entries = _by_id(result)

    assert len(entries) == 1
    returned_ids = {result["start"]} | set(entries)
    assert result["limit_hit"] is True
    assert all(
        {edge["from"], edge["to"]} <= returned_ids
        for path in result["paths"]
        for edge in path["edges"]
    )


@pytest.mark.parametrize("mode", ["structural", "value_flow", "selection"])
@pytest.mark.parametrize("depth", [0, 1, 5, 20])
@pytest.mark.parametrize("limit", [1, 2, 5, 20])
def test_impact_bounds_never_emit_edges_to_unreturned_nodes(mode, depth, limit):
    result = impact(_graph(), "variable:seed", mode=mode, depth=depth, limit=limit)
    returned_ids = {result["start"]} | {entry["id"] for entry in result["impacted"]}

    assert all(
        {edge["from"], edge["to"]} <= returned_ids
        for path in result["paths"]
        for edge in path["edges"]
    )


def test_impact_rejects_output_mode_and_invalid_bounds():
    with pytest.raises(ValueError, match="mode"):
        impact(_graph(), "variable:seed", mode="output")
    with pytest.raises(ValueError, match="depth"):
        impact(_graph(), "variable:seed", depth=21)
    with pytest.raises(ValueError, match="limit"):
        impact(_graph(), "variable:seed", limit=5001)


def test_impact_result_is_json_serializable():
    result = impact(_graph(), "variable:seed")
    json.dumps(result)


def test_analyze_impact_exposes_modes_without_changing_legacy_default():
    graph = _graph()
    result = analyze_impact(graph, "variable:seed", mode="selection")

    assert result["modes"] == ["selection"]
    assert result["impacted"]


def test_impact_accepts_a_reusable_index_without_mutating_graph():
    graph = _graph()
    before = json.dumps(graph, sort_keys=True)

    result = impact(
        graph,
        "variable:seed",
        mode="value_flow",
        index=GraphIndex(graph),
    )

    assert result["start"] == "variable:seed"
    assert json.dumps(graph, sort_keys=True) == before


def test_query_impact_modes_cli_emits_json_and_preserves_exit_contract(
    capsys, tmp_path
):
    path = tmp_path / "graph.json"
    path.write_text(
        json.dumps(dict(_graph(), schema_version="0.2.0", run_status="COMPLETE")),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "query-impact",
                "--graph",
                str(path),
                "--node",
                "variable:seed",
                "--mode",
                "value_flow",
                "--depth",
                "20",
                "--limit",
                "20",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["modes"] == ["value_flow"]
    assert output["start"] == "variable:seed"

    with pytest.raises(SystemExit) as error:
        main(
            [
                "query-impact",
                "--graph",
                str(path),
                "--node",
                "variable:seed",
                "--mode",
                "output",
            ]
        )
    assert error.value.code == 2


if __name__ == "__main__":
    result = impact(_graph(), "variable:seed")
    assert result["start"] == "variable:seed"
    assert result["paths"]
    print("impact modes: standalone checks passed")
