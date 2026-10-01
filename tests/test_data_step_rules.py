"""DATA step rules (dev plan section 12, 21 Phase 4)."""

import conftest  # noqa: F401

from sas_graph import rules_data_step
from sas_graph.blocks import group_blocks
from sas_graph.graph_model import GraphContext
from sas_graph.macro_state import walk_let_statements
from sas_graph.statements import split_statements


def build(text, file_name="adae.sas", derivation_v1=True):
    result = split_statements(text, file_name)
    blocks, _ = group_blocks(result.statements)
    events = walk_let_statements(result.statements)
    ctx = GraphContext(
        main_programs=[file_name], setup_file="setup.sas", run_id="r1",
        derivation_v1=derivation_v1,
    )
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


def test_data_target_options_do_not_create_fake_output_targets():
    blocks, ctx, events = build(
        'data rawlib.out(keep=A B drop=C D rename=(a=b c=d) '
        'where=(x="A B" and nested=(fn(y="( )"))));\n'
        "  output;\n"
        "run;\n",
        "synthetic.sas",
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(node for node in ctx.nodes if node["type"] == "Step")
    writes = {edge["to"] for edge in ctx.edges if edge["type"] == "writes_dataset"}
    assert writes == {"dataset:rawlib.out"}
    assert not any(finding["type"] == "AMBIGUOUS_OUTPUT_TARGET" for finding in ctx.findings)
    assert "MULTI_OUTPUT_DATA_STEP" not in step["patterns"]
    assert "MULTI_OUTPUT_AMBIGUOUS" not in step["patterns"]


def test_data_target_options_preserve_genuine_multiple_outputs():
    blocks, ctx, events = build(
        "data rawlib.a(keep=x y) rawlib.b;\n  output;\nrun;\n",
        "synthetic.sas",
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(node for node in ctx.nodes if node["type"] == "Step")
    writes = {edge["to"] for edge in ctx.edges if edge["type"] == "writes_dataset"}
    assert writes == {"dataset:rawlib.a", "dataset:rawlib.b"}
    assert "MULTI_OUTPUT_DATA_STEP" in step["patterns"]
    assert "MULTI_OUTPUT_AMBIGUOUS" in step["patterns"]
    assert any(finding["type"] == "AMBIGUOUS_OUTPUT_TARGET" for finding in ctx.findings)


def test_data_target_options_with_space_before_paren_stay_single_output():
    blocks, ctx, events = build(
        "data rawlib.out (keep=a b);\n  output;\nrun;\n",
        "synthetic.sas",
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(node for node in ctx.nodes if node["type"] == "Step")
    writes = {edge["to"] for edge in ctx.edges if edge["type"] == "writes_dataset"}
    assert writes == {"dataset:rawlib.out"}
    assert not any(finding["type"] == "AMBIGUOUS_OUTPUT_TARGET" for finding in ctx.findings)
    assert "MULTI_OUTPUT_DATA_STEP" not in step["patterns"]


def test_split_top_level_whitespace_handles_nesting_quotes_and_incomplete_input():
    split = rules_data_step._split_top_level_whitespace
    assert split('a(where=(x=fn("b c"))) d') == ['a(where=(x=fn("b c")))', "d"]
    assert split('a(where=(x="say ""hi there""")) b') == [
        'a(where=(x="say ""hi there"""))',
        "b",
    ]
    assert split("a b(where=(x='y'") == ["a", "b(where=(x='y'"]
    assert split("") == []


def test_plain_keep_drop_rename_shape_metadata_is_preserved():
    blocks, ctx, events = build(
        "data rawlib.out;\n"
        "  keep a b c;\n"
        "  drop d;\n"
        "  rename e=f;\n"
        "run;\n",
        "synthetic.sas",
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(node for node in ctx.nodes if node["type"] == "Step")
    assert step["keep_vars"] == ["a", "b", "c"]
    assert step["drop_vars"] == ["d"]
    assert step["rename_map"] == {"e": "f"}


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


def test_data_null_call_symput_marks_runtime_macro_creation():
    blocks, ctx, events = build(
        "data _null_;\n  call symput('n', 1);\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    step = next(node for node in ctx.nodes if node["type"] == "Step")
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
    write = next(e for e in ctx.edges if e["type"] == "writes_dataset")
    assert write["evidence"]["kind"] == "RESOLVED"
    assert write["evidence"]["resolution"] == "EXACT"


def test_mixed_macro_and_literal_data_targets_keep_literal_edges_observed():
    blocks, ctx, events = build(
        "data &outlib..out1 out2;\n  set work.src;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    evidence = {
        (edge["type"], edge["from"], edge["to"]): edge["evidence"]["kind"]
        for edge in ctx.edges
        if edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"}
    }

    assert evidence[("reads_dataset", "dataset:work.src", "step:001")] == "OBSERVED"
    assert evidence[("writes_dataset", "step:001", "dataset:work.out2")] == "OBSERVED"
    assert evidence[("depends_on", "dataset:work.out2", "dataset:work.src")] == "OBSERVED"
    assert evidence[("writes_dataset", "step:001", "unknowndataset:&outlib..out1")] == "UNKNOWN"


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
    assert [
        finding["object"] for finding in ctx.findings
        if finding["type"] == "unknown_expression"
    ] == ["input(y, best32.)", "put(y, yymmdd10.)"]


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


def test_if_not_precedence_preserves_direct_comparison_metadata():
    cases = (
        ("not a = 1", {"a": (None, None)}),
        ("not (a = 1)", {"a": ("1", "=")}),
        ("not a = 1 and b = 2", {"a": (None, None), "b": ("2", "=")}),
        ("not missing(x) and y = 1", {"x": (None, None), "y": ("1", "=")}),
    )
    for condition, expected in cases:
        blocks, ctx, events = build(chr(10).join((
            "data work.a;",
            "  set sdtm.ae;",
            f"  if {condition} then flag = 1;",
            "run;",
        )))
        rules_data_step.apply(blocks[0], ctx, events)

        reads = {
            edge["from"].rsplit(".", 1)[-1]: (
                edge["value"], edge["operator"],
            )
            for edge in ctx.edges
            if edge["type"] == "reads_variable"
            and edge["source"]["rule"] == "data_step_if_condition"
        }
        assert reads == expected, condition
        assert not any(
            finding["type"] == "unknown_expression"
            for finding in ctx.findings
        ), condition


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


def test_if_then_assignment_emits_derivation_and_conditioned_by_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if left = right then target = a + b;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    derives = [edge for edge in ctx.edges if edge["type"] == "derives"]
    conditioned = [edge for edge in ctx.edges if edge["type"] == "conditioned_by"]
    assert len(derives) == 1
    assert derives[0]["from"] == "step:001"
    assert derives[0]["to"] == "variable:work.a.target"
    assert derives[0]["expression"] == "a + b"
    assert derives[0]["evidence"]["kind"] == "OBSERVED"
    assert derives[0]["evidence"]["resolution"] == "EXACT"
    assert derives[0]["evidence"]["confidence"] is None
    assert {
        (edge["from"], edge["to"], edge["condition_text"])
        for edge in conditioned
    } == {
        ("variable:work.a.left", "step:001", "left = right"),
        ("variable:work.a.right", "step:001", "left = right"),
    }
    assert len([edge for edge in ctx.edges if edge["type"] == "writes_variable"]) == 1


def test_chained_if_condition_emits_each_distinct_conditioned_by_edge():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set adam.adae;\n"
        "  if trtsdt <= aestdt <= trtedt then trtemfl = 'Y';\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    derives = [edge for edge in ctx.edges if edge["type"] == "derives"]
    conditioned = [edge for edge in ctx.edges if edge["type"] == "conditioned_by"]
    assert len(derives) == 1
    assert derives[0]["from"] == "step:001"
    assert derives[0]["to"] == "variable:work.a.trtemfl"
    assert {
        (edge["from"], edge["to"], edge["condition_text"])
        for edge in conditioned
    } == {
        ("variable:work.a.trtsdt", "step:001", "trtsdt <= aestdt <= trtedt"),
        ("variable:work.a.aestdt", "step:001", "trtsdt <= aestdt <= trtedt"),
        ("variable:work.a.trtedt", "step:001", "trtsdt <= aestdt <= trtedt"),
    }
    assert [
        (edge["from"], edge["operator"], edge["value"])
        for edge in ctx.edges
        if edge["type"] == "reads_variable"
        and edge["source"]["rule"] == "data_step_if_condition"
    ] == [
        ("variable:work.a.trtsdt", "<=", None),
        ("variable:work.a.aestdt", "<=", None),
        ("variable:work.a.aestdt", "<=", None),
        ("variable:work.a.trtedt", "<=", None),
    ]


def test_derivation_edges_deduplicate_repeated_condition_variables():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if left = left then target = 'Y';\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    conditioned = [edge for edge in ctx.edges if edge["type"] == "conditioned_by"]
    assert [(edge["from"], edge["condition_text"]) for edge in conditioned] == [
        ("variable:work.a.left", "left = left"),
    ]


def test_derivation_capability_off_preserves_existing_assignment_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if left = right then target = 'Y';\n"
        "run;\n",
        derivation_v1=False,
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(edge["type"] == "derives" for edge in ctx.edges)
    assert not any(edge["type"] == "conditioned_by" for edge in ctx.edges)
    assert any(edge["type"] == "writes_variable" for edge in ctx.edges)
    assert ctx.to_graph()["schema"]["capabilities"] == [
        "dataset_lineage",
        "variable_lineage",
        "evidence_v1",
    ]


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


def test_if_and_else_do_blocks_emit_body_edges_and_guarded_block_findings():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if c then do;\n"
        "    x = y;\n"
        "    do i = 1 to 3;\n"
        "      z = w;\n"
        "    end;\n"
        "  end;\n"
        "  else do;\n"
        "    a = b;\n"
        "  end;\n"
        "  else if d then do;\n"
        "    p = q;\n"
        "  end;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        e["from"]
        for e in ctx.edges if e["type"] == "reads_variable"
    }
    writes = {
        e["to"]
        for e in ctx.edges if e["type"] == "writes_variable"
    }
    findings = [
        finding for finding in ctx.findings
        if finding["type"] == "deferred_variable_construct"
    ]
    assert reads == {
        "variable:work.a.y",
        "variable:work.a.w",
        "variable:work.a.b",
        "variable:work.a.q",
    }
    assert writes == {
        "variable:work.a.x",
        "variable:work.a.z",
        "variable:work.a.a",
        "variable:work.a.p",
    }
    assert [finding["status"] for finding in findings] == [
        "NOT_EXECUTED", "NOT_EXECUTED", "NOT_EXECUTED", "NOT_EXECUTED",
    ]
    assert sum("Guarded block" in finding["message"] for finding in findings) == 3
    assert sum("DO loop" in finding["message"] for finding in findings) == 1
    assert not any(edge["type"] == "conditioned_by" for edge in ctx.edges)


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
        (f["type"], f["status"], f["object"], f["source"]["rule"], f["message"])
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
            "Guarded block: statements inside this do; are not linked to the condition.",
        ),
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "x=1;",
            "data_step_deferred_variable_construct",
            "SELECT branch is outside the supported variable-edge grammar; no variable edge was inferred for this statement.",
        ),
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "y=2;",
            "data_step_deferred_variable_construct",
            "SELECT branch is outside the supported variable-edge grammar; no variable edge was inferred for this statement.",
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


def test_call_symput_function_value_matches_symputx_findings():
    """Macro-state owns the runtime finding; DATA emits none for this shape."""
    findings_by_routine = {}
    for routine in ("symput", "symputx"):
        source = f'data work.a;\n  call {routine}("x", repeat("a", 3));\nrun;\n'
        blocks, ctx, events = build(source)
        statements = split_statements(source, "adae.sas").statements
        macro_findings = walk_let_statements(statements, findings_only=True)
        rules_data_step.apply(blocks[0], ctx, events)

        runtime_findings = [
            finding for finding in macro_findings
            if finding["type"] == "runtime_macro_variable_creation"
        ]
        assert len(runtime_findings) == 1
        assert runtime_findings[0]["source"]["rule"] == f"call_{routine}"
        assert runtime_findings[0]["status"] == "NOT_EXECUTED"
        assert runtime_findings[0]["severity"] == "INFORMATION"
        assert ctx.findings == []
        findings_by_routine[routine] = {
            (
                finding["type"], finding["status"], finding["severity"],
                finding["object"],
                finding["message"].replace(routine.upper(), "<routine>"),
                finding["source"]["rule"].replace(routine, "<routine>"),
            )
            for finding in [*macro_findings, *ctx.findings]
        }

    assert findings_by_routine["symput"] == findings_by_routine["symputx"]


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


def test_sum_statement_accepts_single_character_rhs():
    for statement, accumulator, rhs_variables in (
        ("n+1;", "n", set()),
        ("n+10;", "n", set()),
        ("t+x;", "t", {"x"}),
        ("total+amt*2;", "total", {"amt"}),
    ):
        blocks, ctx, events = build(
            "data work.a;\n"
            "  set sdtm.ae;\n"
            f"  {statement}\n"
            "run;\n"
        )
        rules_data_step.apply(blocks[0], ctx, events)

        reads = {
            e["from"] for e in ctx.edges if e["type"] == "reads_variable"
        }
        writes = {
            e["to"] for e in ctx.edges if e["type"] == "writes_variable"
        }
        assert reads == {
            f"variable:work.a.{accumulator}",
            *(f"variable:work.a.{name}" for name in rhs_variables),
        }
        assert writes == {f"variable:work.a.{accumulator}"}
        assert ctx.findings == []


def test_malformed_sum_statement_defers_without_variable_edges():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  total + amount +;\n"
        "  n+;\n"
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
        (
            "deferred_variable_construct",
            "NOT_EXECUTED",
            "n+;",
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


def test_select_without_selector_emits_when_condition_reads():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  select;\n"
        "    when (upcase(x) = 'A') y = z;\n"
        "  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
    assert {edge["from"] for edge in reads} == {
        "variable:work.a.x",
        "variable:work.a.z",
    }
    condition_edges = [
        edge for edge in ctx.edges if edge["type"] == "conditioned_by"
    ]
    assert {edge["from"] for edge in condition_edges} == {"variable:work.a.x"}
    assert {edge["condition_text"] for edge in condition_edges} == {
        "upcase(x) = 'A'",
    }
    assert ctx.findings == []


def test_select_when_actions_parse_balanced_parentheses():
    cases = (
        ("(a + b) * 2", {"a", "b"}),
        ("sum(a, b) + 1", {"a", "b"}),
        ("round(a, 1) * 2", {"a"}),
    )
    for expression, action_reads in cases:
        for selector in (True, False):
            select_text = "select (x);" if selector else "select;"
            when_condition = "1" if selector else "gate > 1"
            expected_reads = action_reads | ({"x"} if selector else {"gate"})
            blocks, ctx, events = build(
                "data work.a;\n  set sdtm.ae;\n  "
                f"{select_text}\n  when ({when_condition}) y = {expression};\n"
                "  end;\nrun;\n"
            )
            rules_data_step.apply(blocks[0], ctx, events)

            assert {
                edge["from"].rsplit(".", 1)[-1]
                for edge in ctx.edges if edge["type"] == "reads_variable"
            } == expected_reads, (selector, expression)
            assert {
                edge["to"] for edge in ctx.edges
                if edge["type"] == "writes_variable"
            } == {"variable:work.a.y"}, (selector, expression)
            assert not any(
                finding["type"] in {
                    "unknown_expression", "deferred_variable_construct",
                }
                for finding in ctx.findings
            ), (selector, expression)


def test_select_with_unparsed_selector_keeps_selector_findings_and_semantics():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n"
        "  select (upcase(x));\n"
        "    when ('A') y = 1;\n"
        "    otherwise y = 0;\n"
        "  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(edge["type"] == "reads_variable" for edge in ctx.edges)
    assert {
        edge["to"] for edge in ctx.edges if edge["type"] == "writes_variable"
    } == {"variable:work.a.y"}
    assert [
        (finding["type"], finding["object"])
        for finding in ctx.findings
        if finding["type"] == "deferred_variable_construct"
    ] == [("deferred_variable_construct", "select (upcase(x));")]

    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n"
        "  select (upcase(x));\n"
        "    when (missing(w)) y = 1;\n"
        "  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(
        edge["type"] in {"reads_variable", "writes_variable", "conditioned_by"}
        for edge in ctx.edges
    )
    assert [
        (finding["type"], finding["object"])
        for finding in ctx.findings
        if finding["type"] == "deferred_variable_construct"
    ] == [
        ("deferred_variable_construct", "select (upcase(x));"),
        ("deferred_variable_construct", "when (missing(w)) y = 1;"),
    ]


def test_selectorless_when_condition_with_quoted_paren_keeps_action():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  select;\n"
        "    when (x = ')') y = 1;\n"
        "  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
    assert [(e["from"], e.get("value")) for e in reads] == [
        ("variable:work.a.x", ")"),
    ]
    assert [e["to"] for e in ctx.edges if e["type"] == "writes_variable"] == [
        "variable:work.a.y",
    ]
    assert ctx.findings == []


def test_selectorless_when_literal_condition_keeps_action_reads_and_writes():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  select;\n"
        "    when (1) y = a;\n"
        "  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert {e["from"] for e in ctx.edges if e["type"] == "reads_variable"} == {
        "variable:work.a.a",
    }
    assert [e["to"] for e in ctx.edges if e["type"] == "writes_variable"] == [
        "variable:work.a.y",
    ]
    assert not any(f["type"] == "unknown_expression" for f in ctx.findings)


def test_selectorless_select_when_closing_paren_literal_keeps_condition_reads():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  select;\n"
        "    when (x = ')') y = 1;\n  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
    assert [(edge["from"], edge["value"], edge["operator"]) for edge in reads] == [
        ("variable:work.a.x", ")", "="),
    ]
    assert [(edge["to"], edge["value"]) for edge in ctx.edges if edge["type"] == "writes_variable"] == [
        ("variable:work.a.y", "1"),
    ]
    assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings)


def test_selectorless_select_when_literal_condition_reads_action_rhs():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  select;\n"
        "    when (1) y = a;\n  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert [edge["from"] for edge in ctx.edges if edge["type"] == "reads_variable"] == [
        "variable:work.a.a",
    ]
    assert [(edge["to"], edge["value"]) for edge in ctx.edges if edge["type"] == "writes_variable"] == [
        ("variable:work.a.y", None),
    ]
    assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings)


def test_select_unsupported_when_condition_emits_no_branch_edges():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  select;\n"
        "    when ('Y', 'N') y = z;\n"
        "  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(
        edge["type"] in {"reads_variable", "writes_variable"}
        for edge in ctx.edges
    )
    assert [
        (finding["type"], finding["object"])
        for finding in ctx.findings
        if finding["type"] == "unknown_expression"
    ] == [("unknown_expression", "'Y', 'N'")]


def test_if_not_missing_condition_emits_reads_and_condition_edges():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n"
        "  if not missing(x) then y = z;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
    assert {edge["from"] for edge in reads} == {
        "variable:work.a.x",
        "variable:work.a.z",
    }
    condition_edges = [
        edge for edge in ctx.edges if edge["type"] == "conditioned_by"
    ]
    assert {edge["from"] for edge in condition_edges} == {"variable:work.a.x"}
    assert {edge["condition_text"] for edge in condition_edges} == {
        "not missing(x)",
    }
    assert not any(
        finding["type"] == "unknown_expression" for finding in ctx.findings
    )


def test_if_pure_function_condition_emits_reads_and_condition_edges():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n"
        "  if upcase(x) = 'A' then y = z;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
    assert {edge["from"] for edge in reads} == {
        "variable:work.a.x",
        "variable:work.a.z",
    }
    condition_edges = [
        edge for edge in ctx.edges if edge["type"] == "conditioned_by"
    ]
    assert {edge["from"] for edge in condition_edges} == {"variable:work.a.x"}
    assert {edge["condition_text"] for edge in condition_edges} == {
        "upcase(x) = 'A'",
    }
    assert not any(
        finding["type"] == "unknown_expression" for finding in ctx.findings
    )


def test_condition_reads_only_carry_literal_facts_for_plain_variables():
    cases = (
        ("year(d) = 2020", {"d"}, None, None),
        ("substr(x, 1, 2) = 'AB'", {"x"}, None, None),
        ("length(x) > 1", {"x"}, None, None),
        ("sum(a, b) > 3", {"a", "b"}, None, None),
        ("x > 1", {"x"}, "1", ">"),
    )
    for condition, expected_reads, expected_value, expected_operator in cases:
        blocks, ctx, events = build(
            "data work.a;\n  set sdtm.ae;\n"
            f"  if {condition} then y = 1;\nrun;\n"
        )
        rules_data_step.apply(blocks[0], ctx, events)

        reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
        assert {
            edge["from"].rsplit(".", 1)[-1] for edge in reads
        } == expected_reads, condition
        assert all(
            edge["value"] == expected_value
            and edge["operator"] == expected_operator
            for edge in reads
        ), condition
        assert not any(
            finding["type"] == "unknown_expression" for finding in ctx.findings
        ), condition


def test_boolean_and_in_conditions_emit_reads_for_if_and_where():
    cases = (
        ("a > 1 and b < 2", {"a": ("1", ">"), "b": ("2", "<")}),
        ("a > 1 or b < 2", {"a": ("1", ">"), "b": ("2", "<")}),
        ("(a > 1 and b < 2) or c > 3", {
            "a": ("1", ">"), "b": ("2", "<"), "c": ("3", ">"),
        }),
        ("x in (1, 2)", {"x": (None, None)}),
        ("x not in ('A', 'B')", {"x": (None, None)}),
        ("x NOT IN ('A', 'B')", {"x": (None, None)}),
        ("x Not In ('A', 'B')", {"x": (None, None)}),
        ("x = 1 or y NOT IN ('A')", {"x": ("1", "="), "y": (None, None)}),
        ("x in (a, b)", {"x": (None, None), "a": (None, None), "b": (None, None)}),
        ("x in (upcase(a), b)", {"x": (None, None), "a": (None, None), "b": (None, None)}),
        ("not (x in (1, 2))", {"x": (None, None)}),
        ("a > 1 AND b < 2", {"a": ("1", ">"), "b": ("2", "<")}),
    )
    nl = chr(10)
    for condition, expected in cases:
        for statement_kind in ("if", "where"):
            statement = (
                f"if {condition} then target = 'Y';"
                if statement_kind == "if"
                else f"where {condition};"
            )
            program = "data work.a;" + nl + "  set sdtm.ae;" + nl + "  " + statement + nl + "run;" + nl
            blocks, ctx, events = build(program)
            rules_data_step.apply(blocks[0], ctx, events)

            reads = [
                edge for edge in ctx.edges
                if edge["type"] == "reads_variable"
                and edge["source"]["rule"] == f"data_step_{statement_kind}_condition"
            ]
            assert {
                edge["from"].rsplit(".", 1)[-1]: (edge["value"], edge["operator"])
                for edge in reads
            } == expected, (statement_kind, condition)
            if statement_kind == "if":
                conditioned = [edge for edge in ctx.edges if edge["type"] == "conditioned_by"]
                assert {edge["from"].rsplit(".", 1)[-1] for edge in conditioned} == set(expected)
            assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings), (statement_kind, condition)


def test_assignment_rhs_boolean_and_in_lists_emit_variable_reads():
    nl = chr(10)
    program = "data work.a;" + nl + "  set sdtm.ae;" + nl + "  y = a > 1 and b < 2;" + nl + "  z = x in (1, 2);" + nl + "  q = x not in ('A', 'B');" + nl + "  w = x in (a, b);" + nl + "  v = x NOT IN (1, 2);" + nl + "run;" + nl
    blocks, ctx, events = build(program)
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
    assert {edge["from"].rsplit(".", 1)[-1] for edge in reads} == {"a", "b", "x"}
    assert all(edge["value"] is None and edge["operator"] is None for edge in reads)
    assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings)


def test_unsupported_in_lists_on_assignment_rhs_keep_baseline_unknown_reads():
    for expression in (
        "x in (&mv)", "x in (1:5)", "x in ()", "x in (lag(x))",
        "x in (1,", "x in (1, 2", "x in y",
    ):
        nl = chr(10)
        program = "data work.a;" + nl + "  set sdtm.ae;" + nl + f"  y = {expression};" + nl + "run;" + nl
        blocks, ctx, events = build(program)
        rules_data_step.apply(blocks[0], ctx, events)

        actual = {
            edge["from"].rsplit(".", 1)[-1].split(":")[-1].split("@")[0].lstrip("&")
            for edge in ctx.edges if edge["type"] == "reads_variable"
        }
        assert actual == set(rules_data_step._rhs_identifiers(expression)), expression
        assert [
            (finding["type"], finding["object"])
            for finding in ctx.findings
            if finding["type"] == "unknown_expression"
        ] == [("unknown_expression", expression)], expression


def test_unsupported_boolean_assignment_rhs_keeps_baseline_unknown_reads():
    for expression in (
        "a > 1 and b", "x = a or b", "first.x and z > 1",
        "last.x and z > 1",
    ):
        nl = chr(10)
        program = "data work.a;" + nl + "  set sdtm.ae;" + nl + f"  y = {expression};" + nl + "run;" + nl
        blocks, ctx, events = build(program)
        rules_data_step.apply(blocks[0], ctx, events)

        actual = {
            edge["from"].rsplit(".", 1)[-1].split(":")[-1].split("@")[0].lstrip("&")
            for edge in ctx.edges if edge["type"] == "reads_variable"
        }
        assert actual == set(rules_data_step._rhs_identifiers(expression)), expression
        assert [
            (finding["type"], finding["object"])
            for finding in ctx.findings
            if finding["type"] == "unknown_expression"
        ] == [("unknown_expression", expression)], expression


def test_assignment_rhs_boolean_and_in_lists_keep_unknown_baseline_reads():
    cases = (
        ("first.id and last.id", {"id"}),
        ("first.id or a", {"id", "a"}),
        ("a and of", {"a"}),
        ("a and _all_", {"a"}),
        ("a and &mv", {"a", "mv"}),
        ("(a > &mv) and b", {"a", "mv", "b"}),
        ("a & b", {"a", "b"}),
        ("a | b", {"a", "b"}),
        ("x in (lag(y))", {"x", "y"}),
        ("x in (a+1)", {"x", "a"}),
        ("a + b in (1)", {"a", "b"}),
        ("a > &mv and b < 2", {"a", "mv", "b"}),
        ("first.id > 1 and b < 2", {"id", "b"}),
        ("first.id = 1 or last.id = 1", {"id"}),
        ("a > 1 and b < of", {"a", "b"}),
        ("a > 1 and b < _all_", {"a", "b"}),
        ("a > 1 and b = &mv.", {"a", "b"}),
        ("sum(first, b and c)", {"first", "b", "c"}),
        ("sum(first, x in (1, 2))", {"first", "x"}),
        ("not (first and b)", {"first", "b"}),
        ("(a and b) + first", {"a", "b", "first"}),
        ("first + (x in (1, 2))", {"first", "x"}),
    )
    nl = chr(10)
    for expression, expected_reads in cases:
        program = "data work.a;" + nl + "  set sdtm.ae;" + nl + f"  y = {expression};" + nl + "run;" + nl
        blocks, ctx, events = build(program)
        rules_data_step.apply(blocks[0], ctx, events)

        assert {
            edge["from"].rsplit(".", 1)[-1].split(":")[-1].split("@")[0].lstrip("&")
            for edge in ctx.edges if edge["type"] == "reads_variable"
        } == expected_reads, expression
        assert [finding["object"] for finding in ctx.findings if finding["type"] == "unknown_expression"] == [expression]


def test_supported_nested_boolean_assignment_controls_remain_clean():
    cases = (
        ("sum(first, b)", {"first", "b"}),
        ("first + b", {"first", "b"}),
        ("not first", {"first"}),
        ("sum(a, b and c)", {"a", "b", "c"}),
    )
    nl = chr(10)
    for expression, expected_reads in cases:
        program = "data work.a;" + nl + "  set sdtm.ae;" + nl + f"  y = {expression};" + nl + "run;" + nl
        blocks, ctx, events = build(program)
        rules_data_step.apply(blocks[0], ctx, events)

        actual_reads = {
            edge["from"].rsplit(".", 1)[-1].split(":")[-1].split("@")[0].lstrip("&")
            for edge in ctx.edges if edge["type"] == "reads_variable"
        }
        assert actual_reads == expected_reads, expression
        assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings), expression


def test_expression_term_cap_boundaries_reach_if_where_and_assignment_rhs():
    nl = chr(10)

    def assert_emitters(expression, expected_unknown):
        for kind in ("if", "where", "assignment"):
            statement = (
                f"if {expression} then y = 1;" if kind == "if" else
                f"where {expression};" if kind == "where" else
                f"y = {expression};"
            )
            program = "data work.a;" + nl + "  set sdtm.ae;" + nl + "  " + statement + nl + "run;" + nl
            blocks, ctx, events = build(program)
            rules_data_step.apply(blocks[0], ctx, events)

            unknown = [
                finding["object"] for finding in ctx.findings
                if finding["type"] == "unknown_expression"
            ]
            assert unknown == ([expression] if expected_unknown else []), (
                expression[:80], kind,
            )

    for operator in ("and", "or"):
        for operator_count in (200, 201):
            operands = [f"x{index}" for index in range(operator_count + 1)]
            expression = "sum(" + f" {operator} ".join(operands) + ")"
            assert_emitters(expression, operator_count == 201)

    for operator_count in (200, 201):
        operands = [f"x{index}" for index in range(operator_count + 1)]
        assert_emitters("sum(" + " + ".join(operands) + ")", operator_count == 201)
        assert_emitters(" < ".join(operands), operator_count == 201)
        mixed = "sum(" + " + ".join("x" for _ in operands) + " and y)"
        assert_emitters(mixed, operator_count == 201)


def test_boolean_expression_term_limit_is_safe_for_emitters():
    def invoke_at_depth(depth, action):
        if depth == 0:
            return action()
        return invoke_at_depth(depth - 1, action)

    for depth in (0, 40):
        for size in (150, 250, 950, 990, 3000):
            expression = " and ".join(
                f"a{index} > 1" for index in range(size)
            )
            for kind in ("if", "where", "assignment"):
                statement = (
                    f"if {expression} then y = 1;" if kind == "if" else
                    f"where {expression};" if kind == "where" else
                    f"y = {expression};"
                )
                program = (
                    "data work.a;\n  set sdtm.ae;\n  "
                    + statement + "\nrun;\n"
                )

                def analyze():
                    blocks, ctx, events = build(program)
                    rules_data_step.apply(blocks[0], ctx, events)
                    return ctx

                ctx = invoke_at_depth(depth, analyze)
                unknown = [
                    finding["object"] for finding in ctx.findings
                    if finding["type"] == "unknown_expression"
                ]
                assert unknown == ([] if size == 150 else [expression]), (
                    depth, size, kind,
                )


def test_condition_reads_keep_facts_only_on_direct_comparison_operands():
    cases = (
        ("x = upcase(y)", {"x": (None, "="), "y": (None, None)}),
        ("x = (y)", {"x": (None, "="), "y": (None, "=")}),
        ("1 < x", {"x": (None, "<")}),
        ("'A' = x", {"x": (None, "=")}),
        ("year(d) = 2020", {"d": (None, None)}),
        ("substr(x, 1, 2) = 'AB'", {"x": (None, None)}),
        ("sum(a + 1, b) > 3", {"a": (None, None), "b": (None, None)}),
    )
    nl = chr(10)
    for condition, expected in cases:
        program = "data work.a;" + nl + "  set sdtm.ae;" + nl + f"  if {condition} then target = 1;" + nl + "run;" + nl
        blocks, ctx, events = build(program)
        rules_data_step.apply(blocks[0], ctx, events)

        reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable" and edge["source"]["rule"] == "data_step_if_condition"]
        assert {
            edge["from"].rsplit(".", 1)[-1]: (edge["value"], edge["operator"])
            for edge in reads
        } == expected, condition
        assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings), condition


def test_chained_comparison_reads_keep_each_link_operator_and_literal_fact():
    cases = (
        ("a < b < c", [("a", "<", None), ("b", "<", None), ("b", "<", None), ("c", "<", None)]),
        ("x < y <= 3", [("x", "<", None), ("y", "<", None), ("y", "<=", "3")]),
    )
    nl = chr(10)
    for condition, expected in cases:
        program = "data work.a;" + nl + "  set sdtm.ae;" + nl + f"  if {condition} then target = 1;" + nl + "run;" + nl
        blocks, ctx, events = build(program)
        rules_data_step.apply(blocks[0], ctx, events)

        reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable" and edge["source"]["rule"] == "data_step_if_condition"]
        assert [
            (edge["from"].rsplit(".", 1)[-1], edge["operator"], edge["value"])
            for edge in reads
        ] == expected, condition


def test_boolean_and_in_condition_read_deduplication_uses_fact_keys():
    nl = chr(10)
    program = "data work.a;" + nl + "  set sdtm.ae;" + nl + "  if a > 1 and a < 5 then first_target = 1;" + nl + "  if x in (a, a) then second_target = 1;" + nl + "run;" + nl
    blocks, ctx, events = build(program)
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable" and edge["source"]["rule"] == "data_step_if_condition"]
    a_facts = [(edge["value"], edge["operator"]) for edge in reads if edge["from"].endswith(".a")]
    assert a_facts == [("1", ">"), ("5", "<"), (None, None)]
    x_reads = [edge for edge in reads if edge["from"].endswith(".x")]
    assert len(x_reads) == 1


def test_3000_term_if_where_and_assignment_chains_fall_back_unknown():
    nl = chr(10)
    condition_and = " and ".join(f"a > {i}" for i in range(3000))
    condition_or = " or ".join(f"a > {i}" for i in range(3000))
    rhs = " + ".join("a" for _ in range(3000))
    programs = (
        ("if", "if " + condition_and + " then target = 1;"),
        ("where", "where " + condition_or + ";"),
        ("rhs", "target = " + rhs + ";"),
    )
    for _label, statement in programs:
        program = "data work.a;" + nl + "  set sdtm.ae;" + nl + "  " + statement + nl + "run;" + nl
        blocks, ctx, events = build(program)
        rules_data_step.apply(blocks[0], ctx, events)
        assert any(finding["type"] == "unknown_expression" for finding in ctx.findings)


def test_hundred_term_boolean_condition_emits_all_reads():
    condition = " and ".join(f"x{i} > 0" for i in range(100))
    blocks, ctx, events = build(
        f"data work.a;\n  set sdtm.ae;\n  if {condition} then y = 1;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = {
        edge["from"].rsplit(".", 1)[-1]
        for edge in ctx.edges
        if edge["type"] == "reads_variable"
        and edge["source"]["rule"] == "data_step_if_condition"
    }
    assert reads == {f"x{i}" for i in range(100)}
    assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings)


def test_select_when_boolean_condition_emits_reads_and_conditioned_by_edges():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  select;\n"
        "    when (a > 1 or b < 2) y = z;\n"
        "  end;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert {edge["from"].rsplit(".", 1)[-1] for edge in ctx.edges if edge["type"] == "reads_variable"} == {"a", "b", "z"}
    assert {edge["from"].rsplit(".", 1)[-1] for edge in ctx.edges if edge["type"] == "conditioned_by"} == {"a", "b"}
    assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings)


def test_unsupported_conditions_stay_unknown_without_variable_reads():
    conditions = (
        "lag(x) > 1",
        "first.x",
        "last.x = 1",
        "x and y",
        "x AND y > 1",
        "a > 1 and b",
        "x = a or b",
        "x in (1:5)",
        "x in (&mv)",
        "x in (lag(x))",
        "x in ()",
        "x in (1,",
        "x in (1, 2",
        "x & y",
        "x > 1 & y",
        "x > 1 & y < 2",
        "x | y",
        "x > 1 | y < 2",
        "&mv",
        "first.x and y > 1",
        "lag(x) > 1 or y > 2",
        "x like 'A'",
        "x ?? 'A'",
        "x",
        "a - b >= 2",
        "a--b = 1",
        "sum(of a--c) > 1",
        "cats(x, best32.) = 'A'",
        "of > 1",
        "first = 'a'",
        "last = 'a'",
        "missing(a--b)",
        "missing(upcase(&mv))",
        "^missing(x)",
        "-x > 0",
        "-missing(x)",
        "_all_ > 1",
    )
    for condition in conditions:
        for statement_kind in ("if", "where"):
            statement = (
                f"if {condition} then y = 1;"
                if statement_kind == "if"
                else f"where {condition};"
            )
            blocks, ctx, events = build(
                "data work.a;\n  set sdtm.ae;\n"
                f"  {statement}\nrun;\n"
            )
            rules_data_step.apply(blocks[0], ctx, events)

            assert not any(
                edge["type"] in {"reads_variable", "conditioned_by"}
                for edge in ctx.edges
            ), (statement_kind, condition)
            assert [
                (finding["type"], finding["object"])
                for finding in ctx.findings
                if finding["type"] == "unknown_expression"
            ] == [("unknown_expression", condition)], (statement_kind, condition)


def test_if_compound_or_condition_emits_reads_without_garbled_literal():
    blocks, ctx, events = build(
        'data adam.adlb;\n  set sdtm.lb;\n'
        '  if aesdth = "Y" or upcase(strip(aeout)) = "FATAL" then aedthfl = "Y";'
        '\nrun;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [
        edge for edge in ctx.edges
        if edge["type"] == "reads_variable"
        and edge["source"]["rule"] == "data_step_if_condition"
    ]
    assert {
        edge["from"].rsplit(".", 1)[-1]: (edge["value"], edge["operator"])
        for edge in reads
    } == {"aesdth": ("Y", "="), "aeout": (None, None)}
    assert {edge["from"].rsplit(".", 1)[-1] for edge in ctx.edges if edge["type"] == "conditioned_by"} == {"aesdth", "aeout"}
    assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings)
    assert not any(
        node["type"] in {"Variable", "UnknownVariable"}
        and node["id"].rsplit(".", 1)[-1] in {"upcase", "strip"}
        for node in ctx.nodes
    )


def test_if_three_way_or_condition_emits_reads_and_conditioned_by_edges():
    blocks, ctx, events = build(
        'data adam.adlb;\n  set sdtm.lb;\n'
        '  if a = "Y" or b = "Y" or c = "Y" then d = "Y";\n'
        'run;\n'
    )
    rules_data_step.apply(blocks[0], ctx, events)

    reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
    assert {
        edge["from"].rsplit(".", 1)[-1]: (edge["value"], edge["operator"])
        for edge in reads
    } == {"a": ("Y", "="), "b": ("Y", "="), "c": ("Y", "=")}
    assert {edge["from"].rsplit(".", 1)[-1] for edge in ctx.edges if edge["type"] == "conditioned_by"} == {"a", "b", "c"}
    assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings)


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


def test_data_null_put_variables_do_not_get_unqualified_findings():
    blocks, ctx, events = build(
        "data _null_;\n  set sdtm.ae;\n  put 'a' x y;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(
        finding["type"] == "unqualified_variable_reference"
        for finding in ctx.findings
    )
    assert not any(node["type"] == "UnknownVariable" for node in ctx.nodes)
    assert not any(
        edge["source"]["rule"] == "data_step_put" for edge in ctx.edges
    )


def test_multi_output_put_is_exempt_but_assignment_still_gets_findings():
    blocks, ctx, events = build(
        "data work.a work.b;\n"
        "  set sdtm.ae;\n"
        "  put 'a' x y;\n"
        "  result = value;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    findings = [
        finding for finding in ctx.findings
        if finding["type"] == "unqualified_variable_reference"
    ]
    assert {finding["source"]["rule"] for finding in findings} == {
        "data_step_assignment"
    }
    assert {finding["object"] for finding in findings} == {"result", "value"}
    assert {finding["suggested_action"] for finding in findings} == {
        "Split the step so each output target has unambiguous variable "
        "references, or confirm the ambiguity is intentional."
    }
    assert not any(
        edge["source"]["rule"] == "data_step_put" for edge in ctx.edges
    )


def test_data_null_call_input_and_assignment_keep_unqualified_findings():
    blocks, ctx, events = build(
        "data _null_;\n"
        "  set sdtm.ae;\n"
        "  call symputx('flag', x);\n"
        "  input y;\n"
        "  result = value;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    findings = [
        finding for finding in ctx.findings
        if finding["type"] == "unqualified_variable_reference"
    ]
    assert {finding["source"]["rule"] for finding in findings} == {
        "data_step_call_symputx", "data_step_input", "data_step_assignment",
    }
    assert all(finding["suggested_action"] == (
        "Identify the input dataset that owns this variable, or confirm the "
        "unbound reference is intentional."
    ) for finding in findings)


def test_single_output_put_still_binds_reads_to_the_output():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  put y;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert any(
        e["type"] == "reads_variable" and e["source"]["rule"] == "data_step_put"
        for e in ctx.edges
    )
    assert not any(f["type"] == "unqualified_variable_reference" for f in ctx.findings)


def test_conditional_put_in_null_step_flags_only_the_condition_variable():
    blocks, ctx, events = build(
        "data _null_;\n"
        "  set sdtm.ae;\n"
        "  if x = 1 then put y;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    findings = [f for f in ctx.findings if f["type"] == "unqualified_variable_reference"]
    assert [f["object"] for f in findings] == ["x"]


def test_put_in_failed_single_target_step_emits_nothing():
    blocks, ctx, events = build(
        "data &out;\n"
        "  set sdtm.ae;\n"
        "  put y;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(f["type"] == "unqualified_variable_reference" for f in ctx.findings)
    assert not any(e["source"]["rule"] == "data_step_put" for e in ctx.edges)


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


def test_if_then_iterative_do_keeps_do_loop_wording():
    blocks, ctx, events = build(
        "data work.a;\n"
        "  set sdtm.ae;\n"
        "  if c then do i = 1 to 2;\n"
        "    x = y;\n"
        "  end;\n"
        "run;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    findings = [f for f in ctx.findings if f["type"] == "deferred_variable_construct"]
    assert len(findings) == 1
    assert "DO loop" in findings[0]["message"]
    assert "Guarded block" not in findings[0]["message"]


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
    assert [
        (finding["type"], finding["object"])
        for finding in ctx.findings
    ] == [("unknown_expression", "somefunc(i)")]


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


def test_whitelisted_assignment_calls_emit_derivations_and_argument_reads():
    cases = (
        ("upcase(x)", {"x"}),
        ("coalesce(a, b, 'z')", {"a", "b"}),
        ("substr(strip(x), 1, 3)", {"x"}),
        ("ifc(a = 'Y', 'u', 'v')", {"a"}),
    )
    for expression, expected_reads in cases:
        blocks, ctx, events = build(
            f"data work.a;\n  set sdtm.ae;\n  y = {expression};\nrun;\n"
        )
        rules_data_step.apply(blocks[0], ctx, events)

        reads = [edge for edge in ctx.edges if edge["type"] == "reads_variable"]
        assert {edge["from"] for edge in reads} == {
            f"variable:work.a.{name}" for name in expected_reads
        }, expression
        assert all(
            edge["value"] is None
            and edge["operator"] is None
            and edge["source"]["rule"] == "data_step_assignment"
            for edge in reads
        ), expression
        derives = [edge for edge in ctx.edges if edge["type"] == "derives"]
        assert len(derives) == 1, expression
        assert derives[0]["to"] == "variable:work.a.y"
        assert derives[0]["expression"] == expression
        assert not any(f["type"] == "unknown_expression" for f in ctx.findings)
        assert not any(
            node["type"] in {"Variable", "UnknownVariable"}
            and node["id"].rsplit(".", 1)[-1] in {
                "upcase", "coalesce", "substr", "strip", "ifc",
            }
            for node in ctx.nodes
        )


def test_off_whitelist_nested_call_stays_unknown_but_reads_arguments():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  y = upcase(lag(x));\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert {e["from"] for e in ctx.edges if e["type"] == "reads_variable"} == {
        "variable:work.a.x",
    }
    assert [
        (finding["type"], finding["object"])
        for finding in ctx.findings
        if finding["type"] == "unknown_expression"
    ] == [("unknown_expression", "upcase(lag(x))")]
    assert not any(
        node["type"] in {"Variable", "UnknownVariable"}
        and node["id"].endswith(".lag")
        for node in ctx.nodes
    )


def test_off_whitelist_calls_stay_unknown_on_assignment_rhs():
    for name in (
        "put", "input", "lag", "dif", "symget", "resolve", "rand",
        "ranuni", "today", "date", "time", "datetime", "dosubl",
        "prxchange", "somefunc",
    ):
        expression = f"{name}(x)"
        blocks, ctx, events = build(
            f"data work.a;\n  set sdtm.ae;\n  y = {expression};\nrun;\n"
        )
        rules_data_step.apply(blocks[0], ctx, events)

        assert [
            (finding["type"], finding["object"])
            for finding in ctx.findings
            if finding["type"] == "unknown_expression"
        ] == [("unknown_expression", expression)], name


def test_dotted_call_tokens_keep_legacy_unknown_reads():
    cases = (
        ("upcase(first.x)", {"x"}),
        ("upcase(last.x)", {"x"}),
        ("sum(a.b,c)", {"b", "c"}),
        ("upcase(work.x)", {"x"}),
    )
    for expression, expected_reads in cases:
        blocks, ctx, events = build(
            f"data work.a;\n  set sdtm.ae;\n  y = {expression};\nrun;\n"
        )
        rules_data_step.apply(blocks[0], ctx, events)

        reads = {
            edge["from"].rsplit(".", 1)[-1]
            for edge in ctx.edges if edge["type"] == "reads_variable"
        }
        assert reads == expected_reads, expression
        assert [
            finding["object"] for finding in ctx.findings
            if finding["type"] == "unknown_expression"
        ] == [expression]


def test_special_variable_lists_inside_calls_stay_unknown():
    for expression, expected_reads in (
        ("sum(_numeric_)", {"_numeric_"}),
        ("cats(_all_)", set()),
        ("cats(_character_)", {"_character_"}),
    ):
        blocks, ctx, events = build(
            f"data work.a;\n  set sdtm.ae;\n  y = {expression};\nrun;\n"
        )
        rules_data_step.apply(blocks[0], ctx, events)

        assert {
            edge["from"].rsplit(".", 1)[-1]
            for edge in ctx.edges if edge["type"] == "reads_variable"
        } == expected_reads
        assert [
            finding["object"] for finding in ctx.findings
            if finding["type"] == "unknown_expression"
        ] == [expression]


def test_ranges_and_format_tokens_stay_unknown_without_fake_reads():
    cases = (
        ("sum(of x1-x3)", {"x1", "x2", "x3"}),
        ("mean(of a1-a10)", {f"a{i}" for i in range(1, 11)}),
        ("sum(OF x1-x3)", {"x1", "x2", "x3"}),
        ("sum(x1--x3)", {"x1", "x3"}),
        ("sum(of a--c)", {"a", "c"}),
        ("cats(of _all_)", set()),
        ("cats(x, best32.)", {"x"}),
        ("cats(x, $char8.)", {"x"}),
        ("cats(x, yymmdd10.)", {"x"}),
        ("cats(x, z3.)", {"x"}),
    )
    for expression, expected_reads in cases:
        blocks, ctx, events = build(
            f"data work.a;\n  set sdtm.ae;\n  y = {expression};\nrun;\n"
        )
        rules_data_step.apply(blocks[0], ctx, events)

        reads = {
            edge["from"].rsplit(".", 1)[-1]
            for edge in ctx.edges if edge["type"] == "reads_variable"
        }
        assert reads == expected_reads, expression
        assert [
            (finding["type"], finding["object"])
            for finding in ctx.findings
            if finding["type"] == "unknown_expression"
        ] == [("unknown_expression", expression)]
        assert not any(
            node["type"] in {"Variable", "UnknownVariable"}
            and node["id"].rsplit(".", 1)[-1] in {"sum", "mean", "cats"}
            for node in ctx.nodes
        )


def test_plain_double_hyphen_keeps_subtraction_of_negative_behavior():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  y = x--z;\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert {
        edge["from"].rsplit(".", 1)[-1]
        for edge in ctx.edges if edge["type"] == "reads_variable"
    } == {"x", "z"}
    assert not any(finding["type"] == "unknown_expression" for finding in ctx.findings)


def test_macro_references_inside_calls_stay_unknown_with_existing_reads():
    cases = (
        ('upcase("&x")', set()),
        (
            "substr(a, &n, 1)",
            {"variable:work.a.a", "unknownvariable:n@3"},
        ),
    )
    for expression, expected_reads in cases:
        blocks, ctx, events = build(
            f"data work.a;\n  set sdtm.ae;\n  y = {expression};\nrun;\n"
        )
        rules_data_step.apply(blocks[0], ctx, events)

        assert {
            edge["from"]
            for edge in ctx.edges if edge["type"] == "reads_variable"
        } == expected_reads
        assert [
            finding["object"] for finding in ctx.findings
            if finding["type"] == "unknown_expression"
        ] == [expression]


def test_substr_pseudo_variable_assignment_keeps_current_noop_behavior():
    blocks, ctx, events = build(
        "data work.a;\n  set sdtm.ae;\n  substr(x,1,1)='Y';\nrun;\n"
    )
    rules_data_step.apply(blocks[0], ctx, events)

    assert not any(
        node["type"] in {"Variable", "UnknownVariable"} for node in ctx.nodes
    )
    assert not any(
        edge["type"] in {"reads_variable", "writes_variable", "derives"}
        for edge in ctx.edges
    )
    assert ctx.findings == []


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
    read = next(e for e in ctx.edges if e["type"] == "reads_variable")
    assert read["evidence"]["kind"] == "UNKNOWN"
    assert read["evidence"]["resolution"] == "UNRESOLVED"


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
    writes_by_variable = {
        edge["to"].rsplit(".", 1)[-1]: edge
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert writes_by_variable["single_note"]["evidence"]["kind"] == "OBSERVED"
    assert writes_by_variable["double_note"]["evidence"]["kind"] == "RESOLVED"
    assert writes_by_variable["bare_dose"]["evidence"]["kind"] == "RESOLVED"


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("data step rules: all checks passed")


if __name__ == "__main__":
    demo()
