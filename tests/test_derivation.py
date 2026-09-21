"""Acceptance checks for DATA-step derivation capability emission."""

import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if __name__ == "__main__" and str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import conftest  # noqa: F401

from sas_graph.config import load_config
from sas_graph.run_pipeline import run


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
FIXTURE_NAMES = ("basic_adae", "variable_lineage", "synthetic_multi_program")


def _run_fixture(name, derivation_v1):
    result = load_config(FIXTURE_ROOT / name / "project.yaml")
    assert result.ok, result.findings
    return run(
        result,
        run_id="derivation-" + name,
        derivation_v1=derivation_v1,
    )


def _legacy_graph(graph):
    """Return the graph facts that existed before derivation_v1."""

    return {
        "nodes": graph["nodes"],
        "edges": [
            {key: value for key, value in edge.items() if key != "id"}
            for edge in graph["edges"]
            if edge["type"] not in {"derives", "conditioned_by"}
        ],
        "findings": graph["findings"],
    }


def test_derivation_flag_off_preserves_the_legacy_fixture_graph():
    """The off path keeps nodes, existing edges, and findings unchanged."""

    always_on_capabilities = [
        "dataset_lineage",
        "variable_lineage",
        "evidence_v1",
    ]
    expected_new_edges = {
        "basic_adae": (0, 0),
        "variable_lineage": (4, 0),
        "synthetic_multi_program": (0, 0),
    }
    for name in FIXTURE_NAMES:
        enabled = _run_fixture(name, derivation_v1=True)
        disabled = _run_fixture(name, derivation_v1=False)
        assert _legacy_graph(disabled) == _legacy_graph(enabled)
        assert enabled["schema"]["capabilities"] == always_on_capabilities + [
            "derivation_v1"
        ]
        assert disabled["schema"]["capabilities"] == always_on_capabilities
        assert (
            sum(edge["type"] == "derives" for edge in enabled["edges"]),
            sum(edge["type"] == "conditioned_by" for edge in enabled["edges"]),
        ) == expected_new_edges[name]


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print("ok  {0}".format(name))
    print("derivation: all checks passed")


if __name__ == "__main__":
    demo()
