"""GraphContext: the only thing that builds node/edge/finding ids and the
graph.json envelope (dev plan section 4.1's contract, frozen in
wayfinder/tickets/freeze-minimal-graph-contract.md)."""

import conftest  # noqa: F401

from sas_graph.graph_model import GraphContext, normalize_dataset


def make_context():
    return GraphContext(main_programs=["adae.sas"], setup_file="setup.sas", run_id="run-0001")


def test_normalize_dataset_splits_libref_and_member():
    assert normalize_dataset("sdtm.AE", {}) == ("sdtm", "ae")


def test_normalize_dataset_bare_name_defaults_to_work():
    assert normalize_dataset("adae1", {}) == ("work", "adae1")


def test_add_dataset_returns_stable_id_and_dedupes():
    ctx = make_context()
    first = ctx.add_dataset("sdtm.ae")
    second = ctx.add_dataset("sdtm.ae")

    assert first == second == "dataset:sdtm.ae"
    assert len([n for n in ctx.nodes if n["id"] == "dataset:sdtm.ae"]) == 1


def test_add_dataset_node_has_null_source():
    ctx = make_context()
    ctx.add_dataset("sdtm.ae")

    node = next(n for n in ctx.nodes if n["id"] == "dataset:sdtm.ae")
    assert node["type"] == "Dataset"
    assert node["source"] is None
    assert node["libref"] == "sdtm"
    assert node["member"] == "ae"


def test_add_unknown_dataset_carries_its_source():
    ctx = make_context()
    source = {"file": "a.sas", "line_start": 1, "line_end": 1, "statement_order": 1,
              "original_text": "x", "rule": "r"}
    node_id = ctx.add_unknown_dataset("&unknown_out.", source)

    node = next(n for n in ctx.nodes if n["id"] == node_id)
    assert node["type"] == "UnknownDataset"
    assert node["source"] == source


def test_step_ids_increment_and_are_zero_padded():
    ctx = make_context()
    assert ctx.next_step_id() == "step:001"
    assert ctx.next_step_id() == "step:002"


def test_edge_ids_increment_and_edge_carries_from_to_type():
    ctx = make_context()
    edge_id = ctx.add_edge("reads_dataset", "step:001", "dataset:sdtm.ae", source=None)

    assert edge_id == "edge:001"
    edge = ctx.edges[0]
    assert edge["type"] == "reads_dataset"
    assert edge["from"] == "step:001"
    assert edge["to"] == "dataset:sdtm.ae"


def test_finding_ids_are_unique_across_calls():
    ctx = make_context()
    first = ctx.add_finding(
        "macro_source_not_found", "UNRESOLVED_MACRO_SOURCE", "WARNING", "gm_missing",
        "msg", "action", source=None,
    )
    second = ctx.add_finding(
        "macro_source_not_found", "UNRESOLVED_MACRO_SOURCE", "WARNING", "gm_other",
        "msg", "action", source=None,
    )
    assert first != second


def test_run_status_complete_with_no_findings():
    ctx = make_context()
    assert ctx.run_status() == "COMPLETE"


def test_supported_informational_findings_leave_the_run_complete_but_unresolved_findings_do_not():
    ctx = make_context()
    ctx.add_finding(
        "macro_contract_applied", "SUPPORTED", "INFORMATION", "gm_derive",
        "msg", None, source=None,
    )
    assert ctx.run_status() == "COMPLETE"

    ctx.add_finding(
        "macro_source_not_found", "UNRESOLVED_MACRO_SOURCE", "WARNING", "x",
        "msg", None, source=None,
    )
    assert ctx.run_status() == "PARTIAL"


def test_to_graph_matches_the_frozen_envelope_shape():
    ctx = make_context()
    ctx.add_dataset("sdtm.ae")
    graph = ctx.to_graph()

    assert graph["schema_version"] == "0.2.0"
    assert graph["run_id"] == "run-0001"
    assert graph["main_programs"] == ["adae.sas"]
    assert graph["setup_file"] == "setup.sas"
    assert graph["run_status"] == "COMPLETE"
    assert isinstance(graph["nodes"], list)
    assert isinstance(graph["edges"], list)
    assert isinstance(graph["findings"], list)


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("graph_model: all checks passed")


if __name__ == "__main__":
    demo()
