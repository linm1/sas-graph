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


def test_reference_graph_is_0_3_0_and_validates_without_evidence():
    graph = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert graph["schema_version"] == "0.3.0"
    assert graph["schema"] == {
        "producer": "sas-graph",
        "producer_version": "0.2.0",
        "capabilities": ["dataset_lineage", "variable_lineage"],
    }
    assert graph["main_programs"]
    assert graph["setup_file"] == "setup.sas"
    assert all("evidence" not in edge for edge in graph["edges"])
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
        "capabilities": ["dataset_lineage", "variable_lineage"],
    }
    assert normalized["main_programs"] == source["main_programs"]
    assert normalized["setup_file"] == source["setup_file"]
    assert normalized["nodes"] == source["nodes"]
    assert normalized["edges"] == source["edges"]
    assert validate_graph(normalized) == []


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print("ok  {0}".format(name))
    print("graph_schema: all checks passed")


if __name__ == "__main__":
    demo()
