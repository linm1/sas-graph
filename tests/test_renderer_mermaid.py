"""Mermaid renderer, exercised against the hand-written contract fixture."""

from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.graph_io import load_graph
from sas_graph.renderer_mermaid import mermaid_id, render

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "hand_written_graph.json"


def test_mermaid_id_sanitises_dots_and_ampersands():
    assert mermaid_id("dataset:sdtm.ae") == "dataset_sdtm_ae"
    assert mermaid_id("unknowndataset:&unknown_out.") == "unknowndataset__unknown_out_"


def test_renders_flow_header_and_dataset_nodes():
    out = render(load_graph(FIXTURE))

    assert out.startswith("flowchart LR\n")
    assert '  dataset_sdtm_ae["sdtm.ae"]' in out
    assert '  step_001("DATA step 001")' in out
    assert '  macrocall_003{{"%gm_derive"}}' in out


def test_reads_dataset_edge_is_flipped_to_read_left_to_right():
    out = render(load_graph(FIXTURE))

    # graph stores step -> dataset; the diagram must show dataset -> step
    assert "  dataset_sdtm_ae --> step_001" in out
    assert "  step_001 --> dataset_sdtm_ae" not in out
    assert "  step_001 --> dataset_work_adae_pre" in out


def test_contract_derived_edges_use_a_dotted_arrow():
    out = render(load_graph(FIXTURE))

    assert "  dataset_work_adae_srt -.-> macrocall_003" in out
    assert "  macrocall_003 -.-> dataset_adam_adae" in out


def test_depends_on_edges_are_not_drawn():
    graph = load_graph(FIXTURE)
    out = render(graph)

    assert any(edge["type"] == "depends_on" for edge in graph["edges"])
    # work.adae_pre depends_on sdtm.ae would be a second, redundant arrow
    assert "  dataset_work_adae_pre --> dataset_sdtm_ae" not in out


def test_detail_nodes_are_excluded_from_the_flow_view():
    out = render(load_graph(FIXTURE))

    for excluded in ("macroparam_", "macrovar_", "library_", "program_"):
        assert excluded not in out


def test_unknown_nodes_are_styled():
    out = render(load_graph(FIXTURE))

    assert "classDef unknown" in out
    assert "  class unknowndataset__unknown_out_ unknown;" in out
    assert "  class unknownmacro_gm_missing unknown;" in out


def demo():
    test_mermaid_id_sanitises_dots_and_ampersands()
    test_renders_flow_header_and_dataset_nodes()
    test_reads_dataset_edge_is_flipped_to_read_left_to_right()
    test_contract_derived_edges_use_a_dotted_arrow()
    test_depends_on_edges_are_not_drawn()
    test_detail_nodes_are_excluded_from_the_flow_view()
    test_unknown_nodes_are_styled()
    print(render(load_graph(FIXTURE)))
    print("mermaid renderer: all checks passed")


if __name__ == "__main__":
    demo()
