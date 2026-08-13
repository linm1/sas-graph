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
    assert ("reads_dataset", "dataset:sdtm.ae", "step:001") in edge_types
    assert ("writes_dataset", "step:001", "dataset:work.ae1") in edge_types
    assert ("depends_on", "dataset:work.ae1", "dataset:sdtm.ae") in edge_types


def test_multiple_set_sources_each_get_a_reads_edge():
    blocks, ctx, events = build(
        "data work.adae_pre;\n  set sdtm.ae adam.adsl;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_dataset"}
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
    assert {edge["from"] for edge in reads} == {
        "dataset:sdtm.ae",
        "dataset:adam.adsl",
        "dataset:work.suppae",
    }
    assert {
        (edge["from"], edge["source"]["statement_order"])
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

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_dataset"}
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
    assert ("reads_dataset", "dataset:sdtm.ae", "step:001") in {
        (edge["type"], edge["from"], edge["to"]) for edge in ctx.edges
    }
    assert all(e["type"] != "depends_on" for e in ctx.edges)
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

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_dataset"}
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


def test_assignment_writes_target_and_reads_rhs_named_variable():
    """User story 1: a plain assignment writes its target and reads any
    named RHS variable, each its own edge."""
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  anl01vs = anl01fl;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    variable_ids = {n["id"] for n in ctx.nodes if n["type"] == "Variable"}
    assert {"variable:adam.adlb.anl01vs", "variable:adam.adlb.anl01fl"} <= variable_ids

    write = next(e for e in ctx.edges if e["type"] == "writes_variable")
    assert write["from"] == "step:001"
    assert write["to"] == "variable:adam.adlb.anl01vs"
    assert write["value"] is None
    assert write["operator"] is None
    assert write["source"]["rule"] == "data_step_assignment"

    read = next(e for e in ctx.edges if e["type"] == "reads_variable")
    assert read["from"] == "variable:adam.adlb.anl01fl"
    assert read["to"] == "step:001"
    assert read["value"] is None
    assert read["operator"] is None


def test_literal_assignment_captures_value_with_null_operator():
    """Frozen contract: a plain assignment has a `value` but no `operator`."""
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  anl01fl = 'Y';\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    write = next(e for e in ctx.edges if e["type"] == "writes_variable")
    assert write["value"] == "Y"
    assert write["operator"] is None
    assert not any(e["type"] == "reads_variable" for e in ctx.edges)


def test_date_literal_assignment_captures_value_with_no_stray_identifier():
    """codex-review-class fix (found while designing the evidence fixture,
    same masking hazard as the SQL name-literal finding): the trailing `d`
    in a SAS date literal must not survive as a stray bare identifier."""
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  imputed_dt = '01JAN2020'd;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    write = next(e for e in ctx.edges if e["type"] == "writes_variable")
    assert write["value"] == "01JAN2020"
    assert not any(e["type"] == "reads_variable" for e in ctx.edges)
    assert not any(
        n["type"] in ("Variable", "UnknownVariable") and n["id"].endswith(".d")
        for n in ctx.nodes
    )


def test_computed_function_call_assignment_produces_no_read_and_null_value():
    """The frozen contract's own writes_variable example: `anl01vs =
    compute_flag();` is computed -- both value and operator stay null, and
    the function name itself is never read as a variable."""
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  anl01vs = compute_flag();\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(e["type"] == "reads_variable" for e in ctx.edges)
    write = next(e for e in ctx.edges if e["type"] == "writes_variable")
    assert write["to"] == "variable:adam.adlb.anl01vs"
    assert write["value"] is None
    assert write["operator"] is None
    assert not any(
        n["type"] in ("Variable", "UnknownVariable") and "compute_flag" in n["id"]
        for n in ctx.nodes
    )


def test_computed_expression_with_quoted_literal_argument_reads_only_real_identifiers():
    """codex-review fix: a quoted string literal inside a function call must
    never be misread as a bare variable name (`Y`/`yes`/`no` from
    `ifc(test='Y', 'yes', 'no')`)."""
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  flag = ifc(test = 'Y', 'yes', 'no');\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    assert reads == {"variable:adam.adlb.test"}
    assert not any(
        n["type"] in ("Variable", "UnknownVariable")
        and n["id"].rsplit(".", 1)[-1] in ("y", "yes", "no")
        for n in ctx.nodes
    )


def test_format_and_informat_names_are_not_read_as_variables():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  x = input(y, best32.);\n"
        "  z = put(y, yymmdd10.);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert reads == {"variable:work.a.y"}
    assert writes == {"variable:work.a.x", "variable:work.a.z"}
    assert not any(
        n["id"].rsplit(".", 1)[-1] in {"best32", "yymmdd10"}
        for n in ctx.nodes
        if n["type"] in ("Variable", "UnknownVariable")
    )


def test_literal_suffixes_and_special_missing_are_not_read_as_variables():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  date_expr = '01JAN2020:12:30'dt + delta;\n"
        "  hexchar = '534153'x;\n"
        "  special_missing = .A;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert reads == {"variable:work.a.delta"}
    assert writes == {
        "variable:work.a.date_expr",
        "variable:work.a.hexchar",
        "variable:work.a.special_missing",
    }
    assert not any(
        n["id"].rsplit(".", 1)[-1] in {"dt", "x", "a"}
        for n in ctx.nodes
        if n["type"] in ("Variable", "UnknownVariable")
    )


def test_name_literal_rhs_binds_as_one_variable_identifier():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  name_ref = 'dose value'n;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    assert [e["from"] for e in reads] == ["variable:work.a.'dose value'n"]
    assert any(
        e["type"] == "writes_variable" and e["to"] == "variable:work.a.name_ref"
        for e in ctx.edges
    )
    assert not any(n["id"] == "variable:work.a.n" for n in ctx.nodes)


def test_function_argument_logical_not_is_not_read_as_a_variable():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  x = ifc(not missing(y), a, b);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    assert reads == {"variable:work.a.y", "variable:work.a.a", "variable:work.a.b"}
    assert not any(n["id"] == "variable:work.a.not" for n in ctx.nodes)


def test_function_argument_logical_and_comparison_words_are_not_variables():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  result = ifc(x and y or z eq aa or bb ne cc or dd gt ee "
        "or ff lt gg or hh ge ii or jj le kk, yes_var, no_var);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    assert reads == {
        "variable:work.a.x", "variable:work.a.y", "variable:work.a.z",
        "variable:work.a.aa", "variable:work.a.bb", "variable:work.a.cc",
        "variable:work.a.dd", "variable:work.a.ee", "variable:work.a.ff",
        "variable:work.a.gg", "variable:work.a.hh", "variable:work.a.ii",
        "variable:work.a.jj", "variable:work.a.kk",
        "variable:work.a.yes_var", "variable:work.a.no_var",
    }
    assert not any(
        n["id"].rsplit(".", 1)[-1] in {
            "and", "or", "eq", "ne", "gt", "lt", "ge", "le",
        }
        for n in ctx.nodes
        if n["type"] in ("Variable", "UnknownVariable")
    )


def test_automatic_variables_are_not_read_and_assignment_writes_remain():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  obsno = _n_;\n"
        "  errcopy = _error_;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(e["type"] == "reads_variable" for e in ctx.edges)
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert writes == {"variable:work.a.obsno", "variable:work.a.errcopy"}
    assert not any(
        n["id"] in {"variable:work.a._n_", "variable:work.a._error_"}
        for n in ctx.nodes
    )


def test_of_numbered_range_reads_every_member_and_not_the_of_marker():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  total = sum(of x1-x3);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    assert reads == {
        "variable:work.a.x1", "variable:work.a.x2", "variable:work.a.x3"
    }
    assert not any(n["id"] == "variable:work.a.of" for n in ctx.nodes)


def test_if_condition_variable_to_variable_comparison_carries_operator_on_both_sides():
    """codex-review fix: the frozen contract says `operator` is null "when
    there is no comparison" -- `if left = right` is a comparison even though
    neither side is a literal, so both reads_variable edges carry `=`."""
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  if left = right then flag = 1;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        e["from"]: e for e in ctx.edges
        if e["type"] == "reads_variable" and e["to"] == "step:001"
        and e["from"] in ("variable:adam.adlb.left", "variable:adam.adlb.right")
    }
    assert reads["variable:adam.adlb.left"]["operator"] == "="
    assert reads["variable:adam.adlb.left"]["value"] is None
    assert reads["variable:adam.adlb.right"]["operator"] == "="
    assert reads["variable:adam.adlb.right"]["value"] is None


def test_if_condition_literal_captures_value_and_operator():
    """The frozen contract's own reads_variable example."""
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  if anl01fl = 'Y' then flag = 1;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    read = next(
        e for e in ctx.edges
        if e["type"] == "reads_variable" and e["from"] == "variable:adam.adlb.anl01fl"
    )
    assert read["to"] == "step:001"
    assert read["value"] == "Y"
    assert read["operator"] == "="
    assert read["source"]["rule"] == "data_step_if_condition"


def test_if_compat_greater_equal_spelling_maps_to_canonical_operator():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if x => 1 then y = 'Y';\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.x", "1", ">=", "data_step_if_condition"),
    }
    assert writes == {
        ("variable:work.a.y", "Y", None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_if_compat_less_equal_spelling_maps_to_canonical_operator():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if x =< 1 then y = 'Y';\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.x", "1", "<=", "data_step_if_condition"),
    }
    assert writes == {
        ("variable:work.a.y", "Y", None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_canonical_and_mnemonic_comparison_operators_remain_unchanged():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if x_ge >= 1 then y_ge = 'Y';\n"
        "  if x_le <= 1 then y_le = 'Y';\n"
        "  if x_ge_word ge 1 then y_ge_word = 'Y';\n"
        "  if x_le_word le 1 then y_le_word = 'Y';\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.x_ge", "1", ">=", "data_step_if_condition"),
        ("variable:work.a.x_le", "1", "<=", "data_step_if_condition"),
        ("variable:work.a.x_ge_word", "1", "GE", "data_step_if_condition"),
        ("variable:work.a.x_le_word", "1", "LE", "data_step_if_condition"),
    }
    assert writes == {
        ("variable:work.a.y_ge", "Y", None, "data_step_assignment"),
        ("variable:work.a.y_le", "Y", None, "data_step_assignment"),
        ("variable:work.a.y_ge_word", "Y", None, "data_step_assignment"),
        ("variable:work.a.y_le_word", "Y", None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_if_then_assignment_emits_condition_and_action_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if aetrtem = 'Y' then trtemfl = 'Y';\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.aetrtem", "Y", "=", "data_step_if_condition"),
    }
    assert writes == {
        ("variable:work.a.trtemfl", "Y", None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_if_then_computed_assignment_keeps_condition_and_rhs_reads():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if x >= y then z = x - y + 1;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.x", None, ">=", "data_step_if_condition"),
        ("variable:work.a.y", None, ">=", "data_step_if_condition"),
        ("variable:work.a.x", None, None, "data_step_assignment"),
        ("variable:work.a.y", None, None, "data_step_assignment"),
    }
    assert writes == {
        ("variable:work.a.z", None, None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_standalone_else_assignment_emits_the_same_assignment_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if condition = 'Y' then target = 'Y';\n"
        "  else target = 'N';\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.condition", "Y", "=", "data_step_if_condition"),
    }
    assert writes == {
        ("variable:work.a.target", "Y", None, "data_step_assignment"),
        ("variable:work.a.target", "N", None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_if_and_else_unsupported_actions_keep_deferred_behavior():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if condition = 'Y' then do;\n"
        "  end;\n"
        "  else do;\n"
        "  end;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = [
        (finding["type"], finding["status"], finding["source"]["rule"])
        for finding in ctx.findings
    ]
    assert reads == {
        ("variable:work.a.condition", "Y", "=", "data_step_if_condition"),
    }
    assert writes == set()
    assert findings == [
        ("deferred_variable_construct", "NOT_EXECUTED", "data_step_deferred_variable_construct"),
        ("deferred_variable_construct", "NOT_EXECUTED", "data_step_deferred_variable_construct"),
    ]


def test_call_missing_writes_one_variable_with_null_value_and_operator():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  call missing(trtemfl);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == set()
    assert writes == {
        ("variable:work.a.trtemfl", None, None, "data_step_call_missing"),
    }
    assert ctx.findings == []


def test_call_missing_writes_every_comma_separated_argument():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  call missing(a, b, c);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == set()
    assert writes == {
        (f"variable:work.a.{name}", None, None, "data_step_call_missing")
        for name in ("a", "b", "c")
    }
    assert ctx.findings == []


def test_call_missing_dynamic_argument_list_defers_without_variable_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  call missing(of a1-a10);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = [
        (finding["type"], finding["status"], finding["source"]["rule"])
        for finding in ctx.findings
    ]
    assert reads == set()
    assert writes == set()
    assert findings == [
        ("deferred_variable_construct", "NOT_EXECUTED", "data_step_deferred_variable_construct"),
    ]


def test_else_call_missing_reuses_else_dispatch_and_writes_argument():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if condition = 'Y' then target = 'Y';\n"
        "  else call missing(x);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.condition", "Y", "=", "data_step_if_condition"),
    }
    assert writes == {
        ("variable:work.a.target", "Y", None, "data_step_assignment"),
        ("variable:work.a.x", None, None, "data_step_call_missing"),
    }
    assert ctx.findings == []


def test_unsupported_call_routine_defers_without_variable_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  call other_routine(x);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == set()
    assert writes == set()
    assert [
        (finding["type"], finding["status"], finding["source"]["rule"])
        for finding in ctx.findings
    ] == [
        ("deferred_variable_construct", "NOT_EXECUTED", "data_step_deferred_variable_construct"),
    ]


def test_select_when_otherwise_reads_expression_and_dispatches_actions():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  select (status);\n"
        "    when ('Y') flag=1;\n"
        "    when ('N') flag=0;\n"
        "    otherwise flag=.;\n"
        "  end;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.status", None, None, "data_step_select_expression"),
    }
    assert writes == {
        ("variable:work.a.flag", "1", None, "data_step_assignment"),
        ("variable:work.a.flag", "0", None, "data_step_assignment"),
        ("variable:work.a.flag", None, None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_select_do_branch_does_not_drop_later_when_or_otherwise_actions():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  select (status);\n"
        "    when ('Y') do;\n"
        "      x=1;\n"
        "      y=2;\n"
        "    end;\n"
        "    when ('N') flag=0;\n"
        "    otherwise flag=.;\n"
        "  end;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = {
        (f["type"], f["status"], f["object"], f["source"]["rule"])
        for f in ctx.findings
    }
    assert reads == {
        ("variable:work.a.status", None, None, "data_step_select_expression"),
    }
    assert writes == {
        ("variable:work.a.flag", "0", None, "data_step_assignment"),
        ("variable:work.a.flag", None, None, "data_step_assignment"),
    }
    assert findings == {
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "when ('Y') do;",
            "data_step_deferred_variable_construct",
        ),
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "x=1;",
            "data_step_deferred_variable_construct",
        ),
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "y=2;",
            "data_step_deferred_variable_construct",
        ),
    }


def test_select_unsupported_when_shape_defers_without_branch_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  select (status);\n"
        "    when ('Y', 'N') flag=1;\n"
        "  end;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = {
        (f["type"], f["status"], f["object"], f["source"]["rule"])
        for f in ctx.findings
    }
    assert reads == {("variable:work.a.status", None, None)}
    assert writes == set()
    assert findings == {
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "when ('Y', 'N') flag=1;",
            "data_step_deferred_variable_construct",
        ),
    }


def test_supported_call_routines_read_only_variable_arguments():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  call symput('old', value);\n"
        "  call symputx('n', count);\n"
        "  call execute(cats('x=', id));\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.value", None, None, "data_step_call_symput"),
        ("variable:work.a.count", None, None, "data_step_call_symputx"),
        ("variable:work.a.id", None, None, "data_step_call_execute"),
    }
    assert writes == set()
    assert ctx.findings == []


def test_unsupported_call_argument_shape_defers_without_variable_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  call symputx(name, count);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = {
        (f["type"], f["status"], f["object"], f["source"]["rule"])
        for f in ctx.findings
    }
    assert reads == set()
    assert writes == set()
    assert findings == {
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "call symputx(name, count);",
            "data_step_deferred_variable_construct",
        ),
    }


def test_sum_statement_reads_accumulator_and_rhs_and_writes_accumulator():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  total + amount;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.total", None, None, "data_step_sum_statement"),
        ("variable:work.a.amount", None, None, "data_step_sum_statement"),
    }
    assert writes == {
        ("variable:work.a.total", None, None, "data_step_sum_statement"),
    }
    assert ctx.findings == []


def test_malformed_sum_statement_defers_without_variable_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  total + amount +;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = {
        (f["type"], f["status"], f["object"], f["source"]["rule"])
        for f in ctx.findings
    }
    assert reads == set()
    assert writes == set()
    assert findings == {
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "total + amount +;",
            "data_step_deferred_variable_construct",
        ),
    }


def test_statement_input_writes_and_put_reads_variables():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  input id $ value;\n"
        "  put id= value=;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.id", None, None, "data_step_put"),
        ("variable:work.a.value", None, None, "data_step_put"),
    }
    assert writes == {
        ("variable:work.a.id", None, None, "data_step_input"),
        ("variable:work.a.value", None, None, "data_step_input"),
    }
    assert ctx.findings == []


def test_input_and_put_variable_names_stay_normal_assignments():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  input = 5;\n"
        "  put = 6;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == set()
    assert writes == {
        ("variable:work.a.input", "5", None, "data_step_assignment"),
        ("variable:work.a.put", "6", None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_unsupported_input_put_lists_defer_without_variable_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  input &input_list.;\n"
        "  put _all_;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = {
        (f["type"], f["status"], f["object"], f["source"]["rule"])
        for f in ctx.findings
    }
    assert reads == set()
    assert writes == set()
    assert findings == {
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "input &input_list.;",
            "data_step_deferred_variable_construct",
        ),
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "put _all_;",
            "data_step_deferred_variable_construct",
        ),
    }


def test_if_compound_or_condition_declines_without_garbled_literal():
    blocks, ctx, events = build(
        'data adam.adlb;\n  set sdtm.lb;\n'
        '  if aesdth = "Y" or upcase(strip(aeout)) = "FATAL" then aedthfl = "Y";\n'
        'run;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(e["type"] == "reads_variable" for e in ctx.edges)
    assert not any(
        e.get("value") == 'Y" or upcase(strip(aeout)) = "FATAL"'
        for e in ctx.edges
    )
    assert ctx.findings == []


def test_if_three_way_or_condition_declines_without_garbled_literal():
    blocks, ctx, events = build(
        'data adam.adlb;\n  set sdtm.lb;\n'
        '  if a = "Y" or b = "Y" or c = "Y" then d = "Y";\n'
        'run;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(e["type"] == "reads_variable" for e in ctx.edges)
    assert ctx.findings == []


def test_if_simple_literal_condition_remains_unchanged():
    blocks, ctx, events = build(
        'data adam.adlb;\n  set sdtm.lb;\n  if x = "Y" then y = "Y";\nrun;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    read = next(
        e for e in ctx.edges
        if e["type"] == "reads_variable" and e["from"] == "variable:adam.adlb.x"
    )
    assert read["value"] == "Y"
    assert read["operator"] == "="


def test_if_doubled_quote_literal_remains_one_literal():
    blocks, ctx, events = build(
        'data adam.adlb;\n  set sdtm.lb;\n'
        '  if x = "she said ""hi""" then y = "Y";\n'
        'run;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    read = next(
        e for e in ctx.edges
        if e["type"] == "reads_variable" and e["from"] == "variable:adam.adlb.x"
    )
    assert read["value"] == 'she said ""hi""'
    assert read["operator"] == "="


def test_where_condition_literal_captures_value_and_operator():
    blocks, ctx, events = build(
        'data adam.adlb;\n  set sdtm.lb;\n  where anl01fl = "Y";\nrun;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    read = next(e for e in ctx.edges if e["type"] == "reads_variable")
    assert read["value"] == "Y"
    assert read["operator"] == "="
    assert read["source"]["rule"] == "data_step_where_condition"


def test_statement_order_reflects_the_statements_own_order_not_the_block():
    """Acceptance criterion: statement_order comes from `statement.as_source`,
    not `block.as_source` -- two statements must carry two different orders."""
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  anl01fl = 'Y';\n  anl01vs = 'N';\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    orders = {
        e["to"]: e["source"]["statement_order"]
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert orders["variable:adam.adlb.anl01fl"] == 3
    assert orders["variable:adam.adlb.anl01vs"] == 4
    assert orders["variable:adam.adlb.anl01fl"] != orders["variable:adam.adlb.anl01vs"]


def test_single_output_step_binds_variables_without_findings():
    blocks, ctx, events = build(
        "data adam.adlb;\n  set sdtm.lb;\n  anl01fl = 'Y';\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(n["type"] == "UnknownVariable" for n in ctx.nodes)
    assert not any(f["type"] == "unqualified_variable_reference" for f in ctx.findings)


def test_unbound_variable_in_multi_output_step_becomes_unknown_variable_with_finding():
    """Multi-output DATA step: no single dataset owns a bare variable
    reference, so it must become an UnknownVariable + mandatory finding,
    never a guessed edge."""
    blocks, ctx, events = build(
        "data work.sae work.nonsae;\n"
        "  set sdtm.ae;\n"
        "  if aeser = 'Y' then output work.sae;\n"
        "  else output work.nonsae;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    unknown = [n for n in ctx.nodes if n["type"] == "UnknownVariable"]
    assert len(unknown) == 1
    assert unknown[0]["unresolved_expression"] == "aeser"
    assert unknown[0]["id"] == f"unknownvariable:aeser@{unknown[0]['source']['statement_order']}"
    assert not any(n["type"] == "Variable" and n["variable"] == "aeser" for n in ctx.nodes)

    findings = [f for f in ctx.findings if f["type"] == "unqualified_variable_reference"]
    assert len(findings) == 1
    assert findings[0]["status"] == "UNQUALIFIED_VARIABLE"
    assert findings[0]["severity"] == "WARNING"
    assert findings[0]["affected_nodes"] == [unknown[0]["id"]]

    read = next(e for e in ctx.edges if e["type"] == "reads_variable")
    assert read["from"] == unknown[0]["id"]


def test_data_null_step_treats_bare_variables_as_unbound():
    """DATA _NULL_ writes no Dataset (section 12.8), so it has no single
    owning dataset for any bare variable reference either."""
    blocks, ctx, events = build(
        "data _null_;\n  set sdtm.ae;\n  if aeser = 'Y' then call symputx('flag', 1);\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    unknown = [n for n in ctx.nodes if n["type"] == "UnknownVariable"]
    assert len(unknown) == 1
    assert unknown[0]["unresolved_expression"] == "aeser"


def test_unresolved_output_target_makes_variables_unbound():
    """The third unbound case: the step's own output target failed to
    resolve, so no real Dataset id exists to bind a bare variable to."""
    blocks, ctx, events = build(
        "data work.&missing._out;\n  set sdtm.ae;\n  flag = 'Y';\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    unknown = [n for n in ctx.nodes if n["type"] == "UnknownVariable"]
    assert len(unknown) == 1
    assert unknown[0]["unresolved_expression"] == "flag"


def test_do_loop_produces_finding_and_no_variable_edge():
    """wayfinder: data-step-keep-drop-rename-variable-edges -- a DO loop is
    outside the supported grammar and must produce a deferred-construct
    finding, never a guessed edge."""
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  do i = 1 to 10;\n"
        "  end;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(n["type"] in ("Variable", "UnknownVariable") for n in ctx.nodes)
    assert not any(e["type"] in ("reads_variable", "writes_variable") for e in ctx.edges)
    findings = [f for f in ctx.findings if f["type"] == "deferred_variable_construct"]
    assert len(findings) == 1
    assert findings[0]["status"] == "NOT_EXECUTED"
    assert "DO loop" in findings[0]["message"]


def test_array_reference_produces_finding_and_no_variable_edge():
    """An array-subscript reference (`arr{i}`) is outside the supported
    grammar -- must not be misread as bare identifiers `arr`/`i`."""
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  x = arr{i};\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(n["type"] in ("Variable", "UnknownVariable") for n in ctx.nodes)
    assert not any(e["type"] in ("reads_variable", "writes_variable") for e in ctx.edges)
    findings = [f for f in ctx.findings if f["type"] == "deferred_variable_construct"]
    assert len(findings) == 1
    assert "Array reference" in findings[0]["message"]


def test_declared_array_paren_subscript_defers_without_index_reads():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  array x{3} x1-x3;\n"
        "  x(i) = x(i) + amount;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = {
        (f["type"], f["status"], f["object"], f["source"]["rule"])
        for f in ctx.findings
    }
    assert reads == set()
    assert writes == set()
    assert findings == {
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "array x{3} x1-x3;",
            "data_step_deferred_variable_construct",
        ),
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "x(i) = x(i) + amount;",
            "data_step_deferred_variable_construct",
        ),
    }


def test_declared_array_bracket_subscript_defers_without_index_reads():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  array x{3} x1-x3;\n"
        "  z = x[i];\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = {
        (f["type"], f["status"], f["object"], f["source"]["rule"])
        for f in ctx.findings
    }
    assert reads == set()
    assert writes == set()
    assert findings == {
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "array x{3} x1-x3;",
            "data_step_deferred_variable_construct",
        ),
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "z = x[i];",
            "data_step_deferred_variable_construct",
        ),
    }


def test_undeclared_function_call_keeps_normal_variable_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  y = somefunc(i);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.i", None, None, "data_step_assignment"),
    }
    assert writes == {
        ("variable:work.a.y", None, None, "data_step_assignment"),
    }
    assert ctx.findings == []


def test_array_reference_detection_ignores_a_brace_inside_a_string_literal():
    """codex-review fix: `x = "not_an_array{";` is a plain literal
    assignment, not an array reference -- the array-reference check must
    mask quoted content first, same as `_rhs_identifiers` already does."""
    blocks, ctx, events = build(
        'data work.a;\n  set sdtm.ae;\n  x = "not_an_array{";\nrun;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(f["type"] == "deferred_variable_construct" for f in ctx.findings)
    write = next(e for e in ctx.edges if e["type"] == "writes_variable")
    assert write["value"] == "not_an_array{"


def test_rename_produces_reads_old_and_writes_new_variable_edges():
    """Ticket's settled decision: RENAME old=new reads the old name and
    writes the new one, both value/operator null."""
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  rename aeterm=term;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    read = next(e for e in ctx.edges if e["type"] == "reads_variable")
    assert read["from"] == "variable:work.a.aeterm"
    assert read["to"] == "step:001"
    assert read["value"] is None
    assert read["operator"] is None
    assert read["source"]["rule"] == "data_step_rename"

    write = next(e for e in ctx.edges if e["type"] == "writes_variable")
    assert write["from"] == "step:001"
    assert write["to"] == "variable:work.a.term"
    assert write["value"] is None
    assert write["operator"] is None


def test_rename_name_literal_pair_produces_read_and_write_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  rename 'old name'n='new name'n;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["from"], e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        (
            "variable:work.a.'old name'n",
            "step:001",
            None,
            None,
            "data_step_rename",
        ),
    }
    assert writes == {
        (
            "step:001",
            "variable:work.a.'new name'n",
            None,
            None,
            "data_step_rename",
        ),
    }
    assert ctx.findings == []


def test_rename_mixed_bare_and_name_literal_pairs_produces_all_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  rename oldvar=newvar 'old name'n='new name'n;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["from"], e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.oldvar", "step:001", None, None, "data_step_rename"),
        (
            "variable:work.a.'old name'n",
            "step:001",
            None,
            None,
            "data_step_rename",
        ),
    }
    assert writes == {
        ("step:001", "variable:work.a.newvar", None, None, "data_step_rename"),
        (
            "step:001",
            "variable:work.a.'new name'n",
            None,
            None,
            "data_step_rename",
        ),
    }
    assert ctx.findings == []


def test_rename_bare_identifier_pair_behavior_is_unchanged():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  rename oldvar=newvar;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (e["from"], e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        (e["from"], e["to"], e["value"], e["operator"], e["source"]["rule"])
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.a.oldvar", "step:001", None, None, "data_step_rename"),
    }
    assert writes == {
        ("step:001", "variable:work.a.newvar", None, None, "data_step_rename"),
    }
    assert ctx.findings == []


def test_rename_multiple_pairs_each_get_their_own_edges():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  rename A=SMQCD B=SMQNAME;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert reads == {"variable:work.a.a", "variable:work.a.b"}
    assert writes == {"variable:work.a.smqcd", "variable:work.a.smqname"}


def test_keep_produces_one_writes_variable_edge_per_variable():
    """Ticket's settled decision: KEEP asserts each variable is present in
    the output -- same direction as a normal write."""
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  keep usubjid aeseq;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]
    assert {e["to"] for e in writes} == {"variable:work.a.usubjid", "variable:work.a.aeseq"}
    assert all(e["from"] == "step:001" for e in writes)
    assert all(e["value"] is None and e["operator"] is None for e in writes)
    assert all(e["source"]["rule"] == "data_step_keep" for e in writes)
    assert not any(e["type"] == "reads_variable" for e in ctx.edges)


def test_drop_produces_one_reads_variable_edge_per_variable():
    """Ticket's settled decision: DROP only references the variable to
    exclude it, never asserts output presence."""
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  drop tempvar1 tempvar2;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    assert {e["from"] for e in reads} == {"variable:work.a.tempvar1", "variable:work.a.tempvar2"}
    assert all(e["to"] == "step:001" for e in reads)
    assert all(e["value"] is None and e["operator"] is None for e in reads)
    assert all(e["source"]["rule"] == "data_step_drop" for e in reads)
    assert not any(e["type"] == "writes_variable" for e in ctx.edges)


def test_variable_not_named_anywhere_gets_no_edge():
    """No schema-wide pass-through: a variable never mentioned in an
    assignment/condition/KEEP/DROP/RENAME gets no edge at all."""
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  keep usubjid;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    variable_ids = {
        e["from"] for e in ctx.edges if e["type"] in ("reads_variable", "writes_variable")
    } | {
        e["to"] for e in ctx.edges if e["type"] in ("reads_variable", "writes_variable")
    }
    assert "variable:work.a.aeseq" not in variable_ids
    assert not any(n["id"] == "variable:work.a.aeseq" for n in ctx.nodes)


def test_keep_with_macro_variable_list_produces_finding_and_no_edge():
    """`KEEP &varlist.;` is a dynamic list -- must not guess which
    variables it expands to."""
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  keep &varlist.;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(e["type"] in ("reads_variable", "writes_variable") for e in ctx.edges)
    findings = [f for f in ctx.findings if f["type"] == "deferred_variable_construct"]
    assert len(findings) == 1
    assert "Dynamic variable list" in findings[0]["message"]


def test_drop_with_numbered_range_produces_finding_and_no_edge():
    """`DROP var1-var9;` is a numbered range, not an enumerated list --
    dynamic, must fall through to a finding."""
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  drop var1-var9;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(e["type"] in ("reads_variable", "writes_variable") for e in ctx.edges)
    findings = [f for f in ctx.findings if f["type"] == "deferred_variable_construct"]
    assert len(findings) == 1
    assert "Dynamic variable list" in findings[0]["message"]


def test_rename_with_macro_variable_produces_finding_and_no_edge():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  rename &oldname.=newname;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(e["type"] in ("reads_variable", "writes_variable") for e in ctx.edges)
    findings = [f for f in ctx.findings if f["type"] == "deferred_variable_construct"]
    assert len(findings) == 1
    assert "Dynamic variable list" in findings[0]["message"]


def test_resolved_macro_in_assignment_rhs_is_scanned_as_a_literal():
    blocks, ctx, events = build(
        "%let n=1;\n"
        "data work.out;\n"
        "  set work.in;\n"
        "  flag=%unquote(&n);\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == set()
    assert writes == {(
        "step:001", "variable:work.out.flag", "1", None,
    )}
    assert not ctx.findings
    assert {
        node["id"] for node in ctx.nodes
        if node["type"] in ("Variable", "UnknownVariable")
    } == {"variable:work.out.flag"}


def test_resolved_macro_assignment_target_is_scanned_before_edges():
    blocks, ctx, events = build(
        "%let target=flag;\n"
        "data work.out;\n"
        "  set work.in;\n"
        "  &target = source;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {(
        "variable:work.out.source", "step:001", None, None,
    )}
    assert writes == {(
        "step:001", "variable:work.out.flag", None, None,
    )}
    assert not ctx.findings
    assert {
        node["id"] for node in ctx.nodes
        if node["type"] in ("Variable", "UnknownVariable")
    } == {"variable:work.out.flag", "variable:work.out.source"}


def test_unresolved_macro_in_assignment_rhs_stays_unknown():
    blocks, ctx, events = build(
        "data work.out;\n"
        "  set work.in;\n"
        "  flag = &missing;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    unknowns = [node for node in ctx.nodes if node["type"] == "UnknownVariable"]
    assert len(unknowns) == 1
    assert unknowns[0]["unresolved_expression"] == "missing"
    assert {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    } == {(unknowns[0]["id"], "step:001", None, None)}
    assert {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    } == {("step:001", "variable:work.out.flag", None, None)}
    assert {
        (finding["type"], finding["status"], finding["severity"])
        for finding in ctx.findings
    } == {("unqualified_variable_reference", "UNQUALIFIED_VARIABLE", "WARNING")}


def test_macro_resolution_respects_single_and_double_quoted_assignment_literals():
    blocks, ctx, events = build(
        "%let dose=999;\n"
        "data work.out;\n"
        "  set work.in;\n"
        "  single_note = 'the &dose is \"literal\", not macro';\n"
        "  double_note = \"the &dose is 'literal', not macro\";\n"
        "  bare_dose = &dose;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == set()
    assert writes == {
        ("step:001", "variable:work.out.single_note", 'the &dose is "literal", not macro', None),
        ("step:001", "variable:work.out.double_note", "the 999 is 'literal', not macro", None),
        ("step:001", "variable:work.out.bare_dose", "999", None),
    }
    assert not ctx.findings


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("data step rules: all checks passed")


if __name__ == "__main__":
    demo()
