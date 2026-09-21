"""End-to-end B2 coverage for the synthetic SQL construct project."""

from pathlib import Path

import conftest  # noqa: F401

from sas_graph.config import load_config
from sas_graph.run_pipeline import run


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sql_constructs"


def _graph():
    config = load_config(FIXTURE_DIR / "project.yaml")
    assert config.ok, config.findings
    return run(config, run_id="sql-constructs")


def test_synthetic_fixture_emits_all_b2_semantic_edges():
    graph = _graph()
    counts = {
        edge_type: sum(edge["type"] == edge_type for edge in graph["edges"])
        for edge_type in ("joins_on", "filters_dataset", "groups_by", "sorts_by")
    }

    assert counts["joins_on"] == 4
    assert counts["filters_dataset"] == 1
    assert counts["groups_by"] == 2
    assert counts["sorts_by"] == 1


def test_synthetic_fixture_keeps_ambiguous_column_unknown():
    graph = _graph()
    ambiguous = [
        finding
        for finding in graph["findings"]
        if finding["type"] == "unqualified_variable_reference"
    ]
    assert len(ambiguous) == 1
    assert "value" in ambiguous[0]["message"]
    assert "dataset:work.source_a" in ambiguous[0]["message"]
    assert "dataset:work.source_b" in ambiguous[0]["message"]
    assert any(
        node["type"] == "UnknownVariable"
        and node["unresolved_expression"] == "value"
        for node in graph["nodes"]
    )
    assert not any(
        edge["type"] == "filters_dataset"
        and edge["from"].startswith("unknownvariable:")
        for edge in graph["edges"]
    )


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print("ok  {}".format(name))
    print("sql fixture: all checks passed")


if __name__ == "__main__":
    demo()
