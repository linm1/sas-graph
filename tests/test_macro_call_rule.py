"""Visible macro call rule (dev plan section 15.1-15.6, 21 Phase 4)."""

from pathlib import Path

import conftest  # noqa: F401

from sas_graph import rules_macro_call
from sas_graph import macro_index as macro_index_module
from sas_graph.blocks import group_blocks
from sas_graph.graph_model import GraphContext
from sas_graph.macro_contracts import load_macro_contracts
from sas_graph.macro_index import build_macro_index
from sas_graph.macro_state import walk_let_statements
from sas_graph.statements import split_statements

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def build(text, file_name="adae.sas"):
    result = split_statements(text, file_name)
    _, unattached = group_blocks(result.statements)
    events = walk_let_statements(result.statements)
    ctx = GraphContext(main_program=file_name, setup_file="setup.sas", run_id="r1")
    ctx.add_node("program:adae.sas", "Program", "adae.sas", source=None)
    return unattached, ctx, events


def test_source_template_precedes_a_matched_contract_when_source_is_unique():
    """A unique source body is stronger evidence than its optional contract."""
    macro_index = build_macro_index([FIXTURES / "basic_adae" / "macros"])
    macro_contracts = load_macro_contracts([FIXTURES / "qc_adae" / "contracts"])
    statements, ctx, events = build(
        "%gm_derive(inds=work.adae_srt, outds=adam.adae);\n"
    )

    rules_macro_call.apply(
        statements[0], ctx, events, macro_index, macro_contracts, "program:adae.sas"
    )

    edge_types = {(e["type"], e["from"], e["to"]) for e in ctx.edges}
    assert ("calls_macro", "program:adae.sas", "macrocall:001") in edge_types
    assert ("depends_on", "dataset:adam.adae", "dataset:work.adae_srt") in edge_types

    assert any(node["type"] == "MacroDefinition" for node in ctx.nodes)
    assert not any(node["type"] == "MacroContract" for node in ctx.nodes)


def _write_gm_contract_doc(directory, macro_name="gm_contract"):
    (directory / f"{macro_name}.md").write_text(
        f"# {macro_name}\n\n"
        "## Purpose\n\nMerges an input dataset into an output dataset.\n\n"
        "## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| inds | Input dataset. | LIBRARY.DATASET | REQUIRED |\n"
        "| outds | Output dataset. | LIBRARY.DATASET | REQUIRED |\n",
        encoding="utf-8",
    )


def test_matched_contract_creates_no_dataset_edges(tmp_path):
    """No role classification survives md parsing (wayfinder:
    bind-macro-calls-to-md-contracts.md) -- a matched contract records that
    the call matched a documented macro, and nothing more."""
    _write_gm_contract_doc(tmp_path)
    statements, ctx, events = build(
        "%gm_contract(inds=work.adae_srt, outds=adam.adae);\n"
    )

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([]),
        load_macro_contracts([tmp_path]), "program:adae.sas",
    )

    assert any(node["type"] == "MacroContract" for node in ctx.nodes)
    assert not any(
        edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"}
        for edge in ctx.edges
    )
    assert any(f["status"] == "SUPPORTED" for f in ctx.findings)


def test_matched_contract_parameter_names_are_case_insensitive(tmp_path):
    _write_gm_contract_doc(tmp_path)
    statements, ctx, events = build(
        "%GM_CONTRACT(INDS=work.adae_srt, OUTDS=adam.adae);\n"
    )

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([]),
        load_macro_contracts([tmp_path]), "program:adae.sas",
    )

    finding = next(f for f in ctx.findings if f["status"] == "SUPPORTED")
    assert "missing required" not in finding["message"]
    assert "not in the doc" not in finding["message"]


def test_call_with_no_source_and_no_contract_creates_unknown_macro():
    """Section 15.2's own example: %gm_missing has neither source nor contract."""
    macro_index = build_macro_index([FIXTURES / "basic_adae" / "macros"])
    macro_contracts = load_macro_contracts([FIXTURES / "basic_adae" / "contracts"])
    statements, ctx, events = build(
        "%gm_missing(inds=adam.adae, outds=&unknown_out.);\n"
    )

    rules_macro_call.apply(
        statements[0], ctx, events, macro_index, macro_contracts, "program:adae.sas"
    )

    unknown = next(n for n in ctx.nodes if n["type"] == "UnknownMacro")
    assert unknown["label"] == "gm_missing"
    assert any(f["status"] == "UNRESOLVED_MACRO_SOURCE" for f in ctx.findings)
    # No dependency is invented for a macro whose source is missing.
    assert all(e["type"] != "depends_on" for e in ctx.edges)


def test_contract_missing_required_parameter_is_reported_without_dataset_edges(tmp_path):
    _write_gm_contract_doc(tmp_path, "gm_required")
    statements, ctx, events = build("%gm_required();\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([]),
        load_macro_contracts([tmp_path]), "program:adae.sas",
    )

    finding = next(f for f in ctx.findings if f["status"] == "SUPPORTED")
    assert "missing required parameters: inds, outds" in finding["message"]
    assert not any(
        edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"}
        for edge in ctx.edges
    )


def test_contract_parameter_unknown_to_doc_is_reported(tmp_path):
    _write_gm_contract_doc(tmp_path, "gm_extra")
    statements, ctx, events = build(
        "%gm_extra(inds=sdtm.ae, outds=work.ae, notinthedoc=1);\n"
    )

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([]),
        load_macro_contracts([tmp_path]), "program:adae.sas",
    )

    finding = next(f for f in ctx.findings if f["status"] == "SUPPORTED")
    assert "parameters not in the doc: notinthedoc" in finding["message"]


def test_duplicate_macro_name_across_docs_is_unusable(tmp_path):
    _write_gm_contract_doc(tmp_path, "gm_duplicate")
    (tmp_path / "second.md").write_text(
        (tmp_path / "gm_duplicate.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    contracts = load_macro_contracts([tmp_path])
    statements, ctx, events = build("%gm_duplicate(inds=sdtm.ae, outds=work.ae);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([]), contracts, "program:adae.sas",
    )

    assert any("duplicate contracts" in error for error in contracts.get("gm_duplicate").errors)
    assert not any(node["type"] == "MacroContract" for node in ctx.nodes)
    assert any(f["status"] == "UNRESOLVED_MACRO_SOURCE" for f in ctx.findings)


def test_unresolved_macro_variable_in_parameter_reaches_unknown_dataset():
    """Section 22's own example: outds=&unknown_out. resolves nowhere."""
    macro_index = build_macro_index([FIXTURES / "basic_adae" / "macros"])
    macro_contracts = load_macro_contracts([FIXTURES / "basic_adae" / "contracts"])
    statements, ctx, events = build(
        "%gm_missing(inds=adam.adae, outds=&unknown_out.);\n"
    )

    rules_macro_call.apply(
        statements[0], ctx, events, macro_index, macro_contracts, "program:adae.sas"
    )

    unknown_dataset = next(n for n in ctx.nodes if n["type"] == "UnknownDataset")
    assert unknown_dataset["label"] == "&unknown_out."
    resolves_to = next(e for e in ctx.edges if e["type"] == "resolves_to")
    assert resolves_to["to"] == unknown_dataset["id"]
    assert any(f["status"] == "UNRESOLVED_MACRO_VARIABLE" for f in ctx.findings)


def test_return_and_abort_are_not_treated_as_macro_calls():
    """codex #5: B2's optional-paren regex now matches any bare `%name;`,
    including the SAS macro statements %RETURN/%ABORT -- these must stay
    reserved, exactly like %do/%end already are, or they wrongly become
    UnknownMacro calls."""
    assert not rules_macro_call.is_macro_call("%return;")
    assert not rules_macro_call.is_macro_call("%abort;")


def test_bare_call_without_parens_is_recognized_and_resolves_source(tmp_path):
    """B2: a parameterless call written `%name;` (no parens) must not be
    silently dropped -- it should produce a MacroCall and resolve against a
    known source exactly like the parenthesized zero-arg form does."""
    (tmp_path / "foo.sas").write_text(
        "%macro foo();\ndata work.out;\nset work.in;\nrun;\n%mend foo;\n",
        encoding="utf-8",
    )
    macro_index = build_macro_index([tmp_path])
    macro_contracts = load_macro_contracts([FIXTURES / "basic_adae" / "contracts"])
    statements, ctx, events = build("%foo;\n")

    assert rules_macro_call.is_macro_call(statements[0].text)

    rules_macro_call.apply(
        statements[0], ctx, events, macro_index, macro_contracts, "program:adae.sas"
    )

    assert any(n["type"] == "MacroCall" for n in ctx.nodes)
    assert not any(n["type"] == "UnknownMacro" for n in ctx.nodes)
    assert any(n["type"] == "MacroDefinition" for n in ctx.nodes)


def test_duplicate_macro_definition_creates_conflict_not_expansion(tmp_path):
    """Section 15.3's own scenario."""
    (tmp_path / "a.sas").write_text("%macro dup(x=);\n%mend dup;\n", encoding="utf-8")
    (tmp_path / "b.sas").write_text("%macro dup(y=);\n%mend dup;\n", encoding="utf-8")
    macro_index = build_macro_index([tmp_path])
    macro_contracts = load_macro_contracts([FIXTURES / "basic_adae" / "contracts"])
    statements, ctx, events = build("%dup(x=work.a);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, macro_index, macro_contracts, "program:adae.sas"
    )

    assert any(n["type"] == "MacroConflict" for n in ctx.nodes)
    assert any(f["status"] == "REQUIRES_DECISION" for f in ctx.findings)


def test_unique_source_without_contract_is_not_an_unknown_macro(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro Test(inds=, outds=);\n"
        "data &outds.; set &inds.; run;\n"
        "%mend Test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(inds=sdtm.ae, outds=work.adae_pre);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    definition = next(node for node in ctx.nodes if node["type"] == "MacroDefinition")
    source_file = next(node for node in ctx.nodes if node["type"] == "MacroSourceFile")
    assert definition["label"] == "Test"
    assert source_file["label"] == "test.sas"
    assert not any(node["type"] == "UnknownMacro" for node in ctx.nodes)
    assert any(
        edge["type"] == "implemented_by" and edge["to"] == definition["id"]
        for edge in ctx.edges
    )


def test_source_template_binds_data_dependencies_at_the_call_site(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(inds=, outds=);\n"
        "data &outds.; set &inds.; run;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(inds=sdtm.ae, outds=work.adae_pre);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    call_id = next(node["id"] for node in ctx.nodes if node["type"] == "MacroCall")
    edges = {(edge["type"], edge["from"], edge["to"]) for edge in ctx.edges}
    assert ("reads_dataset", call_id, "dataset:sdtm.ae") in edges
    assert ("writes_dataset", call_id, "dataset:work.adae_pre") in edges
    assert ("depends_on", "dataset:work.adae_pre", "dataset:sdtm.ae") in edges
    template_edges = [edge for edge in ctx.edges if edge.get("template_derived")]
    assert template_edges
    assert all(edge["call_source"]["file"] == "adae.sas" for edge in template_edges)
    assert all(edge["definition_source"]["file"].endswith("test.sas") for edge in template_edges)


def test_same_source_definition_binds_each_call_independently(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(inds=, outds=); data &outds.; set &inds.; run; %mend test;",
        encoding="utf-8",
    )
    statements, ctx, events = build(
        "%test(inds=sdtm.ae, outds=work.a);\n"
        "%test(inds=sdtm.lb, outds=work.b);\n"
    )
    index = build_macro_index([tmp_path])

    for statement in statements:
        rules_macro_call.apply(
            statement, ctx, events, index, load_macro_contracts([]), "program:adae.sas"
        )

    call_reads = {(edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "reads_dataset" and edge["from"].startswith("macrocall:")}
    assert call_reads == {("macrocall:001", "dataset:sdtm.ae"), ("macrocall:002", "dataset:sdtm.lb")}
    dependencies = {(edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"}
    assert {("dataset:work.a", "dataset:sdtm.ae"), ("dataset:work.b", "dataset:sdtm.lb")} <= dependencies


def test_source_template_reuses_sort_and_sql_rules(tmp_path):
    (tmp_path / "pipeline.sas").write_text(
        "%macro pipeline(inds=, srt=, outds=);\n"
        "proc sort data=&inds. out=&srt.; by usubjid; run;\n"
        "proc sql; create table &outds. as select * from &srt.; quit;\n"
        "%mend pipeline;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build(
        "%pipeline(inds=sdtm.ae, srt=work.ae_srt, outds=work.adae);\n"
    )

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    dependencies = {(edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"}
    assert {("dataset:work.ae_srt", "dataset:sdtm.ae"), ("dataset:work.adae", "dataset:work.ae_srt")} <= dependencies


def test_source_template_reuses_proc_import_rule(tmp_path):
    (tmp_path / "import_file.sas").write_text(
        "%macro import_file(datafile=, out=);\n"
        'proc import datafile="&datafile." out=&out. dbms=xlsx replace;\n'
        "run;\n"
        "%mend import_file;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build(
        "%import_file(datafile=lookups.xlsx, out=work.lookup);\n"
    )

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    edge = next(edge for edge in ctx.edges if edge["type"] == "reads_external_file")
    assert (edge["from"], edge["to"], edge["dbms"]) == (
        "externalfile:lookups.xlsx", "dataset:work.lookup", "xlsx",
    )
    assert edge["template_derived"] is True
    assert not any(
        finding["type"] == "macro_source_template_not_executed"
        for finding in ctx.findings
    )


def test_duplicate_source_definition_does_not_bind_a_template(tmp_path):
    (tmp_path / "a.sas").write_text(
        "%macro test(inds=, outds=); data &outds.; set &inds.; run; %mend test;",
        encoding="utf-8",
    )
    (tmp_path / "b.sas").write_text(
        "%macro test(inds=, outds=); data &outds.; set &inds.; run; %mend test;",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(inds=sdtm.ae, outds=work.a);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    assert any(node["type"] == "MacroConflict" for node in ctx.nodes)
    assert not any(edge.get("template_derived") for edge in ctx.edges)


def test_resolved_source_inequality_condition_is_bound(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(inds=, outds=);\n"
        "%if &inds. ne %then %do; data &outds.; set &inds.; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(inds=sdtm.ae, outds=work.a);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    assert any(finding["status"] == "STATIC_CONDITION_SUPPORTED" for finding in ctx.findings)
    assert ("dataset:work.a", "dataset:sdtm.ae") in {
        (edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"
    }


def test_source_template_names_and_parameters_are_case_insensitive(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro TeSt(InDs=, OuTdS=); data &OUTDS.; set &inds.; run; %mend TeSt;",
        encoding="utf-8",
    )
    statements, ctx, events = build("%TEST(INDS=sdtm.ae, outds=work.a);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    assert ("dataset:work.a", "dataset:sdtm.ae") in {
        (edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"
    }


def test_blank_explicit_source_default_is_not_bound_as_a_dataset(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(inds=, outds=); data &outds.; set &inds.; run; %mend test;",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(inds=sdtm.ae);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    assert any(finding["status"] == "NOT_EXECUTED" for finding in ctx.findings)
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)
    assert not any(node["id"] == "dataset:work." for node in ctx.nodes)


def test_source_template_value_resolved_blank_is_not_bound(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro t(inds=, outds=&empty); data &outds.; set &inds.; run; %mend t;",
        encoding="utf-8",
    )
    statements, ctx, events = build(
        "%let empty=;\n"
        "%t(inds=sdtm.ae);\n"
        "%t(inds=sdtm.ae, outds=&empty);\n"
    )

    for statement in statements[1:]:
        rules_macro_call.apply(
            statement, ctx, events, build_macro_index([tmp_path]),
            load_macro_contracts([]), "program:adae.sas",
        )

    assert len([finding for finding in ctx.findings if finding["status"] == "NOT_EXECUTED"]) == 2
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)
    assert not any(node["id"] == "dataset:work." for node in ctx.nodes)


def test_source_template_resolves_external_macro_variables_at_the_call(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro t(outds=); data &outds.; set &glob.; run; %mend t;",
        encoding="utf-8",
    )
    statements, ctx, events = build(
        "%let GLOB=sdtm.ae;\n"
        "%t(outds=work.a);\n"
    )

    rules_macro_call.apply(
        statements[1], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    assert ("dataset:work.a", "dataset:sdtm.ae") in {
        (edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"
    }


def test_unresolved_external_template_variable_is_not_bound(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro t(outds=); data &outds.; set &glob.; run; %mend t;",
        encoding="utf-8",
    )
    statements, ctx, events = build("%t(outds=work.a);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    assert any(finding["status"] == "NOT_EXECUTED" for finding in ctx.findings)
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_unterminated_source_template_block_is_not_bound(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(inds=, outds=); data &outds.; set &inds.; %mend test;",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(inds=sdtm.ae, outds=work.a);\n")

    rules_macro_call.apply(
        statements[0], ctx, events, build_macro_index([tmp_path]),
        load_macro_contracts([]), "program:adae.sas",
    )

    assert any(finding["status"] == "NOT_EXECUTED" for finding in ctx.findings)
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_source_template_is_parsed_once_and_bound_for_each_call(tmp_path, monkeypatch):
    (tmp_path / "test.sas").write_text(
        "%macro test(inds=, outds=); data &outds.; set &inds.; run; %mend test;",
        encoding="utf-8",
    )
    split_calls = 0
    original_split = macro_index_module.split_statements

    def count_split(*args, **kwargs):
        nonlocal split_calls
        split_calls += 1
        return original_split(*args, **kwargs)

    monkeypatch.setattr(macro_index_module, "split_statements", count_split)
    index = build_macro_index([tmp_path])
    statements, ctx, events = build(
        "%test(inds=sdtm.ae, outds=work.a);\n"
        "%test(inds=sdtm.lb, outds=work.b);\n"
    )

    for statement in statements:
        rules_macro_call.apply(
            statement, ctx, events, index, load_macro_contracts([]), "program:adae.sas"
        )

    assert split_calls == 1
    assert {("dataset:work.a", "dataset:sdtm.ae"), ("dataset:work.b", "dataset:sdtm.lb")} <= {
        (edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"
    }


def test_macro_conditional_is_not_executed():
    """Section 15.8: %if/%do is not statically executed in this phase."""
    macro_index = build_macro_index([])
    macro_contracts = load_macro_contracts([])
    statements, ctx, events = build("%if &domain. = ae %then %do;\n")

    rules_macro_call.apply(
        statements[0], ctx, events, macro_index, macro_contracts, "program:adae.sas"
    )

    assert any(f["status"] == "NOT_EXECUTED" for f in ctx.findings)
    # No MacroCall/MacroParameter node is created for an unexecuted conditional.
    assert not any(n["type"] in ("MacroCall", "MacroParameter") for n in ctx.nodes)


def test_source_template_selects_resolved_true_and_false_branches(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(mode=, outds=);\n"
        "%if &mode. = yes %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%else %do; data &outds.; set sdtm.lb; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build(
        "%test(mode=yes, outds=work.a);\n%test(mode=no, outds=work.b);\n"
    )
    index = build_macro_index([tmp_path])
    for statement in statements:
        rules_macro_call.apply(statement, ctx, events, index, load_macro_contracts([]), "program:adae.sas")

    assert {("dataset:work.a", "dataset:sdtm.ae"), ("dataset:work.b", "dataset:sdtm.lb")} <= {
        (edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"
    }
    assert any(finding["status"] == "STATIC_CONDITION_SUPPORTED" for finding in ctx.findings)


def test_unresolved_source_condition_keeps_candidate_branches_without_edges(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(mode=, outds=);\n"
        "%if &mode. = yes %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%else %do; data &outds.; set sdtm.lb; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(outds=work.a);\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert {"CONDITIONAL_BRANCH_UNRESOLVED", "NOT_EXECUTED"} <= {f["status"] for f in ctx.findings}
    assert len([node for node in ctx.nodes if node["type"] == "ConditionalBranch"]) == 2
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_unsupported_source_condition_is_not_executed(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(outds=);\n"
        "%if %sysfunc(exist(sdtm.ae)) %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(outds=work.a);\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert any(finding["status"] == "NOT_EXECUTED" for finding in ctx.findings)
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_source_template_binds_small_literal_integer_loop(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test();\n"
        "%do i=1 %to 2; data work.out&i.; set sdtm.in&i.; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test();\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert {("dataset:work.out1", "dataset:sdtm.in1"), ("dataset:work.out2", "dataset:sdtm.in2")} <= {
        (edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"
    }
    assert any(finding["status"] == "STATIC_LOOP_SUPPORTED" for finding in ctx.findings)


def test_large_or_invalid_literal_loop_is_not_executed(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(); %do i=1 %to 99; data work.out&i.; set sdtm.in&i.; run; %end; %mend test;",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test();\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert any(node["type"] == "MacroLoop" for node in ctx.nodes)
    assert any(finding["status"] == "NOT_EXECUTED" for finding in ctx.findings)
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_descending_literal_loop_is_not_executed(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(); %do i=3 %to 1; data work.out&i.; set sdtm.in&i.; run; %end; %mend test;",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test();\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert any(finding["status"] == "NOT_EXECUTED" for finding in ctx.findings)
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_dynamic_loop_bounds_remain_not_executed_after_call_substitution(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(lo=, hi=); %do i=&lo. %to &hi.; data work.out&i.; set sdtm.in&i.; run; %end; %mend test;",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(lo=1, hi=2);\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert any(finding["status"] == "NOT_EXECUTED" for finding in ctx.findings)
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_recognized_static_list_loop_uses_only_a_literal_list(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(list=);\n"
        "%do i=1 %to %sysfunc(countw(&list.));\n"
        "%let item=%scan(&list., &i.);\n"
        "data work.out_&item.; set sdtm.&item.; run;\n"
        "%end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(list=ae lb);\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert {("dataset:work.out_ae", "dataset:sdtm.ae"), ("dataset:work.out_lb", "dataset:sdtm.lb")} <= {
        (edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"
    }
    assert any(finding["status"] == "STATIC_LIST_LOOP_SUPPORTED" for finding in ctx.findings)


def test_dynamic_sysfunc_or_scan_list_is_not_evaluated(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(list=);\n"
        "%do i=1 %to %sysfunc(countw(&list.));\n"
        "%let item=%scan(&list., &i.);\n"
        "data work.out_&item.; set sdtm.&item.; run;\n"
        "%end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(list=%sysfunc(getoption(work)));\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert any(finding["status"] == "NOT_EXECUTED" for finding in ctx.findings)
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_condition_with_macro_function_operand_is_not_compared_as_text(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(mode=, outds=);\n"
        "%if &mode. = %sysfunc(foo) %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%else %do; data &outds.; set sdtm.lb; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(mode=%sysfunc(foo), outds=work.a);\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert {"CONDITIONAL_BRANCH_UNRESOLVED", "NOT_EXECUTED"} <= {f["status"] for f in ctx.findings}
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_condition_with_indirect_macro_reference_is_not_compared_as_text(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(mode=, outds=);\n"
        "%if &&mode. = yes %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%else %do; data &outds.; set sdtm.lb; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(mode=yes, outds=work.a);\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert any(node["type"] == "MacroConditional" for node in ctx.nodes)
    assert {"CONDITIONAL_BRANCH_UNRESOLVED", "NOT_EXECUTED"} <= {f["status"] for f in ctx.findings}
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_compound_condition_is_not_squeezed_into_a_scalar_comparison(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(a=, b=, outds=);\n"
        "%if &a. = yes and &b. = y %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%else %do; data &outds.; set sdtm.lb; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(a=yes, b=y, outds=work.a);\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert {"CONDITIONAL_BRANCH_UNRESOLVED", "NOT_EXECUTED"} <= {f["status"] for f in ctx.findings}
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_else_if_chain_is_not_treated_as_an_unconditional_else(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(mode=, outds=);\n"
        "%if &mode. = ae %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%else %if &mode. = lb %then %do; data &outds.; set sdtm.lb; run; %end;\n"
        "%else %do; data &outds.; set sdtm.dm; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build("%test(mode=lb, outds=work.a);\n")
    rules_macro_call.apply(statements[0], ctx, events, build_macro_index([tmp_path]), load_macro_contracts([]), "program:adae.sas")

    assert {"CONDITIONAL_BRANCH_UNRESOLVED", "NOT_EXECUTED"} <= {f["status"] for f in ctx.findings}
    assert not any(edge["type"] in {"reads_dataset", "writes_dataset", "depends_on"} for edge in ctx.edges)


def test_conditional_uses_case_insensitive_prior_let_values_per_call(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(MODE=, outds=);\n"
        "%if &mode. eq YES %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%else %do; data &outds.; set sdtm.lb; run; %end;\n"
        "%mend test;\n",
        encoding="utf-8",
    )
    statements, ctx, events = build(
        "%let Mode=YES;\n"
        "%test(outds=work.a, mode=&MODE.);\n"
        "%let mode=no;\n"
        "%test(outds=work.b, mode=&mode.);\n"
    )
    index = build_macro_index([tmp_path])
    for statement in statements:
        if rules_macro_call.is_macro_call(statement.text):
            rules_macro_call.apply(statement, ctx, events, index, load_macro_contracts([]), "program:adae.sas")

    assert {("dataset:work.a", "dataset:sdtm.ae"), ("dataset:work.b", "dataset:sdtm.lb")} <= {
        (edge["from"], edge["to"]) for edge in ctx.edges if edge["type"] == "depends_on"
    }


def test_calls_at_the_same_local_statement_order_in_different_files_do_not_collide():
    """Regression: run_pipeline.py parses setup.sas and the main program as
    two separate split_statements() passes, so their statement_order both
    restart at 1. A macro call id keyed on statement_order alone would
    collide and silently drop one call's node from the graph."""
    macro_index = build_macro_index([])
    macro_contracts = load_macro_contracts([])

    setup_statements, ctx, events = build("%gm_a(x=1);\n", file_name="setup.sas")
    rules_macro_call.apply(
        setup_statements[0], ctx, events, macro_index, macro_contracts, "program:adae.sas"
    )
    main_statements, _, main_events = build("%gm_b(x=2);\n", file_name="adae.sas")
    rules_macro_call.apply(
        main_statements[0], ctx, main_events, macro_index, macro_contracts, "program:adae.sas"
    )

    calls = [n for n in ctx.nodes if n["type"] == "MacroCall"]
    assert len(calls) == 2
    assert {c["macro_name"] for c in calls} == {"gm_a", "gm_b"}


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            import inspect

            if len(inspect.signature(value).parameters) == 0:
                value()
                print(f"ok  {name}")
    print("macro call rule: all checks passed (tmp_path-dependent tests need pytest)")


if __name__ == "__main__":
    demo()
