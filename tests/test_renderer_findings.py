"""Findings renderer, exercised against the hand-written contract fixture."""

from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.graph_io import load_graph
from sas_graph.renderer_findings import STATUS_SECTIONS, render

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "hand_written_graph.json"


def test_header_carries_run_status():
    out = render(load_graph(FIXTURE))

    assert out.startswith("# SAS graph findings\n")
    assert "Run status: PARTIAL" in out


def test_every_section_is_present_in_plan_order():
    out = render(load_graph(FIXTURE))

    positions = [out.index(f"## {heading}") for _, heading in STATUS_SECTIONS]
    assert positions == sorted(positions)


def test_empty_sections_say_none_rather_than_vanishing():
    out = render(load_graph(FIXTURE))

    blocked = out.index("## BLOCKED")
    requires = out.index("## REQUIRES_DECISION")
    assert "_None._" in out[blocked:requires]


def test_unresolved_macro_source_finding_is_reported_with_evidence():
    out = render(load_graph(FIXTURE))

    section = out[out.index("## UNRESOLVED_MACRO_SOURCE") : out.index("## CONTRACT_INCOMPLETE")]
    assert "finding:001" in section
    assert "gm_missing" in section
    assert "`adae.sas` line 13" in section
    assert "rule `macro_source_not_found`" in section
    assert "Suggested action:" in section


def test_affected_nodes_render_as_labels_not_raw_ids():
    out = render(load_graph(FIXTURE))

    assert "`&unknown_out.`" in out
    assert "unknowndataset:&unknown_out." not in out


def _finding(status, finding_id):
    return {
        "id": finding_id,
        "status": status,
        "type": "macro_static_control_flow",
        "severity": "INFORMATION",
        "object": "test",
        "message": "status fixture",
        "source": None,
    }


def test_static_condition_supported_is_an_informational_pattern():
    graph = load_graph(FIXTURE)
    graph["findings"].append(_finding("STATIC_CONDITION_SUPPORTED", "finding:condition"))

    out = render(graph)

    section = out[out.index("## SUPPORTED INFORMATIONAL PATTERNS"):]
    assert "finding:condition" in section
    assert "UNRECOGNISED STATUS" not in out


def test_static_literal_loop_supported_is_an_informational_pattern():
    graph = load_graph(FIXTURE)
    graph["findings"].append(_finding("STATIC_LOOP_SUPPORTED", "finding:loop"))

    out = render(graph)

    section = out[out.index("## SUPPORTED INFORMATIONAL PATTERNS"):]
    assert "finding:loop" in section
    assert "UNRECOGNISED STATUS" not in out


def test_static_list_loop_supported_is_an_informational_pattern():
    graph = load_graph(FIXTURE)
    graph["findings"].append(_finding("STATIC_LIST_LOOP_SUPPORTED", "finding:list-loop"))

    out = render(graph)

    section = out[out.index("## SUPPORTED INFORMATIONAL PATTERNS"):]
    assert "finding:list-loop" in section
    assert "UNRECOGNISED STATUS" not in out


def test_supported_and_unresolved_findings_stay_in_their_sections():
    graph = load_graph(FIXTURE)
    graph["findings"].extend([
        _finding("STATIC_CONDITION_SUPPORTED", "finding:supported"),
        _finding("UNRESOLVED_MACRO_SOURCE", "finding:unresolved"),
    ])

    out = render(graph)

    assert "finding:supported" in out[out.index("## SUPPORTED INFORMATIONAL PATTERNS"):]
    unresolved = out[out.index("## UNRESOLVED_MACRO_SOURCE"):out.index("## CONTRACT_INCOMPLETE")]
    assert "finding:unresolved" in unresolved
    assert "UNRECOGNISED STATUS" not in out


def test_conditional_branch_unresolved_is_grouped_with_required_decisions():
    graph = load_graph(FIXTURE)
    graph["findings"].append(_finding("CONDITIONAL_BRANCH_UNRESOLVED", "finding:conditional"))

    out = render(graph)

    section = out[out.index("## REQUIRES_DECISION"):out.index("## UNRESOLVED_MACRO_VARIABLE")]
    assert "finding:conditional" in section
    assert "UNRECOGNISED STATUS" not in out


def test_unrecognised_status_is_surfaced_not_swallowed():
    graph = load_graph(FIXTURE)
    graph["findings"].append(
        {
            "id": "finding:999",
            "status": "WAT",
            "type": "invented",
            "severity": "WARNING",
            "object": "thing",
            "message": "status not in the plan",
            "source": None,
        }
    )

    out = render(graph)
    assert "## UNRECOGNISED STATUS" in out
    assert "finding:999" in out
    assert "_no source location recorded_" in out


def demo():
    test_header_carries_run_status()
    test_every_section_is_present_in_plan_order()
    test_empty_sections_say_none_rather_than_vanishing()
    test_unresolved_macro_source_finding_is_reported_with_evidence()
    test_affected_nodes_render_as_labels_not_raw_ids()
    test_static_condition_supported_is_an_informational_pattern()
    test_static_literal_loop_supported_is_an_informational_pattern()
    test_static_list_loop_supported_is_an_informational_pattern()
    test_supported_and_unresolved_findings_stay_in_their_sections()
    test_conditional_branch_unresolved_is_grouped_with_required_decisions()
    test_unrecognised_status_is_surfaced_not_swallowed()
    print(render(load_graph(FIXTURE)))
    print("findings renderer: all checks passed")


if __name__ == "__main__":
    demo()
