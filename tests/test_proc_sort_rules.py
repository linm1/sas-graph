"""PROC SORT rules (dev plan section 13, 21 Phase 4)."""

import conftest  # noqa: F401

from sas_graph import rules_data_step, rules_proc_sort
from sas_graph.blocks import group_blocks
from sas_graph.graph_model import GraphContext
from sas_graph.macro_state import walk_let_statements
from sas_graph.statements import split_statements


def build(text, file_name="adae.sas"):
    result = split_statements(text, file_name)
    blocks, _ = group_blocks(result.statements)
    events = walk_let_statements(result.statements)
    ctx = GraphContext(main_program=file_name, setup_file="setup.sas", run_id="r1")
    return blocks, ctx, events


def test_basic_sort_creates_reads_writes_depends_on_and_by_vars():
    """Section 13.1's own example."""
    blocks, ctx, events = build(
        "proc sort data=work.ae out=work.ae_srt;\n  by usubjid aeseq;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    edge_types = {(e["type"], e["from"], e["to"]) for e in ctx.edges}
    assert ("reads_dataset", "step:001", "dataset:work.ae") in edge_types
    assert ("writes_dataset", "step:001", "dataset:work.ae_srt") in edge_types
    assert ("depends_on", "dataset:work.ae_srt", "dataset:work.ae") in edge_types

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert step["step_kind"] == "PROC_SORT"
    assert step["by_vars"] == [
        {"name": "usubjid", "direction": "ascending", "position": 1},
        {"name": "aeseq", "direction": "ascending", "position": 2},
    ]


def test_sort_records_sort_by_of_keyed_by_output_dataset_id():
    """The exact seam rules_data_step._check_merge_sort_prefix reads."""
    blocks, ctx, events = build(
        "proc sort data=work.ae out=work.ae_srt;\n  by usubjid aeseq;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    assert ctx.sort_by_of["dataset:work.ae_srt"] == [
        {"name": "usubjid", "direction": "ascending", "position": 1},
        {"name": "aeseq", "direction": "ascending", "position": 2},
    ]


def test_explicit_in_place_sort_is_flagged():
    """Section 13.2's own example."""
    blocks, ctx, events = build(
        "proc sort data=work.ae1 out=work.ae1;\n  by usubjid aeseq;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert "IN_PLACE_SORT_OVERWRITE" in step["patterns"]
    assert step["severity"] == "INFORMATION"
    assert all(e["type"] != "depends_on" for e in ctx.edges)


def test_implicit_in_place_sort_without_out_is_flagged():
    """Section 13.3's own example."""
    blocks, ctx, events = build(
        "proc sort data=work.ae1;\n  by usubjid aeseq;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert "IMPLICIT_IN_PLACE_SORT_OVERWRITE" in step["patterns"]
    assert step["severity"] == "INFORMATION"
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert writes == {"dataset:work.ae1"}


def test_nodupkey_is_captured_as_row_filtering_sort():
    """Section 13.4's own example."""
    blocks, ctx, events = build(
        "proc sort data=work.ae out=work.ae_uniq nodupkey;\n"
        "  by usubjid aeterm;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert "ROW_FILTERING_SORT" in step["patterns"]
    assert step["sort_option"] == "NODUPKEY"
    assert step["cardinality_effect"] == "possible_row_reduction"
    assert step["data_content_checked"] is False
    assert step["severity"] == "INFORMATION"
    assert any(f["type"] == "row_filtering_sort" for f in ctx.findings)


def test_sort_then_merge_prefix_check_needs_no_hand_seeding():
    """Integration proof of the cross-module seam: PROC SORT writes
    ctx.sort_by_of in the exact shape/key rules_data_step reads, with no test
    manually seeding the map -- section 12.3's supported example run through
    both real rule modules in statement order."""
    text = (
        "proc sort data=work.ae out=work.ae_srt;\n  by usubjid aeseq;\nrun;\n"
        "data work.adae;\n  merge work.ae_srt work.adsl;\n  by usubjid;\nrun;\n"
    )
    blocks, ctx, events = build(text)
    sort_block, data_block = blocks

    rules_proc_sort.apply(sort_block, ctx, events)
    rules_data_step.apply(data_block, ctx, events)

    assert all(f["type"] != "merge_by_not_prefix_of_sort_by" for f in ctx.findings)


def test_sort_then_merge_prefix_mismatch_is_caught_end_to_end():
    """Same seam, the counterexample direction (section 12.3)."""
    text = (
        "proc sort data=work.ae out=work.ae_srt;\n  by aeseq usubjid;\nrun;\n"
        "data work.adae;\n  merge work.ae_srt work.adsl;\n  by usubjid;\nrun;\n"
    )
    blocks, ctx, events = build(text)
    sort_block, data_block = blocks

    rules_proc_sort.apply(sort_block, ctx, events)
    rules_data_step.apply(data_block, ctx, events)

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in ctx.findings)


def test_unresolved_macro_variable_in_data_or_out_creates_unknown_dataset():
    """Regression: DATA=/OUT= must resolve through the same UnknownDataset/
    finding path as every other dataset-name token, not bake an unresolved
    `&missing.` reference silently into a Dataset node id."""
    blocks, ctx, events = build(
        "proc sort data=work.&missing. out=work.a_srt;\n  by usubjid;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    unknown = [n for n in ctx.nodes if n["type"] == "UnknownDataset"]
    assert len(unknown) == 1
    assert any(f["status"] == "UNRESOLVED_MACRO_VARIABLE" for f in ctx.findings)
    reads = {e["to"] for e in ctx.edges if e["type"] == "reads_dataset"}
    assert reads == {unknown[0]["id"]}


def test_dupout_creates_writes_dataset_and_depends_on():
    """B4: `dupout=` names a second output of PROC SORT, same as `out=`."""
    blocks, ctx, events = build(
        "proc sort data=work.ae out=work.ae_uniq dupout=work.ae_dups nodupkey;\n"
        "  by usubjid;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    edge_types = {(e["type"], e["from"], e["to"]) for e in ctx.edges}
    assert ("writes_dataset", "step:001", "dataset:work.ae_dups") in edge_types
    assert ("depends_on", "dataset:work.ae_dups", "dataset:work.ae") in edge_types


def test_dupout_without_out_still_creates_writes_dataset():
    """qc_adae.sas:995's shape: `dupout=` present with no `out=`."""
    blocks, ctx, events = build(
        "proc sort data=work.adae dupout=work.adae_dups;\n  by usubjid;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert "dataset:work.adae_dups" in writes


def test_dupout_nodupkey_carries_by_vars_sort_evidence():
    """codex #6: DUPOUT under NODUPKEY holds the rejected duplicates in the
    same sorted order, so a later MERGE reading it should get the same
    merge-by-prefix evidence `out=` already provides."""
    blocks, ctx, events = build(
        "proc sort data=work.ae out=work.ae_uniq dupout=work.ae_dups nodupkey;\n"
        "  by usubjid;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    expected_by_vars = [{"name": "usubjid", "direction": "ascending", "position": 1}]
    assert ctx.sort_by_of["dataset:work.ae_dups"] == expected_by_vars
    assert ctx.sort_by_at["dataset:work.ae_dups"][-1][1] == expected_by_vars


def test_dupout_without_nodupkey_carries_no_sort_evidence():
    """Without NODUPKEY/NODUPRECS, `dupout=` writes nothing at runtime -- no
    sort-order claim should be recorded for a dataset the sort never wrote."""
    blocks, ctx, events = build(
        "proc sort data=work.adae out=work.adae_srt dupout=work.adae_dups;\n"
        "  by usubjid;\nrun;\n"
    )
    rules_proc_sort.apply(blocks[0], ctx, events)

    assert "dataset:work.adae_dups" not in ctx.sort_by_of


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("proc sort rules: all checks passed")


if __name__ == "__main__":
    demo()
