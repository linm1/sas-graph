"""DATA step rules (dev plan section 12, 21 Phase 4)."""

import conftest  # noqa: F401

from sas_graph import rules_data_step
from sas_graph.blocks import group_blocks
from sas_graph.graph_model import GraphContext
from sas_graph.macro_state import walk_let_statements
from sas_graph.statements import split_statements


def build(text, file_name="adae.sas"):
    result = split_statements(text, file_name)
    blocks, _ = group_blocks(result.statements)
    events = walk_let_statements(result.statements)
    ctx = GraphContext(main_programs=[file_name], setup_file="setup.sas", run_id="r1")
    return blocks, ctx, events


def test_basic_set_creates_reads_writes_and_depends_on():
    """Section 12.1's own example."""
    blocks, ctx, events = build("data work.ae1;\n  set sdtm.ae;\nrun;\n")
    rules_data_step.apply(blocks[0], ctx, events)

    edge_types = {(e["type"], e["from"], e["to"]) for e in ctx.edges}
    assert ("reads_dataset", "step:001", "dataset:sdtm.ae") in edge_types
    assert ("writes_dataset", "step:001", "dataset:work.ae1") in edge_types
    assert ("depends_on", "dataset:work.ae1", "dataset:sdtm.ae") in edge_types


def test_multiple_set_sources_each_get_a_reads_edge():
    blocks, ctx, events = build(
        "data work.adae_pre;\n  set sdtm.ae adam.adsl;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["to"] for e in ctx.edges if e["type"] == "reads_dataset"}
    assert reads == {"dataset:sdtm.ae", "dataset:adam.adsl"}


def test_multiple_set_and_merge_statements_keep_all_inputs_and_their_sources():
    blocks, ctx, events = build(
        "data work.out;\n"
        "  set sdtm.ae;\n"
        "  set adam.adsl;\n"
        "  merge work.suppae;\n"
        "  by usubjid;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_dataset"]
    assert {edge["to"] for edge in reads} == {
        "dataset:sdtm.ae",
        "dataset:adam.adsl",
        "dataset:work.suppae",
    }
    assert {
        (edge["to"], edge["source"]["statement_order"])
        for edge in reads
    } == {
        ("dataset:sdtm.ae", 2),
        ("dataset:adam.adsl", 3),
        ("dataset:work.suppae", 4),
    }
    assert {edge["to"] for edge in ctx.edges if edge["type"] == "depends_on"} == {
        "dataset:sdtm.ae",
        "dataset:adam.adsl",
        "dataset:work.suppae",
    }


def test_merge_by_creates_reads_for_each_input_and_records_by_vars():
    """Section 12.2's own example."""
    blocks, ctx, events = build(
        "data work.adae;\n  merge work.ae_srt work.adsl_srt;\n  by usubjid;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["to"] for e in ctx.edges if e["type"] == "reads_dataset"}
    assert reads == {"dataset:work.ae_srt", "dataset:work.adsl_srt"}
    depends = {e["to"] for e in ctx.edges if e["type"] == "depends_on"}
    assert depends == {"dataset:work.ae_srt", "dataset:work.adsl_srt"}

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert step["by_vars"] == [{"name": "usubjid", "direction": "ascending", "position": 1}]


def test_merge_by_prefix_of_prior_sort_is_supported_no_finding():
    """Section 12.3's supported example: sort_by = usubjid aeseq, merge_by = usubjid."""
    blocks, ctx, events = build(
        "data work.adae;\n  merge work.ae_srt work.b;\n  by usubjid;\nrun;\n"
    )
    ctx.sort_by_of["dataset:work.ae_srt"] = [
        {"name": "usubjid", "direction": "ascending", "position": 1},
        {"name": "aeseq", "direction": "ascending", "position": 2},
    ]
    rules_data_step.apply(blocks[0], ctx, events)

    assert all(f["type"] != "merge_by_not_prefix_of_sort_by" for f in ctx.findings)


def test_merge_by_not_prefix_of_prior_sort_is_flagged():
    """Section 12.3's counterexample: sort_by = aeseq usubjid, merge_by = usubjid."""
    blocks, ctx, events = build(
        "data work.adae;\n  merge work.ae_srt work.b;\n  by usubjid;\nrun;\n"
    )
    ctx.sort_by_of["dataset:work.ae_srt"] = [
        {"name": "aeseq", "direction": "ascending", "position": 1},
        {"name": "usubjid", "direction": "ascending", "position": 2},
    ]
    rules_data_step.apply(blocks[0], ctx, events)

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in ctx.findings)


def test_merge_with_no_prior_sort_evidence_is_not_flagged():
    """Section 12.2: do not prove input sort correctness without evidence."""
    blocks, ctx, events = build(
        "data work.adae;\n  merge work.a work.b;\n  by usubjid;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert all(f["type"] != "merge_by_not_prefix_of_sort_by" for f in ctx.findings)


def test_by_direction_normalization():
    """Section 12.4's own example."""
    blocks, ctx, events = build(
        "data work.a;\n  set work.b;\n  by usubjid descending astdt;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert step["by_vars"] == [
        {"name": "usubjid", "direction": "ascending", "position": 1},
        {"name": "astdt", "direction": "descending", "position": 2},
    ]


def test_in_place_overwrite_pattern_when_read_and_write_the_same_dataset():
    blocks, ctx, events = build("data work.a;\n  set work.a;\nrun;\n")
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert "IN_PLACE_OVERWRITE" in step["patterns"]


def test_multi_output_data_step_creates_writes_for_all_targets():
    """Section 12.6's own example."""
    blocks, ctx, events = build(
        "data work.sae work.nonsae;\n"
        '  set sdtm.ae;\n'
        '  if aeser = "Y" then output work.sae;\n'
        "  else output work.nonsae;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert writes == {"dataset:work.sae", "dataset:work.nonsae"}
    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert "MULTI_OUTPUT_DATA_STEP" in step["patterns"]
    assert step["branch_text"] == [
        'if aeser = "Y" then output work.sae;',
        "else output work.nonsae;",
    ]
    assert step["row_allocation_inferred"] is False
    assert all(f["type"] != "multi_output_ambiguous" for f in ctx.findings)


def test_unnamed_output_in_multi_output_step_is_ambiguous():
    """Section 12.7's own example."""
    blocks, ctx, events = build(
        "data work.sae work.nonsae;\n"
        '  set sdtm.ae;\n'
        '  if aeser = "Y" then output;\n'
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert "MULTI_OUTPUT_AMBIGUOUS" in next(n for n in ctx.nodes if n["type"] == "Step")["patterns"]
    assert any(f["type"] == "AMBIGUOUS_OUTPUT_TARGET" for f in ctx.findings)
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert writes == {"dataset:work.sae", "dataset:work.nonsae"}


def test_inline_comment_cannot_make_multi_output_ambiguous():
    blocks, ctx, events = build(
        "data work.sae work.nonsae;\n"
        "  set sdtm.ae;\n"
        "  if aeser = 'Y' then /* output; */ output work.sae;\n"
        "  else output work.nonsae;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(node for node in ctx.nodes if node["type"] == "Step")
    assert "MULTI_OUTPUT_AMBIGUOUS" not in step["patterns"]
    assert not any(finding["type"] == "AMBIGUOUS_OUTPUT_TARGET" for finding in ctx.findings)
    assert step["branch_text"] == [
        "if aeser = 'Y' then /* output; */ output work.sae;",
        "else output work.nonsae;",
    ]


def test_output_identifier_is_not_an_output_statement():
    blocks, ctx, events = build(
        "data work.sae work.nonsae;\n"
        "  set sdtm.ae;\n"
        "  output_flag = 1;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(node for node in ctx.nodes if node["type"] == "Step")
    assert step["branch_text"] == []
    assert "MULTI_OUTPUT_AMBIGUOUS" not in step["patterns"]


def test_put_output_text_or_value_is_not_an_output_statement():
    blocks, ctx, events = build(
        "data work.sae work.nonsae;\n"
        "  set sdtm.ae;\n"
        '  put "output ;";\n'
        "  put output;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(node for node in ctx.nodes if node["type"] == "Step")
    assert step["branch_text"] == []
    assert "MULTI_OUTPUT_AMBIGUOUS" not in step["patterns"]
    assert not any(finding["type"] == "AMBIGUOUS_OUTPUT_TARGET" for finding in ctx.findings)


def test_data_null_creates_step_with_no_output_dataset():
    """Section 12.8's own example."""
    blocks, ctx, events = build(
        "data _null_;\n  set sdtm.ae;\n  call symputx('n', 1);\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert all(e["type"] != "writes_dataset" for e in ctx.edges)
    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert "DATA_NULL_STEP" in step["patterns"]
    assert "RUNTIME_MACRO_VARIABLE_CREATION" in step["patterns"]
    assert step["usable_for_static_resolution"] is False


def test_where_is_captured_as_row_filter_metadata():
    """Section 12.9's own example shape."""
    blocks, ctx, events = build(
        'data work.a;\n  set sdtm.ae;\n  where aeser = "Y";\nrun;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert "ROW_FILTER" in step["patterns"]
    assert step["condition_text"] == 'aeser = "Y"'
    assert step["clinical_meaning_interpreted"] is False
    # dependency stays active despite the filter
    assert any(e["type"] == "reads_dataset" for e in ctx.edges)


def test_keep_drop_rename_captured_as_shape_metadata():
    """Section 12.10's own example."""
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  keep usubjid aeseq aeterm aeser;\n"
        "  rename aeterm=term;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert "VARIABLE_SHAPE_CHANGE" in step["patterns"]
    assert step["keep_vars"] == ["usubjid", "aeseq", "aeterm", "aeser"]
    assert step["rename_map"] == {"aeterm": "term"}


def test_dataset_level_where_option_is_not_read_as_a_second_dataset():
    """Regression: `set sdtm.ae (where=(aeser="Y"));` must resolve to exactly
    one dataset, not treat the option group as a sibling dataset name."""
    blocks, ctx, events = build(
        'data work.a;\n  set sdtm.ae (where=(aeser="Y"));\nrun;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["to"] for e in ctx.edges if e["type"] == "reads_dataset"}
    assert reads == {"dataset:sdtm.ae"}


def test_where_after_multiple_statements_does_not_leak_into_next_statement():
    """Regression: a joined-text WHERE scan with a greedy match could span
    past its own statement boundary into an unrelated one."""
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        '  where aeser = "Y";\n'
        "  drop unrelated_var;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(n for n in ctx.nodes if n["type"] == "Step")
    assert step["where_condition"] == 'aeser = "Y"'
    assert step["drop_vars"] == ["unrelated_var"]


def test_unresolved_macro_variable_in_set_creates_unknown_dataset():
    """Section 15.6's posture applied to a DATA step SET source."""
    blocks, ctx, events = build("data work.a;\n  set sdtm.&domain.;\nrun;\n")
    rules_data_step.apply(blocks[0], ctx, events)

    unknown = [n for n in ctx.nodes if n["type"] == "UnknownDataset"]
    assert len(unknown) == 1
    assert any(f["status"] == "UNRESOLVED_MACRO_VARIABLE" for f in ctx.findings)


def test_unresolved_macro_variable_in_data_target_creates_unknown_dataset():
    """Regression: the DATA statement's own output target must resolve
    through the same UnknownDataset/finding path as SET/MERGE, not bake an
    unresolved `&missing.` reference silently into a Dataset node id."""
    blocks, ctx, events = build("data work.&missing._out;\n  set sdtm.ae;\nrun;\n")
    rules_data_step.apply(blocks[0], ctx, events)

    unknown = [n for n in ctx.nodes if n["type"] == "UnknownDataset"]
    assert len(unknown) == 1
    assert any(f["status"] == "UNRESOLVED_MACRO_VARIABLE" for f in ctx.findings)
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert writes == {unknown[0]["id"]}


def test_resolved_macro_variable_in_data_target_produces_a_real_dataset():
    """The happy-path counterpart: a %let defined earlier resolves cleanly."""
    blocks, ctx, events = build(
        "%let domain = ae;\ndata work.&domain._out;\n  set sdtm.ae;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert writes == {"dataset:work.ae_out"}
    assert not any(n["type"] == "UnknownDataset" for n in ctx.nodes)


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("data step rules: all checks passed")


if __name__ == "__main__":
    demo()
