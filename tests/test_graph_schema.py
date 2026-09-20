"""Registry, reference graph, and schema normalizer tests for Phase A1."""

import json
from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.graph_io import SUPPORTED_SCHEMA_VERSIONS, load_graph
from sas_graph.graph_schema import EDGE_ENDPOINTS, EDGE_TYPES, NODE_TYPES, normalize_graph
from sas_graph.graph_validator import validate_graph


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "reference_graph_0_3_0.json"


EXPECTED_NODE_TYPES = {
    "Dataset",
    "UnknownDataset",
    "Variable",
    "UnknownVariable",
    "Step",
    "ExternalFile",
    "InactiveEvidence",
    "CommentBlock",
    "CommentedStatement",
    "Library",
    "MacroCall",
    "MacroParameter",
    "MacroConflict",
    "MacroDefinition",
    "MacroSourceFile",
    "UnknownMacro",
    "MacroContract",
    "MacroConditional",
    "MacroLoop",
    "ConditionalBranch",
    "SqlBlock",
    "SqlStatement",
    "SetupFile",
    "MacroVariable",
    "Program",
}


def test_registry_contains_the_emitted_node_vocabulary_without_datastep():
    assert NODE_TYPES == frozenset(EXPECTED_NODE_TYPES)
    assert "DataStep" not in NODE_TYPES


def test_registry_contains_the_emitted_edge_vocabulary_without_dead_types():
    assert EDGE_TYPES == frozenset(EDGE_ENDPOINTS)
    assert "contract_derived" not in EDGE_TYPES
    assert "conflicts_with" not in EDGE_TYPES
    assert "depends_on" in EDGE_TYPES
    assert {"derives", "conditioned_by"} <= EDGE_TYPES


def test_derivation_endpoints_mirror_writes_and_reverse_its_direction():
    assert EDGE_ENDPOINTS["derives"] == EDGE_ENDPOINTS["writes_variable"]
    assert EDGE_ENDPOINTS["conditioned_by"] == frozenset(
        (target, source)
        for source, target in EDGE_ENDPOINTS["writes_variable"]
    )


def test_dataset_and_variable_endpoint_aliases_expand_to_unknown_types():
    assert ("Dataset", "Step") in EDGE_ENDPOINTS["reads_dataset"]
    assert ("UnknownDataset", "Step") in EDGE_ENDPOINTS["reads_dataset"]
    assert ("Variable", "Step") in EDGE_ENDPOINTS["reads_variable"]
    assert ("UnknownVariable", "Step") in EDGE_ENDPOINTS["reads_variable"]
    assert set(EDGE_ENDPOINTS["depends_on"]) == {
        ("Dataset", "Dataset"),
        ("Dataset", "UnknownDataset"),
        ("UnknownDataset", "Dataset"),
        ("UnknownDataset", "UnknownDataset"),
    }


def test_derivation_edges_validate_with_or_without_the_capability_flag():
    graph = {
        "schema_version": "0.2.0",
        "nodes": [
            {"id": "step:001", "type": "Step", "label": "step"},
            {"id": "variable:work.a.flag", "type": "Variable", "label": "flag"},
            {
                "id": "unknownvariable:condition@2",
                "type": "UnknownVariable",
                "label": "condition",
            },
        ],
        "edges": [
            {
                "id": "edge:001",
                "type": "derives",
                "from": "step:001",
                "to": "variable:work.a.flag",
            },
            {
                "id": "edge:002",
                "type": "conditioned_by",
                "from": "unknownvariable:condition@2",
                "to": "step:001",
            },
        ],
    }

    assert validate_graph(graph) == []
    graph["schema"] = {"capabilities": []}
    assert validate_graph(graph) == []
    graph["schema"] = {"capabilities": ["derivation_v1"]}
    assert validate_graph(graph) == []


def test_derivation_edges_reject_reversed_endpoint_pairs():
    graph = {
        "schema_version": "0.2.0",
        "nodes": [
            {"id": "step:001", "type": "Step", "label": "step"},
            {"id": "variable:work.a.flag", "type": "Variable", "label": "flag"},
        ],
        "edges": [
            {
                "id": "edge:bad-1",
                "type": "derives",
                "from": "variable:work.a.flag",
                "to": "step:001",
            },
            {
                "id": "edge:bad-2",
                "type": "conditioned_by",
                "from": "step:001",
                "to": "variable:work.a.flag",
            },
        ],
    }

    problems = validate_graph(graph)
    assert any(
        "edge:bad-1" in problem and "illegal endpoint pair" in problem
        for problem in problems
    )
    assert any(
        "edge:bad-2" in problem and "illegal endpoint pair" in problem
        for problem in problems
    )


def test_reference_graph_is_0_3_0_and_validates_with_evidence():
    graph = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert graph["schema_version"] == "0.3.0"
    assert graph["schema"] == {
        "producer": "sas-graph",
        "producer_version": "0.2.0",
        "capabilities": ["dataset_lineage", "variable_lineage", "evidence_v1"],
    }
    assert graph["main_programs"]
    assert graph["setup_file"] == "setup.sas"
    assert all("evidence" in edge for edge in graph["edges"])
    assert validate_graph(graph) == []


def test_graph_io_accepts_both_supported_schema_versions():
    assert {"0.2.0", "0.3.0"} <= SUPPORTED_SCHEMA_VERSIONS
    assert load_graph(FIXTURE)["schema_version"] == "0.3.0"


def test_normalizer_upconverts_a_real_graph_without_dropping_envelope_data():
    source_path = next(
        (Path(__file__).resolve().parent / "fixtures").glob(
            "*/graph_runs/runs/*/graph.json"
        )
    )
    source = json.loads(source_path.read_text(encoding="utf-8"))
    normalized = normalize_graph(source)

    assert source["schema_version"] == "0.2.0"
    assert normalized["schema_version"] == "0.3.0"
    assert normalized["schema"] == {
        "producer": "sas-graph",
        "producer_version": "0.2.0",
        "capabilities": ["dataset_lineage", "variable_lineage", "evidence_v1"],
    }
    assert normalized["main_programs"] == source["main_programs"]
    assert normalized["setup_file"] == source["setup_file"]
    assert normalized["nodes"] == source["nodes"]
    assert normalized["edges"] != source["edges"]
    assert all("evidence" in edge for edge in normalized["edges"])
    assert all(
        edge["evidence"]["kind"] == "UNKNOWN"
        and edge["evidence"]["resolution"] == "NOT_ATTEMPTED"
        and edge["evidence"]["confidence"] is None
        for edge in normalized["edges"]
    )
    assert all("evidence" not in edge for edge in source["edges"])
    assert validate_graph(normalized) == []


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print("ok  {0}".format(name))
    print("graph_schema: all checks passed")


if __name__ == "__main__":
    demo()
