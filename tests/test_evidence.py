"""Evidence objects and graph evidence factory checks for Phase C1."""

import json

import conftest  # noqa: F401  (puts src/ on sys.path)

import pytest

from sas_graph.evidence import EvidenceKind, ResolutionStatus
from sas_graph.graph_model import GraphContext
from sas_graph.graph_schema import normalize_graph
from sas_graph.graph_validator import validate_graph


def make_context():
    return GraphContext(main_programs=["adae.sas"], setup_file="setup.sas", run_id="run-0001")


def source():
    return {
        "file": "adae.sas",
        "line_start": 12,
        "line_end": 14,
        "statement_order": 3,
        "original_text": "data work.adae;",
        "rule": "data_step_output",
    }


def test_factory_returns_serializable_evidence_with_source_ref():
    evidence = make_context().make_evidence(
        EvidenceKind.UNKNOWN,
        ResolutionStatus.NOT_ATTEMPTED,
        "data_step_output",
        source(),
    )

    assert json.loads(json.dumps(evidence)) == evidence
    assert evidence == {
        "kind": "UNKNOWN",
        "extractor": "data_step_output",
        "extractor_version": "1",
        "source_refs": [source()],
        "derivation_refs": [],
        "resolution": "NOT_ATTEMPTED",
        "confidence": None,
        "limitations": [],
    }


def test_unknown_factory_refuses_non_null_confidence():
    with pytest.raises(ValueError, match="UNKNOWN.*confidence"):
        make_context().make_evidence(
            EvidenceKind.UNKNOWN,
            ResolutionStatus.NOT_ATTEMPTED,
            "test",
            source(),
            confidence=0.0,
        )


def test_add_edge_defaults_to_unknown_evidence_and_preserves_source():
    context = make_context()
    edge_source = source()

    context.add_edge("reads_dataset", "dataset:sdtm.ae", "step:001", edge_source)

    edge = context.edges[0]
    assert edge["source"] == edge_source
    assert edge["evidence"]["kind"] == EvidenceKind.UNKNOWN
    assert edge["evidence"]["resolution"] == ResolutionStatus.NOT_ATTEMPTED
    assert edge["evidence"]["confidence"] is None
    assert edge["evidence"]["source_refs"] == [edge_source]


def test_add_edge_with_null_source_has_no_fabricated_source_ref():
    context = make_context()

    context.add_edge("reads_dataset", "dataset:sdtm.ae", "step:001", None)

    edge = context.edges[0]
    assert edge["source"] is None
    assert edge["evidence"]["source_refs"] == []


def test_validator_reports_unknown_confidence_and_invalid_vocabularies():
    graph = {
        "schema_version": "0.3.0",
        "nodes": [
            {"id": "dataset:one", "type": "Dataset", "label": "one"},
            {"id": "step:001", "type": "Step", "label": "step"},
        ],
        "edges": [
            {
                "id": "edge:bad",
                "type": "reads_dataset",
                "from": "dataset:one",
                "to": "step:001",
                "evidence": {
                    "kind": "UNKNOWN",
                    "resolution": "NOT_ATTEMPTED",
                    "confidence": 0.0,
                },
            }
        ],
    }

    problems = validate_graph(graph)

    assert len(problems) == 1
    assert "edge:bad" in problems[0]
    assert "confidence" in problems[0]


def test_validator_reports_invalid_kind_and_resolution():
    graph = {
        "schema_version": "0.3.0",
        "nodes": [
            {"id": "dataset:one", "type": "Dataset", "label": "one"},
            {"id": "step:001", "type": "Step", "label": "step"},
        ],
        "edges": [
            {
                "id": "edge:bad",
                "type": "reads_dataset",
                "from": "dataset:one",
                "to": "step:001",
                "evidence": {
                    "kind": "NOT_A_KIND",
                    "resolution": "NOT_A_RESOLUTION",
                    "confidence": None,
                },
            }
        ],
    }

    problems = validate_graph(graph)

    assert len(problems) == 2
    assert all("edge:bad" in problem for problem in problems)
    assert any("kind" in problem for problem in problems)
    assert any("resolution" in problem for problem in problems)


def test_edge_without_evidence_remains_compatible():
    graph = {
        "schema_version": "0.2.0",
        "nodes": [
            {"id": "dataset:one", "type": "Dataset", "label": "one"},
            {"id": "step:001", "type": "Step", "label": "step"},
        ],
        "edges": [
            {
                "id": "edge:001",
                "type": "reads_dataset",
                "from": "dataset:one",
                "to": "step:001",
            }
        ],
    }

    assert validate_graph(graph) == []


def test_normalizer_adds_evidence_without_mutating_source_graph():
    graph = {
        "schema_version": "0.2.0",
        "main_programs": ["main.sas"],
        "setup_file": "setup.sas",
        "nodes": [
            {"id": "dataset:one", "type": "Dataset", "label": "one"},
            {"id": "step:001", "type": "Step", "label": "step"},
        ],
        "edges": [
            {
                "id": "edge:001",
                "type": "reads_dataset",
                "from": "dataset:one",
                "to": "step:001",
            }
        ],
    }

    normalized = normalize_graph(graph)

    assert "evidence" not in graph["edges"][0]
    assert normalized is not graph
    assert normalized["schema"]["capabilities"] == [
        "dataset_lineage",
        "variable_lineage",
        "evidence_v1",
    ]
    assert normalized["edges"][0]["evidence"]["kind"] == EvidenceKind.UNKNOWN
    assert normalized["edges"][0]["evidence"]["resolution"] == ResolutionStatus.NOT_ATTEMPTED
    assert normalized["edges"][0]["evidence"]["confidence"] is None
    assert validate_graph(normalized) == []


def test_normalizer_leaves_existing_evidence_untouched():
    existing = {
        "kind": "OBSERVED",
        "extractor": "test",
        "extractor_version": "1",
        "source_refs": [],
        "derivation_refs": [],
        "resolution": "EXACT",
        "confidence": 1.0,
        "limitations": [],
    }
    graph = {
        "schema_version": "0.2.0",
        "nodes": [],
        "edges": [
            {
                "id": "edge:001",
                "type": "contains_step",
                "from": "program:001",
                "to": "step:001",
                "evidence": existing,
            }
        ],
    }

    normalized = normalize_graph(graph)

    assert normalized["edges"][0]["evidence"] == existing
    # The docstring makes this shared identity intentional.
    assert normalized["edges"][0]["evidence"] is existing


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print("ok  {0}".format(name))
    print("evidence: all checks passed")


if __name__ == "__main__":
    demo()
