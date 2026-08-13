"""End-to-end `run` against the section 22 fixture (dev plan section 21 Phase 4).

Structural assertions only, not a JSON diff against `examples/hand_written_graph.json`
-- the advisor's own finding during design: that fixture's `statement_order`
values are pre-`statements.py` unit indices (1 per DATA/PROC block), not the
real per-statement numbering `split_statements` produces. The two are not
byte-comparable; this test instead proves the *shape* (node/edge types, run
status, finding statuses) and that both renderers accept the generated graph.
"""

import tempfile
from pathlib import Path

import conftest  # noqa: F401
import pytest

from sas_graph.config import load_config
from sas_graph.cli import _run
from sas_graph.graph_io import load_graph, save_graph
from sas_graph.renderer_findings import render as render_findings
from sas_graph.renderer_mermaid import render as render_mermaid
from sas_graph.run_pipeline import run

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "basic_adae" / "project.yaml"


def _write_project(tmp_path, setup_text, main_text, extra_config=""):
    (tmp_path / "setup.sas").write_text(setup_text, encoding="utf-8")
    (tmp_path / "main.sas").write_text(main_text, encoding="utf-8")
    config = tmp_path / "project.yaml"
    config.write_text(
        "main_program: main.sas\n"
        "setup_file: setup.sas\n"
        "allowed_roots:\n"
        "  - .\n"
        "output_dir: graph_runs\n"
        + extra_config,
        encoding="utf-8",
    )
    result = load_config(config)
    assert result.ok
    return result


def _write_multi_program_project(tmp_path, setup_text, programs, extra_config=""):
    """Like `_write_project`, but `programs` is `{filename: text}` for N
    declared main programs, written as a YAML list under `main_program:` in
    insertion order."""
    (tmp_path / "setup.sas").write_text(setup_text, encoding="utf-8")
    for name, text in programs.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    program_list = "".join(f"  - {name}\n" for name in programs)
    config = tmp_path / "project.yaml"
    config.write_text(
        f"main_program:\n{program_list}"
        "setup_file: setup.sas\n"
        "allowed_roots:\n"
        "  - .\n"
        "output_dir: graph_runs\n"
        + extra_config,
        encoding="utf-8",
    )
    result = load_config(config)
    assert result.ok
    return result


def _macro_names(graph):
    return [node["macro_name"] for node in graph["nodes"] if node["type"] == "MacroCall"]


def test_utf8_bom_sources_keep_setup_main_include_and_macro_lineage(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (tmp_path / "setup.sas").write_bytes(
        b"\xef\xbb\xbfdata work.setup;\nset sdtm.dm;\nrun;\n"
    )
    (tmp_path / "included.sas").write_bytes(
        b"\xef\xbb\xbfdata work.included;\nset sdtm.ae;\nrun;\n"
    )
    (macros / "source.sas").write_bytes(
        b"\xef\xbb\xbf%macro source(inds=, outds=);\n"
        b"data &outds.;\nset &inds.;\nrun;\n%mend source;\n"
    )
    (tmp_path / "main.sas").write_bytes(
        b'\xef\xbb\xbf%include "included.sas";\n'
        b"data work.main;\nset work.included;\nrun;\n"
        b"%source(inds=work.main, outds=work.final);\n"
    )
    config = tmp_path / "project.yaml"
    config.write_text(
        "main_program: main.sas\nsetup_file: setup.sas\nallowed_roots:\n  - .\n"
        "output_dir: graph_runs\nmacro_roots:\n  - macros\n",
        encoding="utf-8",
    )

    graph = run(load_config(config), run_id="utf8-bom")

    assert graph["run_status"] == "COMPLETE"
    assert {node["id"] for node in graph["nodes"] if node["type"] == "Dataset"} >= {
        "dataset:work.setup", "dataset:sdtm.dm", "dataset:work.included",
        "dataset:sdtm.ae", "dataset:work.main", "dataset:work.final",
    }
    assert any(node["type"] == "MacroDefinition" and node["label"] == "source" for node in graph["nodes"])


def test_plain_utf8_source_behavior_is_unchanged(tmp_path):
    graph = run(
        _write_project(tmp_path, "data work.setup;\nset sdtm.dm;\nrun;\n", ""),
        run_id="plain-utf8",
    )

    assert graph["run_status"] == "COMPLETE"
    assert {node["id"] for node in graph["nodes"] if node["type"] == "Dataset"} == {
        "dataset:work.setup", "dataset:sdtm.dm"
    }


def test_missing_quit_with_identifiable_sql_recovers_complete_and_is_rendered(tmp_path):
    graph = run(
        _write_project(
            tmp_path, "", "proc sql;\ncreate table work.a as select * from sdtm.ae;\n"
        ),
        run_id="sql-eof-recovery",
    )

    finding = next(f for f in graph["findings"] if f["type"] == "sql_block_not_explicitly_closed")
    # PARTIAL, not COMPLETE: `select *` is genuinely unresolved at the
    # column-variable level (wayfinder: sql-select-alias-variable-edges) even
    # though this test's own subject -- missing-QUIT recovery -- still fully
    # succeeds, per the still-SUPPORTED/INFORMATION assertion right below.
    assert graph["run_status"] == "PARTIAL"
    assert (finding["status"], finding["severity"]) == ("SUPPORTED", "INFORMATION")
    with tempfile.TemporaryDirectory() as tmp:
        loaded = load_graph(save_graph(graph, Path(tmp) / "graph.json"))
    assert any(f["id"] == finding["id"] for f in loaded["findings"])
    assert "UNRECOGNISED STATUS" not in render_findings(loaded)


def test_static_macro_control_flow_writes_complete_findings_without_unknown_statuses(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "condition.sas").write_text(
        "%macro condition(mode=, outds=);\n"
        "%if &mode. = yes %then %do; data &outds.; set sdtm.ae; run; %end;\n"
        "%mend condition;\n",
        encoding="utf-8",
    )
    (macros / "loop.sas").write_text(
        "%macro loop();\n"
        "%do i=1 %to 2; data work.out&i.; set sdtm.in&i.; run; %end;\n"
        "%mend loop;\n",
        encoding="utf-8",
    )
    (macros / "list_loop.sas").write_text(
        "%macro list_loop(list=);\n"
        "%do i=1 %to %sysfunc(countw(&list.));\n"
        "%let item=%scan(&list., &i.);\n"
        "data work.out_&item.; set sdtm.&item.; run;\n"
        "%end;\n"
        "%mend list_loop;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "%condition(mode=yes, outds=work.condition);\n%loop();\n%list_loop(list=ae lb);\n",
        "macro_roots:\n  - macros\n",
    )

    assert _run(result.config_path) == 0

    run_dir = next((tmp_path / "graph_runs" / "runs").iterdir())
    graph = load_graph(run_dir / "graph.json")
    findings = (run_dir / "findings.md").read_text(encoding="utf-8")
    assert graph["run_status"] == "COMPLETE"
    assert {"STATIC_CONDITION_SUPPORTED", "STATIC_LOOP_SUPPORTED", "STATIC_LIST_LOOP_SUPPORTED"} <= {
        finding["status"] for finding in graph["findings"]
    }
    assert "UNRECOGNISED STATUS" not in findings


def test_missing_quit_recovers_at_data_and_proc_boundaries_then_parses_them(tmp_path):
    graph = run(
        _write_project(
            tmp_path,
            "",
            "proc sql;\ncreate table work.a as select * from sdtm.ae;\n"
            "data work.b;\nset work.a;\nrun;\n"
            "proc sql;\ncreate table work.c as select * from work.b;\n"
            "proc sort data=work.c;\nby usubjid;\nrun;\n",
        ),
        run_id="sql-boundary-recovery",
    )

    # PARTIAL, not COMPLETE: two `select *` statements, each unresolved at
    # the column-variable level (wayfinder: sql-select-alias-variable-edges).
    assert graph["run_status"] == "PARTIAL"
    assert len([f for f in graph["findings"] if f["type"] == "sql_block_not_explicitly_closed"]) == 2
    assert {node["id"] for node in graph["nodes"] if node["type"] == "Dataset"} >= {
        "dataset:work.a", "dataset:work.b", "dataset:work.c"
    }
    assert any(node.get("step_kind") == "PROC_SORT" for node in graph["nodes"])


def test_missing_quit_without_identifiable_from_remains_partial(tmp_path):
    graph = run(
        _write_project(tmp_path, "", "proc sql;\ncreate table work.a as select 1;\n"),
        run_id="sql-ambiguous-recovery",
    )

    finding = next(f for f in graph["findings"] if f["type"] == "sql_block_not_explicitly_closed")
    assert graph["run_status"] == "PARTIAL"
    assert (finding["status"], finding["severity"]) == ("NOT_EXECUTED", "WARNING")


def test_missing_quit_join_without_from_is_ambiguous_not_recovered(tmp_path):
    graph = run(
        _write_project(
            tmp_path,
            "",
            "proc sql;\ncreate table work.a as select * join sdtm.ae on 1=1;\n",
        ),
        run_id="sql-join-without-from",
    )

    finding = next(f for f in graph["findings"] if f["type"] == "sql_block_not_explicitly_closed")
    assert graph["run_status"] == "PARTIAL"
    assert (finding["status"], finding["severity"]) == ("NOT_EXECUTED", "WARNING")
    assert "dataset:sdtm.ae" not in {node["id"] for node in graph["nodes"]}


def test_missing_quit_multiple_identifiable_sql_statements_stays_complete(tmp_path):
    graph = run(
        _write_project(
            tmp_path,
            "",
            "proc sql;\n"
            "create table work.a as select * from sdtm.ae;\n"
            "create view work.b as select * from work.a;\n",
        ),
        run_id="sql-multiple-recovered",
    )

    # PARTIAL, not COMPLETE: both `select *` statements are unresolved at the
    # column-variable level (wayfinder: sql-select-alias-variable-edges).
    assert graph["run_status"] == "PARTIAL"
    assert next(f for f in graph["findings"] if f["type"] == "sql_block_not_explicitly_closed")["status"] == "SUPPORTED"


def test_missing_quit_ambiguous_statement_after_recovered_statement_is_partial(tmp_path):
    graph = run(
        _write_project(
            tmp_path,
            "",
            "proc sql;\n"
            "create table work.a as select * from sdtm.ae;\n"
            "create table work.b as select 1;\n",
        ),
        run_id="sql-ambiguous-last",
    )

    assert graph["run_status"] == "PARTIAL"
    assert next(f for f in graph["findings"] if f["type"] == "sql_block_not_explicitly_closed")["status"] == "NOT_EXECUTED"


def test_missing_quit_ambiguous_statement_before_recovered_statement_is_partial(tmp_path):
    graph = run(
        _write_project(
            tmp_path,
            "",
            "proc sql;\n"
            "create table work.a as select 1;\n"
            "create table work.b as select * from sdtm.ae;\n",
        ),
        run_id="sql-ambiguous-first",
    )

    assert graph["run_status"] == "PARTIAL"
    assert next(f for f in graph["findings"] if f["type"] == "sql_block_not_explicitly_closed")["status"] == "NOT_EXECUTED"


def test_missing_quit_join_after_from_is_supported_lineage(tmp_path):
    graph = run(
        _write_project(
            tmp_path,
            "",
            "proc sql;\n"
            "create table work.a as select * from sdtm.ae "
            "join sdtm.dm on ae.usubjid=dm.usubjid;\n",
        ),
        run_id="sql-join-after-from",
    )

    # PARTIAL, not COMPLETE: `select *` is unresolved at the column-variable
    # level (wayfinder: sql-select-alias-variable-edges) even though the
    # dataset-level lineage this assertion checks is fully resolved.
    assert graph["run_status"] == "PARTIAL"
    assert {edge["from"] for edge in graph["edges"] if edge["type"] == "reads_dataset"} == {
        "dataset:sdtm.ae", "dataset:sdtm.dm"
    }


def test_missing_quit_quoted_from_literals_are_not_recovered_or_sources(tmp_path):
    literals = (
        '"from sdtm.double"',
        "'from sdtm.single'",
        '"from ""sdtm.doubled"""',
    )

    for index, literal in enumerate(literals):
        graph = run(
            _write_project(
                tmp_path,
                "",
                f"proc sql;\ncreate table work.a{index} as select {literal} as label;\n",
            ),
            run_id=f"sql-quoted-from-{index}",
        )

        finding = next(f for f in graph["findings"] if f["type"] == "sql_block_not_explicitly_closed")
        assert graph["run_status"] == "PARTIAL"
        assert finding["status"] == "NOT_EXECUTED"
        assert not any(node["id"].startswith("dataset:sdtm.") for node in graph["nodes"])


def test_missing_quit_quoted_join_after_real_from_does_not_add_fake_source(tmp_path):
    sql = 'create table work.a as select "join sdtm.fake" as label from sdtm.ae;'
    graph = run(
        _write_project(tmp_path, "", f"proc sql;\n{sql}\n"),
        run_id="sql-quoted-join",
    )

    assert graph["run_status"] == "COMPLETE"
    assert {edge["from"] for edge in graph["edges"] if edge["type"] == "reads_dataset"} == {
        "dataset:sdtm.ae"
    }
    statement = next(node for node in graph["nodes"] if node["type"] == "SqlStatement")
    assert statement["source"]["original_text"] == sql


def test_source_template_before_merge_updates_sort_state_before_prefix_check(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "%sortit(inds=work.left);\n"
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-before-merge")

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_source_template_after_merge_does_not_retroactively_update_prefix_check(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n"
        "%sortit(inds=work.left);\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-after-merge")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_source_template_inside_data_before_merge_updates_prefix_check(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "data work.out;\n"
        "%sortit(inds=work.left);\n"
        "merge work.left;\nby actual;\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-inside-before-merge")

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_source_template_inside_data_after_merge_cannot_affect_prefix_check(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "data work.out;\n"
        "merge work.left;\nby actual;\n"
        "%sortit(inds=work.left);\n"
        "run;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-inside-after-merge")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_later_ordinary_sort_supersedes_template_sort_for_merge_prefix(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "%sortit(inds=work.left);\n"
        "proc sort data=work.left;\nby actual;\nrun;\n"
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="ordinary-sort-after-template")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_later_template_sort_supersedes_ordinary_sort_for_merge_prefix(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby actual;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "proc sort data=work.left;\nby expected;\nrun;\n"
        "%sortit(inds=work.left);\n"
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-sort-after-ordinary")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_latest_of_two_ordinary_sorts_drives_merge_prefix(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "proc sort data=work.left;\nby expected;\nrun;\n"
        "proc sort data=work.left;\nby actual;\nrun;\n"
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n",
    )

    graph = run(result, run_id="two-ordinary-sorts")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_post_merge_ordinary_sort_does_not_retroactively_supply_prefix_evidence(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n"
        "proc sort data=work.left;\nby expected;\nrun;\n",
    )

    graph = run(result, run_id="ordinary-sort-after-merge")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_setup_template_sort_remains_prior_to_main_later_template_sort(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=, by=);\n"
        "proc sort data=&inds.;\nby &by.;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "%sortit(inds=work.left, by=expected);\n",
        "data work.out;\nmerge work.left;\nby actual;\n"
        "%sortit(inds=work.left, by=actual);\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="setup-template-prior")

    finding = next(f for f in graph["findings"] if f["type"] == "merge_by_not_prefix_of_sort_by")
    assert finding["source"]["statement_order"] == 3
    assert finding["source"]["original_text"] == "by actual;"


def test_setup_ordinary_sort_remains_prior_to_main_later_template_sort(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=, by=);\n"
        "proc sort data=&inds.;\nby &by.;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "proc sort data=work.left;\nby expected;\nrun;\n",
        "data work.out;\nmerge work.left;\nby actual;\n"
        "%sortit(inds=work.left, by=actual);\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="setup-ordinary-prior")

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_setup_template_sort_remains_prior_to_main_later_ordinary_sort(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "%sortit(inds=work.left);\n",
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n"
        "proc sort data=work.left;\nby actual;\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="setup-template-before-main-ordinary")

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_template_sort_between_merge_and_by_cannot_affect_merge_prefix(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "data work.out;\nmerge work.left;\n"
        "%sortit(inds=work.left);\n"
        "by actual;\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-between-merge-and-by")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_template_sort_before_merge_supplies_prefix_evidence(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "data work.out;\n%sortit(inds=work.left);\n"
        "merge work.left;\nby actual;\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-before-merge-causal-order")

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_uncalled_macro_sort_does_not_affect_later_merge(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "%macro unused();\n"
        "proc sort data=work.left;\nby expected;\nrun;\n"
        "%mend unused;\n"
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n",
    )

    graph = run(result, run_id="uncalled-macro-sort")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_uncalled_macro_let_does_not_resolve_later_source(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "%macro unused();\n%let domain = bad;\n%mend unused;\n"
        "data work.&domain._out;\nset raw.&domain.;\nrun;\n",
    )

    graph = run(result, run_id="uncalled-macro-let")

    assert any(f["status"] == "UNRESOLVED_MACRO_VARIABLE" for f in graph["findings"])
    assert not any(n["id"] == "dataset:work.bad_out" for n in graph["nodes"])


def test_uncalled_macro_data_and_sql_create_no_runtime_steps(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "%macro unused();\n"
        "data work.hidden;\nset sdtm.ae;\nrun;\n"
        "proc sql;\ncreate table work.hidden_sql as select * from sdtm.dm;\nquit;\n"
        "%mend unused;\n",
    )

    graph = run(result, run_id="uncalled-macro-body")

    assert not any(n["type"] in {"Step", "SqlBlock", "SqlStatement"} for n in graph["nodes"])


def test_nested_uncalled_macro_body_stays_excluded_until_outer_mend(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "%macro outer();\n"
        "%macro inner();\n%mend inner;\n"
        "%let domain = bad;\n"
        "data work.hidden;\nset sdtm.ae;\nrun;\n"
        "proc sort data=work.left;\nby expected;\nrun;\n"
        "%mend outer;\n"
        "data work.&domain._out;\nset raw.&domain.;\nrun;\n",
    )

    graph = run(result, run_id="nested-uncalled-macro")

    assert len([n for n in graph["nodes"] if n["type"] == "Step"]) == 1
    assert not any(n["id"] in {"dataset:work.hidden", "dataset:work.bad_out"} for n in graph["nodes"])
    assert any(f["status"] == "UNRESOLVED_MACRO_VARIABLE" for f in graph["findings"])


def test_definition_comments_are_excluded_but_post_mend_comments_from_includes_remain(tmp_path):
    (tmp_path / "defs.sas").write_text(
        "%macro outer();\n"
        "/* data work.hidden; set sdtm.ae; run; */\n"
        "%mend outer; /* data work.after_mend; set sdtm.dm; run; */\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        '%include "defs.sas";\n'
        "/* data work.parent_comment; set sdtm.lb; run; */\n",
    )

    graph = run(result, run_id="definition-comment-span")

    comments = [n for n in graph["nodes"] if n["type"] == "CommentBlock"]
    assert {(n["source"]["file"], n["source"]["original_text"]) for n in comments} == {
        ("defs.sas", "/* data work.after_mend; set sdtm.dm; run; */"),
        ("main.sas", "/* data work.parent_comment; set sdtm.lb; run; */"),
    }


def test_definition_span_does_not_hide_comment_in_same_named_included_file(tmp_path):
    (tmp_path / "left").mkdir()
    (tmp_path / "right").mkdir()
    (tmp_path / "left" / "common.sas").write_text(
        "%macro outer();\n"
        "data work.hidden;\nset sdtm.ae;\nrun;\n"
        "%mend outer;\n",
        encoding="utf-8",
    )
    (tmp_path / "right" / "common.sas").write_text(
        "%let keep = 1; /* data work.retained; set sdtm.dm; run; */\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        '%include "left/common.sas";\n%include "right/common.sas";\n',
    )

    graph = run(result, run_id="same-basename-definition-comment")

    assert [n["source"]["original_text"] for n in graph["nodes"] if n["type"] == "CommentBlock"] == [
        "/* data work.retained; set sdtm.dm; run; */"
    ]


def test_template_internal_sort_precedes_its_own_merge_at_any_call_position(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortmerge.sas").write_text(
        "%macro sortmerge(inds=, out=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "data &out.;\nmerge &inds.;\nby actual;\nrun;\n"
        "%mend sortmerge;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "%sortmerge(inds=work.early, out=work.early_out);\n"
        "data work.filler;\nset sdtm.ae;\nrun;\n"
        "%sortmerge(inds=work.late, out=work.late_out);\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-internal-order")

    assert len([f for f in graph["findings"] if f["type"] == "merge_by_not_prefix_of_sort_by"]) == 2


def test_template_later_internal_sort_cannot_affect_earlier_internal_merge(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "late_sort.sas").write_text(
        "%macro late_sort(inds=, out=);\n"
        "data &out.;\nmerge &inds.;\nby actual;\nrun;\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend late_sort;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "%late_sort(inds=work.left, out=work.out);\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-late-internal-sort")

    assert not any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_outer_prior_sort_is_visible_to_template_internal_merge(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "mergeit.sas").write_text(
        "%macro mergeit(inds=, out=);\n"
        "data &out.;\nmerge &inds.;\nby actual;\nrun;\n"
        "%mend mergeit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "proc sort data=work.left;\nby expected;\nrun;\n"
        "%mergeit(inds=work.left, out=work.out);\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="outer-sort-in-template")

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_template_net_sort_evidence_is_visible_to_later_main_merge(tmp_path):
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\n"
        "proc sort data=&inds.;\nby expected;\nrun;\n"
        "%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "%sortit(inds=work.left);\n"
        "data work.out;\nmerge work.left;\nby actual;\nrun;\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="template-net-sort")

    assert any(f["type"] == "merge_by_not_prefix_of_sort_by" for f in graph["findings"])


def test_calls_inside_data_and_sql_blocks_are_captured_once_in_source_order(tmp_path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    # gm-prefixed: the run() pipeline's lazy contract pre-pass only scans
    # for `%gm\w+` call names (see run_pipeline._called_gm_macro_names).
    (contracts / "gmcontractit.md").write_text(
        "# gmcontractit\n\n## Purpose\n\nDoes a thing.\n\n## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| inds | Input. | LIBRARY.DATASET | REQUIRED |\n"
        "| outds | Output. | LIBRARY.DATASET | REQUIRED |\n",
        encoding="utf-8",
    )
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sourceit.sas").write_text(
        "%macro sourceit(inds=);\nproc sort data=&inds.;\nby usubjid;\nrun;\n%mend sourceit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "data work.out;\n"
        "%sourceit(inds=work.left);\n"
        "%gm_missing(inds=work.left);\n"
        "set sdtm.ae;\nrun;\n"
        "proc sql;\n"
        "%gmcontractit(inds=work.left, outds=work.contract_out);\n"
        "create table work.sql_out as select * from sdtm.dm;\n"
        "%gmcontractit(inds=work.left, outds=work.contract_late);\nquit;\n",
        "macro_roots:\n  - macros\nmacro_contracts:\n  - contracts\n",
    )

    graph = run(result, run_id="calls-inside-blocks")

    assert _macro_names(graph) == ["sourceit", "gm_missing", "gmcontractit", "gmcontractit"]
    assert [
        node["source"]["statement_order"]
        for node in graph["nodes"] if node["type"] == "MacroCall"
    ] == [2, 3, 7, 9]
    assert len([n for n in graph["nodes"] if n["type"] == "MacroCall"]) == 4
    assert any(n["type"] == "UnknownMacro" and n["label"] == "gm_missing" for n in graph["nodes"])
    assert any(n["type"] == "MacroContract" and n["label"] == "gmcontractit" for n in graph["nodes"])
    # A matched md-parsed contract has no role classification, so it infers
    # no dataset edges (wayfinder: bind-macro-calls-to-md-contracts.md).
    assert not any(
        (
            e["type"] == "reads_dataset"
            and e["from"] in {"dataset:work.contract_out", "dataset:work.contract_late"}
        )
        or (
            e["type"] in {"writes_dataset", "depends_on"}
            and e["to"] in {"dataset:work.contract_out", "dataset:work.contract_late"}
        )
        for e in graph["edges"]
    )


def test_top_level_calls_are_not_processed_twice_and_definition_body_is_not_a_call(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "%gm_first(x=1);\n"
        "%macro local();\n%gm_hidden(x=2);\n%mend local;\n"
        "%gm_second(x=3);\n",
    )

    graph = run(result, run_id="macro-call-dedup")

    assert _macro_names(graph) == ["gm_first", "gm_second"]


def _build_graph():
    result = load_config(FIXTURE)
    assert result.ok
    graph = run(result, run_id="test-run-0001")
    with tempfile.TemporaryDirectory() as tmp:
        path = save_graph(graph, Path(tmp) / "graph.json")
        return load_graph(path)


def test_run_produces_partial_status_matching_section_22():
    """Section 22's own expected findings: PARTIAL, one UNRESOLVED_MACRO_SOURCE,
    one UNRESOLVED_MACRO_VARIABLE."""
    graph = _build_graph()

    assert graph["run_status"] == "PARTIAL"
    statuses = {f["status"] for f in graph["findings"]}
    assert "UNRESOLVED_MACRO_SOURCE" in statuses
    assert "UNRESOLVED_MACRO_VARIABLE" in statuses


def test_run_produces_the_expected_node_and_edge_type_shape():
    graph = _build_graph()

    node_types = {n["type"] for n in graph["nodes"]}
    edge_types = {e["type"] for e in graph["edges"]}

    assert node_types >= {
        "Program", "SetupFile", "Library", "MacroVariable", "Dataset",
        "Step", "MacroCall", "MacroParameter", "MacroDefinition", "MacroSourceFile", "UnknownMacro",
        "UnknownDataset",
    }
    assert edge_types >= {
        "reads_dataset", "writes_dataset", "depends_on", "calls_macro",
        "passes_parameter", "implemented_by", "resolves_to",
        "defines_macro_variable", "contains_step",
    }


def test_run_dataset_dependency_chain_matches_section_22_flow():
    """sdtm.ae/adam.adsl -> work.adae_pre -> work.adae_srt -> (contract) -> adam.adae."""
    graph = _build_graph()

    depends = {(e["from"], e["to"]) for e in graph["edges"] if e["type"] == "depends_on"}
    assert ("dataset:work.adae_pre", "dataset:sdtm.ae") in depends
    assert ("dataset:work.adae_pre", "dataset:adam.adsl") in depends
    assert ("dataset:work.adae_srt", "dataset:work.adae_pre") in depends
    assert ("dataset:adam.adae", "dataset:work.adae_srt") in depends


def test_generated_graph_renders_through_both_renderers_without_crashing():
    """The cheapest end-to-end regression guard: if graph_model ever emits a
    node/edge type the renderers don't handle, this is what catches it --
    the renderers are otherwise only ever exercised against the hand-written
    fixture, never generated output."""
    graph = _build_graph()

    mermaid_output = render_mermaid(graph)
    assert "flowchart LR" in mermaid_output

    findings_output = render_findings(graph)
    assert "UNRECOGNISED STATUS" not in findings_output


def test_setup_let_values_resolve_in_main_until_a_later_main_reassignment(tmp_path):
    result = _write_project(
        tmp_path,
        "%let root = /study/demo;\n%let domain = ae;\n",
        "libname raw \"&root./raw\";\n"
        "data work.&domain._before;\nset raw.&domain.;\nrun;\n"
        "%gm_missing(inds=work.&domain._before, outds=work.&domain._out);\n"
        "%let domain = lb;\n"
        "data work.&domain._after;\nset raw.&domain.;\nrun;\n"
        "%gm_missing(inds=work.&domain._after, outds=work.&domain._out);\n",
    )

    graph = run(result, run_id="setup-context")

    library = next(n for n in graph["nodes"] if n["type"] == "Library")
    assert library["resolved_path"] == "/study/demo/raw"
    assert {n["id"] for n in graph["nodes"] if n["type"] == "Dataset"} >= {
        "dataset:work.ae_before",
        "dataset:raw.ae",
        "dataset:work.lb_after",
        "dataset:raw.lb",
    }
    parameters = [n for n in graph["nodes"] if n["type"] == "MacroParameter"]
    assert {p["resolved_value"] for p in parameters} >= {
        "work.ae_before",
        "work.ae_out",
        "work.lb_after",
        "work.lb_out",
    }


def test_data_step_resolves_each_dataset_reference_at_its_own_statement(tmp_path):
    result = _write_project(
        tmp_path,
        "%let domain = ae;\n",
        "data work.&domain._out;\n"
        "%let domain = lb;\n"
        "set raw.&domain.;\n"
        "run;\n",
    )

    graph = run(result, run_id="intra-data-context")

    reads = {edge["from"] for edge in graph["edges"] if edge["type"] == "reads_dataset"}
    writes = {edge["to"] for edge in graph["edges"] if edge["type"] == "writes_dataset"}
    assert reads == {"dataset:raw.lb"}
    assert writes == {"dataset:work.ae_out"}


def test_partial_config_finding_is_retained_in_the_run_graph(tmp_path):
    _write_project(tmp_path, "", "")
    config = tmp_path / "project.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "macro_roots:\n"
        + "  - missing_macros\n",
        encoding="utf-8",
    )
    result = load_config(config)
    assert result.status == "PARTIAL"

    graph = run(result, run_id="partial-config")

    assert graph["run_status"] == "PARTIAL"
    assert any(finding["type"] == "config_declared_root_missing" for finding in graph["findings"])


def test_setup_macro_variable_preserves_defining_let_source(tmp_path):
    result = _write_project(tmp_path, "%let domain = ae;\n", "")

    graph = run(result, run_id="macro-variable-source")

    variable = next(node for node in graph["nodes"] if node["type"] == "MacroVariable")
    assert variable["source"] == {
        "file": "setup.sas",
        "line_start": 1,
        "line_end": 1,
        "statement_order": 1,
        "original_text": "%let domain = ae;",
        "rule": "let_statement",
    }


def test_n_programs_produce_distinct_program_and_macrovariable_nodes(tmp_path):
    """Ticket 01 acceptance: 3 distinct Program nodes and 3 distinct,
    program-scoped macro-variable nodes for a same-named %let, each carrying
    its own value -- never one collapsed node."""
    result = _write_multi_program_project(
        tmp_path,
        "",
        {
            "prog_a.sas": "%let domain = ADAE;\ndata work.a;\nset sdtm.ae;\nrun;\n",
            "prog_b.sas": "%let domain = ADDV;\ndata work.b;\nset sdtm.dv;\nrun;\n",
            "prog_c.sas": "%let domain = ADEXSUM;\ndata work.c;\nset sdtm.ex;\nrun;\n",
        },
    )

    graph = run(result, run_id="n-programs")

    program_nodes = [n for n in graph["nodes"] if n["type"] == "Program"]
    assert {n["label"] for n in program_nodes} == {"prog_a.sas", "prog_b.sas", "prog_c.sas"}
    assert len(program_nodes) == 3

    domain_vars = {
        n["id"]: n["value"]
        for n in graph["nodes"]
        if n["type"] == "MacroVariable" and n["label"] == "domain"
    }
    assert domain_vars == {
        "macrovar:000_prog_a.sas:domain@1": "ADAE",
        "macrovar:001_prog_b.sas:domain@1": "ADDV",
        "macrovar:002_prog_c.sas:domain@1": "ADEXSUM",
    }


def test_two_programs_sharing_a_basename_get_distinct_graph_nodes(tmp_path):
    """Regression: `a/adae.sas` and `b/adae.sas` used to collapse into one
    `program:adae.sas` node (and one `macrovar:adae.sas:...` node) because
    the graph ids were keyed by bare basename -- the second program's %let
    silently vanished via add_node's generic id-based dedup, with no crash
    and no finding. Ids are now qualified by declaration index."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "adae.sas").write_text(
        "%let d = A;\ndata work.a;\nset sdtm.ae;\nrun;\n", encoding="utf-8",
    )
    (tmp_path / "b" / "adae.sas").write_text(
        "%let d = B;\ndata work.b;\nset sdtm.ae;\nrun;\n", encoding="utf-8",
    )
    (tmp_path / "setup.sas").write_text("", encoding="utf-8")
    config = tmp_path / "project.yaml"
    config.write_text(
        "main_program:\n  - a/adae.sas\n  - b/adae.sas\n"
        "setup_file: setup.sas\n"
        "allowed_roots:\n  - .\n"
        "output_dir: graph_runs\n",
        encoding="utf-8",
    )
    result = load_config(config)
    assert result.ok

    graph = run(result, run_id="same-basename")

    program_nodes = [n for n in graph["nodes"] if n["type"] == "Program"]
    assert len(program_nodes) == 2
    assert {n["id"] for n in program_nodes} == {"program:000_adae.sas", "program:001_adae.sas"}

    d_vars = {
        n["id"]: n["value"]
        for n in graph["nodes"]
        if n["type"] == "MacroVariable" and n["label"] == "d"
    }
    assert d_vars == {
        "macrovar:000_adae.sas:d@1": "A",
        "macrovar:001_adae.sas:d@1": "B",
    }


def test_gm_macro_called_only_by_third_program_resolves_through_contract(tmp_path):
    """Ticket 01 acceptance: a %gm...-prefixed macro called only by the third
    declared program still resolves through its .md contract in the merged
    graph -- the lazy-contract pre-pass must scan every declared program, not
    just the first."""
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "gm_only_third.md").write_text(
        "# gm_only_third\n\n## Purpose\n\nDerives a dataset.\n\n## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| inds | Input. | LIBRARY.DATASET | REQUIRED |\n"
        "| outds | Output. | LIBRARY.DATASET | REQUIRED |\n",
        encoding="utf-8",
    )
    result = _write_multi_program_project(
        tmp_path,
        "",
        {
            "prog_a.sas": "data work.a;\nset sdtm.ae;\nrun;\n",
            "prog_b.sas": "data work.b;\nset sdtm.dv;\nrun;\n",
            "prog_c.sas": "%gm_only_third(inds=sdtm.ex, outds=work.c_out);\n",
        },
        "macro_contracts:\n  - contracts\n",
    )

    graph = run(result, run_id="third-program-contract")

    assert any(
        n["type"] == "MacroContract" and n["label"] == "gm_only_third"
        for n in graph["nodes"]
    )
    assert any(f["type"] == "macro_contract_matched" for f in graph["findings"])


def test_sort_evidence_does_not_leak_between_programs(tmp_path):
    """Ticket 01 acceptance: sort_by_at/sort_by_of isolation across programs
    is a behavioral test, not an emptiness check. Program A sorts
    `adam.adsl` by `usubjid`, then merges it by a mismatched `aeseq` --
    that merge must still fire the prefix-mismatch finding (positive
    control: the mechanism itself still works). Program B merges the same
    `adam.adsl` by `aeseq` with no sort of its own -- if program A's sort
    evidence leaked into program B's parse, the same mismatch would
    incorrectly fire there too; isolation means it must not."""
    result = _write_multi_program_project(
        tmp_path,
        "",
        {
            "prog_a.sas": (
                "proc sort data=adam.adsl;\nby usubjid;\nrun;\n"
                "data work.a_out;\nmerge adam.adsl;\nby aeseq;\nrun;\n"
            ),
            "prog_b.sas": (
                "data work.b_out;\nmerge adam.adsl;\nby aeseq;\nrun;\n"
            ),
        },
    )

    graph = run(result, run_id="sort-isolation")

    mismatches = [f for f in graph["findings"] if f["type"] == "merge_by_not_prefix_of_sort_by"]
    assert len(mismatches) == 1
    assert mismatches[0]["source"]["file"] == "prog_a.sas"


def test_main_program_rebinding_a_setup_value_is_an_information_finding(tmp_path):
    """Ticket 01 acceptance: a program rebinding a value setup.sas already
    declared gets a plainly-informational finding, not an error and not
    silence. A same-value rebind is not a divergence and gets no finding."""
    result = _write_multi_program_project(
        tmp_path,
        "%let domain = SETUPVAL;\n",
        {
            "prog_a.sas": "%let domain = PROGVAL;\ndata work.a;\nset sdtm.ae;\nrun;\n",
            "prog_b.sas": "%let domain = SETUPVAL;\ndata work.b;\nset sdtm.ae;\nrun;\n",
        },
    )

    graph = run(result, run_id="setup-rebind")

    rebinds = [
        f for f in graph["findings"] if f["type"] == "main_program_rebinds_setup_macro_variable"
    ]
    assert len(rebinds) == 1
    assert rebinds[0]["status"] == "SUPPORTED"
    assert rebinds[0]["severity"] == "INFORMATION"
    assert rebinds[0]["source"]["file"] == "prog_a.sas"
    assert "PROGVAL" in rebinds[0]["message"]
    assert "SETUPVAL" in rebinds[0]["message"]
    assert graph["run_status"] != "FAILED"


def test_same_named_included_files_keep_distinct_macro_call_and_parameter_nodes(tmp_path):
    (tmp_path / "left").mkdir()
    (tmp_path / "right").mkdir()
    (tmp_path / "left" / "common.sas").write_text("%gm_missing(x=1);\n", encoding="utf-8")
    (tmp_path / "right" / "common.sas").write_text("%gm_missing(x=1);\n", encoding="utf-8")
    result = _write_project(
        tmp_path,
        '%include "left/common.sas";\n',
        '%include "right/common.sas";\n',
    )

    graph = run(result, run_id="common-includes")

    calls = [node for node in graph["nodes"] if node["type"] == "MacroCall"]
    parameters = [node for node in graph["nodes"] if node["type"] == "MacroParameter"]
    assert len(calls) == len({node["id"] for node in calls}) == 2
    assert len(parameters) == len({node["id"] for node in parameters}) == 2


def test_commented_data_step_is_inactive_evidence_not_active_lineage(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "data work.active;\nset sdtm.ae;\nrun;\n"
        "/* data work.disabled; set sdtm.dm; run; */\n",
    )

    graph = run(result, run_id="inactive-data")

    assert graph["run_status"] == "COMPLETE"
    assert {node["id"] for node in graph["nodes"] if node["type"] == "Dataset"} == {
        "dataset:work.active", "dataset:sdtm.ae"
    }
    assert {node["type"] for node in graph["nodes"]} >= {
        "CommentBlock", "InactiveEvidence", "CommentedStatement"
    }
    assert {edge["type"] for edge in graph["edges"]} >= {
        "comment_mentions_dataset", "inactive_candidate_reads",
        "inactive_candidate_writes", "inactive_candidate_depends_on",
    }
    assert not any("disabled" in node.get("label", "") for node in graph["nodes"] if node["type"] == "Dataset")


def test_inactive_macro_references_are_preserved_without_resolution_or_findings(tmp_path):
    result = _write_project(
        tmp_path,
        "%let domain = ae;\n",
        "/* data work.&missing._out; set sdtm.&domain.; run; */\n",
    )

    graph = run(result, run_id="inactive-macros")

    assert graph["run_status"] == "COMPLETE"
    assert not any(node["type"] in {"Dataset", "UnknownDataset", "MacroVariable"} and "missing" in node.get("label", "") for node in graph["nodes"])
    mentions = [edge for edge in graph["edges"] if edge["type"] == "comment_mentions_macro"]
    assert {edge["macro_name"] for edge in mentions} == {"domain", "missing"}
    assert {(edge["source"]["line_start"], edge["source"]["original_text"]) for edge in mentions} == {
        (1, " data work.&missing._out;"),
        (1, " set sdtm.&domain.;"),
    }


def test_comment_prose_stays_a_comment_block_without_invented_dependencies(tmp_path):
    result = _write_project(tmp_path, "", "/* revisit data work.a after the AE review */\n")

    graph = run(result, run_id="comment-prose")

    assert [node["type"] for node in graph["nodes"] if node["type"] == "CommentBlock"] == ["CommentBlock"]
    assert not any(node["type"] == "CommentedStatement" for node in graph["nodes"])
    assert not any(edge["type"].startswith("inactive_candidate") for edge in graph["edges"])


def test_inactive_evidence_preserves_comment_and_statement_source(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "/*\n  data work.disabled;\n  set sdtm.dm;\nrun;\n*/\n",
    )

    graph = run(result, run_id="inactive-source")

    block = next(node for node in graph["nodes"] if node["type"] == "CommentBlock")
    statement = next(node for node in graph["nodes"] if node["type"] == "CommentedStatement")
    assert block["source"] == {
        "file": "main.sas", "line_start": 1, "line_end": 5,
        "statement_order": 0,
        "original_text": "/*\n  data work.disabled;\n  set sdtm.dm;\nrun;\n*/",
        "rule": "comment_block",
    }
    assert statement["source"]["line_start"] == 2
    assert statement["source"]["line_end"] == 2
    assert statement["source"]["original_text"] == "  data work.disabled;"


def test_inactive_edges_point_to_their_exact_commented_statement(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "/*\n  data work.disabled;\n  set sdtm.dm;\nrun;\n*/\n",
    )

    graph = run(result, run_id="inactive-edge-source")

    comment = next(node for node in graph["nodes"] if node["type"] == "CommentBlock")
    assert comment["source"]["original_text"] == "/*\n  data work.disabled;\n  set sdtm.dm;\nrun;\n*/"
    write = next(edge for edge in graph["edges"] if edge["type"] == "inactive_candidate_writes")
    read = next(edge for edge in graph["edges"] if edge["type"] == "inactive_candidate_reads")
    depends = next(edge for edge in graph["edges"] if edge["type"] == "inactive_candidate_depends_on")
    mentions = [edge for edge in graph["edges"] if edge["type"] == "comment_mentions_dataset"]
    assert write["source"]["line_start"] == 2
    assert write["source"]["original_text"] == "  data work.disabled;"
    assert read["source"]["line_start"] == 3
    assert read["source"]["original_text"] == "  set sdtm.dm;"
    assert depends["source"] == dict(read["source"], rule="inactive_candidate_depends_on")
    assert {(edge["dataset_name"], edge["source"]["line_start"], edge["source"]["original_text"]) for edge in mentions} == {
        ("work.disabled", 2, "  data work.disabled;"),
        ("sdtm.dm", 3, "  set sdtm.dm;"),
    }


def test_same_basename_included_comments_have_unique_inactive_ids(tmp_path):
    (tmp_path / "left").mkdir()
    (tmp_path / "right").mkdir()
    for directory in ("left", "right"):
        (tmp_path / directory / "common.sas").write_text(
            "/* data work.disabled; set sdtm.ae; run; */\n", encoding="utf-8"
        )
    result = _write_project(
        tmp_path,
        '%include "left/common.sas";\n',
        '%include "right/common.sas";\n',
    )

    graph = run(result, run_id="inactive-common-includes")

    comments = [node for node in graph["nodes"] if node["type"] == "CommentBlock"]
    assert len(comments) == len({node["id"] for node in comments}) == 2
    assert all(node["source"]["file"] == "common.sas" for node in comments)


def test_multiple_commented_data_units_stay_comment_only(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "/* data work.a; set sdtm.ae; run; data work.b; set sdtm.dm; run; */\n",
    )

    graph = run(result, run_id="inactive-multi-unit")

    assert len([node for node in graph["nodes"] if node["type"] == "CommentBlock"]) == 1
    assert not any(node["type"] in {"InactiveEvidence", "CommentedStatement"} for node in graph["nodes"])
    assert not any(edge["type"].startswith("inactive_candidate") for edge in graph["edges"])


def test_repeated_inactive_references_reuse_evidence_and_dedupe_dependencies(tmp_path):
    result = _write_project(
        tmp_path,
        "",
        "/* data work.&out.; set sdtm.&domain.; set sdtm.&domain.; run; */\n",
    )

    graph = run(result, run_id="inactive-repeated")

    evidence = [node for node in graph["nodes"] if node["type"] == "InactiveEvidence"]
    assert [(node["evidence_kind"], node["label"]) for node in evidence] == [
        ("macro", "out"), ("dataset", "work.&out."),
        ("macro", "domain"), ("dataset", "sdtm.&domain."),
    ]
    assert len([edge for edge in graph["edges"] if edge["type"] == "inactive_candidate_depends_on"]) == 1


def test_setup_and_main_findings_with_same_local_order_receive_distinct_ids(tmp_path):
    result = _write_project(
        tmp_path,
        '%include "missing.sas";\ndata _null_;\ncall symputx("x", "y");\nrun;\n',
        '%include "missing.sas";\ndata _null_;\ncall symputx("x", "y");\nrun;\n',
    )

    findings = run(result, run_id="finding-identity")["findings"]

    assert len({finding["id"] for finding in findings}) == len(findings)
    include_sources = [
        finding["source"] for finding in findings if finding["type"] == "include_file_missing"
    ]
    runtime_sources = [
        finding["source"]
        for finding in findings
        if finding["type"] == "runtime_macro_variable_creation"
    ]
    assert {(source["file"], source["statement_order"]) for source in include_sources} == {
        ("setup.sas", 1),
        ("main.sas", 1),
    }
    assert {(source["file"], source["statement_order"]) for source in runtime_sources} == {
        ("setup.sas", 3),
        ("main.sas", 3),
    }


def test_inline_macro_defined_earlier_in_main_program_resolves_local_call(tmp_path):
    """B1: a %macro...%mend block defined earlier in the main program must be
    indexed, so a later parenthesized call resolves against it instead of
    reaching UnknownMacro."""
    result = _write_project(
        tmp_path,
        "",
        "%macro foo(in=, out=);\ndata &out.;\nset &in.;\nrun;\n%mend foo;\n"
        "%foo(in=work.a, out=work.b);\n",
    )

    graph = run(result, run_id="inline-macro-local-call")

    assert not any(n["type"] == "UnknownMacro" for n in graph["nodes"])
    call_id = next(n["id"] for n in graph["nodes"] if n["type"] == "MacroCall")
    definition_id = next(n["id"] for n in graph["nodes"] if n["type"] == "MacroDefinition")
    assert ("implemented_by", call_id, definition_id) in {
        (e["type"], e["from"], e["to"]) for e in graph["edges"]
    }
    # A macro defined and called inline in the same main-program file must
    # not be modeled twice -- no separate MacroSourceFile node duplicating
    # the Program node that already represents this file (codex: qc_adae.sas
    # was appearing as its own MacroSourceFile).
    program_id = next(n["id"] for n in graph["nodes"] if n["type"] == "Program")
    assert not any(n["type"] == "MacroSourceFile" for n in graph["nodes"])
    assert ("defined_in", definition_id, program_id) in {
        (e["type"], e["from"], e["to"]) for e in graph["edges"]
    }
    assert {"dataset:work.a", "dataset:work.b"} <= {
        n["id"] for n in graph["nodes"] if n["type"] == "Dataset"
    }


def test_uncalled_macro_invariant_still_holds_after_inline_indexing(tmp_path):
    """Indexing an inline definition (B1) must not resurrect the invariant the
    existing uncalled-macro tests protect: an UNCALLED inline macro's body
    still must not be inlined into the runtime statement stream."""
    result = _write_project(
        tmp_path,
        "",
        "%macro unused();\ndata work.hidden;\nset sdtm.ae;\nrun;\n%mend unused;\n",
    )

    graph = run(result, run_id="inline-index-uncalled-still-excluded")

    assert not any(n["type"] == "Step" for n in graph["nodes"])
    assert not any(n["id"] == "dataset:work.hidden" for n in graph["nodes"])


def test_bare_call_without_parens_resolves_inline_definition(tmp_path):
    """B1+B2 together: a paren-less call (`%name;`) to a macro defined
    earlier in the same file must produce a MacroCall bound to its local
    MacroDefinition, not silently vanish."""
    result = _write_project(
        tmp_path,
        "",
        "%macro ablivfl_derive();\ndata work.flags2;\nset work.flags1;\nrun;\n"
        "%mend ablivfl_derive;\n"
        "%ablivfl_derive;\n",
    )

    graph = run(result, run_id="bare-call-inline-resolution")

    assert any(n["type"] == "MacroCall" for n in graph["nodes"])
    assert not any(n["type"] == "UnknownMacro" for n in graph["nodes"])
    assert any(n["type"] == "MacroDefinition" for n in graph["nodes"])


def test_whole_file_macro_wrapper_reports_a_finding_instead_of_total_silence(tmp_path):
    """B3, updated by setup-bootstrap-resolution: a setup file wholly wrapped
    in one %macro...%mend and invoked by a bare call is now unwrapped -- its
    body becomes real evidence (a Library node here) instead of the old
    control-flow-rejection silence, and the unwrap itself is recorded by a
    dedicated finding rather than the old template-rejection one."""
    result = _write_project(
        tmp_path,
        "%macro setup / minoperator;\nlibname x \"/tmp/x\";\n%mend setup;\n%setup;\n",
        "data work.out;\nset sdtm.in;\nrun;\n",
    )

    graph = run(result, run_id="whole-file-macro-wrapper")

    assert any(n["type"] == "Library" and n["id"] == "library:x" for n in graph["nodes"])
    assert any(
        f["type"] == "setup_macro_wrapper_unwrapped" for f in graph["findings"]
    )


def test_standalone_macro_variable_reference_produces_finding_not_silence(tmp_path):
    """B5: a top-level statement that is nothing but a macro-variable
    reference (no leading %, so it isn't a macro call) must not be dropped
    with zero record."""
    result = _write_project(
        tmp_path,
        "",
        "&&mapcode_&domain.;\ndata work.out;\nset sdtm.in;\nrun;\n",
    )

    graph = run(result, run_id="standalone-macro-variable-reference")

    assert any(
        f["type"] == "unresolved_macro_generated_statement" for f in graph["findings"]
    )


def test_standalone_macro_variable_reference_message_reflects_partial_resolution(tmp_path):
    """codex #7: the finding's message must not blanket-claim the expansion
    is unknown when a single-`&` reference inside it actually resolves via
    prior %let evidence -- and resolving it for the message must not feed
    the substituted text back into dispatch (no new nodes/edges appear)."""
    result = _write_project(
        tmp_path,
        "",
        "%let domain = ae;\n"
        "&&mapcode_&domain.;\ndata work.out;\nset sdtm.in;\nrun;\n",
    )

    graph = run(result, run_id="standalone-macro-variable-partial-resolution")

    finding = next(
        f for f in graph["findings"] if f["type"] == "unresolved_macro_generated_statement"
    )
    assert "&&mapcode_ae" in finding["message"]
    assert not any(n["type"] in {"MacroCall", "Library"} for n in graph["nodes"])
    assert len([n for n in graph["nodes"] if n["type"] == "Step"]) == 1


def test_standalone_macro_variable_reference_fully_resolved_produces_no_finding(tmp_path):
    """codex #7: a single-`&` reference that resolves completely via prior
    %let evidence is not genuinely unknowable -- it must not be reported as
    an unresolved/unknowable statement at all."""
    result = _write_project(
        tmp_path,
        "",
        "%let code = %foo;\n"
        "&code.;\ndata work.out;\nset sdtm.in;\nrun;\n",
    )

    graph = run(result, run_id="standalone-macro-variable-fully-resolved")

    assert not any(
        f["type"] == "unresolved_macro_generated_statement" for f in graph["findings"]
    )


def test_inline_macro_defined_in_an_included_file_reports_its_own_provenance(tmp_path):
    """codex #3: `build_inline_macro_index` must attribute a definition to
    the file it actually came from -- a %macro defined inside a %include'd
    child file must not be reported as living in main.sas."""
    (tmp_path / "defs.sas").write_text(
        "%macro foo(in=, out=);\ndata &out.;\nset &in.;\nrun;\n%mend foo;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        '%include "defs.sas";\n%foo(in=work.a, out=work.b);\n',
    )

    graph = run(result, run_id="inline-macro-in-included-file")

    definition = next(n for n in graph["nodes"] if n["type"] == "MacroDefinition")
    assert definition["source"]["file"] == "defs.sas"
    source_file = next(n for n in graph["nodes"] if n["type"] == "MacroSourceFile")
    assert source_file["path"].endswith("defs.sas")


def test_same_file_included_twice_is_one_site_not_a_conflict(tmp_path):
    """codex #2: %include'ing the same physical definition twice splices its
    %macro...%mend text into the statement stream twice, but it is still one
    physical definition -- dedup on (path, line span) so it doesn't become a
    spurious MacroConflict."""
    (tmp_path / "defs.sas").write_text(
        "%macro foo(in=, out=);\ndata &out.;\nset &in.;\nrun;\n%mend foo;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        '%include "defs.sas";\n%include "defs.sas";\n%foo(in=work.a, out=work.b);\n',
    )

    graph = run(result, run_id="same-file-included-twice")

    assert not any(n["type"] == "MacroConflict" for n in graph["nodes"])
    assert len([n for n in graph["nodes"] if n["type"] == "MacroDefinition"]) == 1
    assert not any(f["type"] == "macro_conflict" for f in graph["findings"])


def test_call_before_its_inline_definition_does_not_resolve_as_a_forward_reference(tmp_path):
    """codex #1: real SAS compiles a %macro when its %macro statement is
    reached in the pass -- a call written textually BEFORE its own inline
    definition must not resolve against it (that definition hasn't been
    "compiled" yet at the point of the call)."""
    result = _write_project(
        tmp_path,
        "",
        "%foo(in=work.a, out=work.b);\n"
        "%macro foo(in=, out=);\ndata &out.;\nset &in.;\nrun;\n%mend foo;\n",
    )

    graph = run(result, run_id="forward-reference-call-before-definition")

    assert any(n["type"] == "UnknownMacro" for n in graph["nodes"])
    assert not any(n["type"] == "MacroDefinition" for n in graph["nodes"])


def test_macro_roots_sourced_definition_is_unaffected_by_the_forward_gate(tmp_path):
    """The forward-resolution gate only applies to a same-file (inline)
    definition -- a macro_roots-sourced one lives in a different file with
    no ordering relative to the call site, and must keep resolving exactly
    as every existing macro_roots test expects."""
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "sortit.sas").write_text(
        "%macro sortit(inds=);\nproc sort data=&inds.;\nby expected;\nrun;\n%mend sortit;\n",
        encoding="utf-8",
    )
    result = _write_project(
        tmp_path,
        "",
        "%sortit(inds=work.left);\n",
        "macro_roots:\n  - macros\n",
    )

    graph = run(result, run_id="macro-roots-source-unaffected-by-gate")

    assert not any(n["type"] == "UnknownMacro" for n in graph["nodes"])
    assert any(n["type"] == "MacroDefinition" for n in graph["nodes"])


def test_macro_roots_overlapping_main_program_directory_does_not_conflict(tmp_path):
    """codex #2: `macro_roots` pointing at the main program's own directory
    scans main.sas as a macro source file, indexing the SAME physical
    %macro...%mend definition that inline indexing already found in it --
    that must dedupe to one site, not a spurious MacroConflict."""
    result = _write_project(
        tmp_path,
        "",
        "%macro foo(in=, out=);\ndata &out.;\nset &in.;\nrun;\n%mend foo;\n"
        "%foo(in=work.a, out=work.b);\n",
        extra_config="macro_roots:\n  - .\n",
    )

    graph = run(result, run_id="macro-roots-overlap-main-program")

    assert not any(n["type"] == "MacroConflict" for n in graph["nodes"])
    assert len([n for n in graph["nodes"] if n["type"] == "MacroDefinition"]) == 1


QC_ADAE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "qc_adae" / "project.yaml"
requires_qc_adae = pytest.mark.skipif(
    not QC_ADAE_FIXTURE.exists(),
    reason="private qc_adae fixture is excluded from public staging",
)


def _run_qc_adae():
    result = load_config(QC_ADAE_FIXTURE)
    assert result.ok
    return run(result, run_id="proc-import-e2e")


def test_proc_import_block_dispatches_through_apply_block(tmp_path):
    """External-file-sources ticket 02: confirms the dispatch wiring from
    ticket 01 actually fires through `run_pipeline.run` (not just
    `rules_proc_import.apply` called directly, as ticket 01's own unit tests
    do)."""
    graph = run(
        _write_project(
            tmp_path, "",
            'proc import datafile="/data/raw/ae.xlsx" out=work.ae_raw dbms=xlsx replace;\nrun;\n',
        ),
        run_id="proc-import-dispatch",
    )

    external_file = next(n for n in graph["nodes"] if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:/data/raw/ae.xlsx"
    edge = next(e for e in graph["edges"] if e["type"] == "reads_external_file")
    assert (edge["from"], edge["to"]) == (external_file["id"], "dataset:work.ae_raw")


def test_proc_export_block_dispatches_through_apply_block(tmp_path):
    graph = run(
        _write_project(
            tmp_path, "",
            'proc export data=work.ae outfile="/data/exports/ae.xlsx" dbms=xlsx replace;\nrun;\n',
        ),
        run_id="proc-export-dispatch",
    )

    external_file = next(n for n in graph["nodes"] if n["type"] == "ExternalFile")
    edge = next(e for e in graph["edges"] if e["type"] == "writes_external_file")
    assert (edge["from"], edge["to"], edge["dbms"]) == (
        "dataset:work.ae", external_file["id"], "xlsx",
    )


def test_proc_import_out_before_datafile_ordering_is_order_agnostic(tmp_path):
    """setup.sas:389's own shape: `out=` precedes `datafile=` on the PROC
    IMPORT statement. Regression for the order-agnostic re.search posture
    ticket 01 requires -- this must resolve identically to `datafile=` first."""
    graph = run(
        _write_project(
            tmp_path, "",
            "proc import out=work.variable_ds\n"
            '    datafile="/data/raw/spec.xlsx"\n'
            "    dbms=xlsx replace;\n"
            "    getnames=yes;\n"
            "run;\n",
        ),
        run_id="proc-import-out-before-datafile",
    )

    edge = next(e for e in graph["edges"] if e["type"] == "reads_external_file")
    assert edge["to"] == "dataset:work.variable_ds"
    assert edge["from"] == "externalfile:/data/raw/spec.xlsx"
    assert edge["dbms"] == "xlsx"
    assert edge["getnames"] == "yes"


def test_proc_import_unresolved_datafile_produces_warning_severity_end_to_end(tmp_path):
    """Ticket 01's own requirement, exercised through the full pipeline: an
    unbound macro variable in DATAFILE= still mints an ExternalFile node and
    reports UNRESOLVED_MACRO_VARIABLE at WARNING severity, not silence."""
    graph = run(
        _write_project(
            tmp_path, "",
            'proc import datafile="&missing.ae.xlsx" out=work.ae_raw dbms=xlsx replace;\nrun;\n',
        ),
        run_id="proc-import-warning-severity",
    )

    finding = next(
        f for f in graph["findings"]
        if f["type"] == "unresolved_macro_variable_in_external_file"
    )
    assert (finding["status"], finding["severity"]) == ("UNRESOLVED_MACRO_VARIABLE", "WARNING")
    external_file = next(n for n in graph["nodes"] if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:&missing.ae.xlsx"
    assert finding["affected_nodes"] == [external_file["id"]]
    assert graph["run_status"] == "PARTIAL"


@requires_qc_adae
def test_qc_adae_direct_call_sites_produce_external_file_evidence():
    """Ticket 02's own fixture claim: tests/fixtures/qc_adae/qc_adae.sas lines
    141/165/246/415 sit at the top level and dispatch directly through
    `apply_block`. The macro-wrapped call sites are asserted separately by
    ticket 03's test below.

    setup.sas:389, updated by setup-bootstrap-resolution: since setup.sas is
    now unwrapped and its `%IF %symexist(_rawspec)` branch is taken for this
    fixture's `_type = interim`, its own PROC IMPORT is real evidence too --
    scoped separately by source file so it is not conflated with
    qc_adae.sas's own line numbers."""
    graph = _run_qc_adae()

    edges = [
        e for e in graph["edges"]
        if e["type"] == "reads_external_file" and not e.get("template_derived")
    ]
    qc_adae_lines = {
        e["source"]["line_start"] for e in edges if e["source"]["file"] == "qc_adae.sas"
    }
    assert qc_adae_lines == {141, 165, 246, 415}
    setup_lines = {
        e["source"]["line_start"] for e in edges if e["source"]["file"] == "setup.sas"
    }
    assert setup_lines == {389}

    external_file_ids = {n["id"] for n in graph["nodes"] if n["type"] == "ExternalFile"}
    assert external_file_ids == {
        "externalfile:/home/study/stats/interim/data/rawxls/SMQ_spreadsheet_28_0_English.xlsx",
        "externalfile:/home/study/stats/interim/data/rawxls/FMQ MedDRA Preferred Terms_Current Version_28_Mar2025.xlsx",
        "externalfile:%sysfunc(tranwrd(%gmExecuteUnixCmd(cmds = %str(find /home/study/stats/interim/data/rawspec/ -maxdepth 1 -type f -exec ls -t {} + | head -1)), @, %str()))",
    }

    write_targets = {e["to"] for e in edges}
    assert write_targets == {
        "dataset:work.smq_aptc_raw0",
        "dataset:work.smq_ptcounts_raw",
        "dataset:work.smq_hier_raw",
        "dataset:work._fmq_hepatic_raw",
        "dataset:work.variable_ds",
    }


@requires_qc_adae
def test_qc_adae_proc_import_output_read_downstream_is_not_a_dangling_read():
    """`reads_external_file` (PROC IMPORT's own edge kind for `out=`, not
    `writes_dataset`) counts as "written" for ticket 03's dangling-read
    check -- otherwise every PROC IMPORT output later read downstream (here
    smq_aptc_raw0/smq_ptcounts_raw/smq_hier_raw, each read a few lines below
    its own `out=`) is a false positive `dataset_read_never_written`."""
    graph = _run_qc_adae()

    dangling = {
        f["object"] for f in graph["findings"] if f["type"] == "dataset_read_never_written"
    }
    assert not dangling & {
        "work.smq_aptc_raw0", "work.smq_ptcounts_raw", "work.smq_hier_raw",
    }


@requires_qc_adae
def test_qc_adae_macro_wrapped_proc_import_is_reported_not_silent():
    """Ticket 03: the PROC IMPORT at line 382 binds once per macro call."""
    graph = _run_qc_adae()

    calls = [
        f for f in graph["findings"]
        if f["type"] == "macro_source_template_not_executed" and f["object"] == "fmq_import_2col"
    ]
    assert not calls
    edges = [
        e for e in graph["edges"]
        if e["type"] == "reads_external_file" and e["source"]["line_start"] == 382
    ]
    assert len(edges) == 3
    assert {e["call_source"]["line_start"] for e in edges} == {403, 404, 405}
    assert {e["to"] for e in edges} == {
        "dataset:work._fmq_fmq_hypo_raw",
        "dataset:work._fmq_fmq_hyper_raw",
        "dataset:work._fmq_fmq_dka_raw",
    }
    assert all(e["template_derived"] for e in edges)
    # setup-bootstrap-resolution: setup.sas's own bare `%setup;` call no longer
    # reaches rules_macro_call at all -- it is unwrapped and its body executed
    # inline, replacing the old control-flow-rejection finding 1:1 with a
    # dedicated unwrap finding (see run_pipeline.py section of the spec).
    assert any(
        f["type"] == "setup_macro_wrapper_unwrapped" and f["object"] == "setup"
        for f in graph["findings"]
    )


def test_dataset_read_but_never_written_gets_a_uniform_supported_finding():
    """Ticket 03: a dataset read but never written by any parsed program gets
    a uniform, non-degrading finding -- no exception for SDTM raw inputs, no
    scoping by library name. `sdtm.ae` and `adam.adsl` are two different
    libraries, both read-only in this fixture, and both must fire the same
    way. `work.adae_pre` is read (by the PROC SORT) and written (by the DATA
    step), so it must not fire."""
    graph = _build_graph()

    dangling = {
        f["object"]: f for f in graph["findings"] if f["type"] == "dataset_read_never_written"
    }
    assert {"sdtm.ae", "adam.adsl"} <= set(dangling)
    assert all(
        (f["status"], f["severity"]) == ("SUPPORTED", "INFORMATION") for f in dangling.values()
    )
    assert "work.adae_pre" not in dangling


def test_read_only_dataset_finding_does_not_degrade_an_otherwise_complete_run(tmp_path):
    result = _write_project(tmp_path, "", "data work.out;\nset sdtm.ae;\nrun;\n")

    graph = run(result, run_id="dangling-read-complete")

    finding = next(f for f in graph["findings"] if f["type"] == "dataset_read_never_written")
    assert (finding["status"], finding["severity"]) == ("SUPPORTED", "INFORMATION")
    assert finding["object"] == "sdtm.ae"
    assert graph["run_status"] == "COMPLETE"
    assert not any(
        f["type"] == "dataset_read_never_written" and f["object"] == "work.out"
        for f in graph["findings"]
    )


def test_dangling_read_finding_fires_across_a_merged_multi_program_graph(tmp_path):
    """Ticket 03's own follow-up: once ticket 01's N-program merge exists,
    confirm the same finding still fires over the final merged node/edge
    set -- no reimplementation needed. `work.a` is written by prog_a and
    read by prog_b, so it must not fire even though the read and write live
    in different files; `sdtm.raw` is read only and must."""
    result = _write_multi_program_project(
        tmp_path,
        "",
        {
            "prog_a.sas": "data work.a;\nset sdtm.raw;\nrun;\n",
            "prog_b.sas": "data work.b;\nset work.a;\nrun;\n",
        },
    )

    graph = run(result, run_id="dangling-merged")

    dangling = {
        f["object"] for f in graph["findings"] if f["type"] == "dataset_read_never_written"
    }
    assert dangling == {"sdtm.raw"}


def test_sql_block_gets_a_contains_step_edge_from_its_program(tmp_path):
    """codex-review-class fix, found while building the variable-lineage
    evidence fixture: a `SqlBlock` used to get no containment edge from its
    owning `Program` at all, so any lineage walk reaching a `SqlStatement`
    inside it could never resolve which program to open (`contains_step`
    reused deliberately, not a new edge type -- see run_pipeline.py)."""
    graph = run(
        _write_project(
            tmp_path, "", "proc sql;\ncreate table work.a as select * from sdtm.ae;\nquit;\n"
        ),
        run_id="sql-block-containment",
    )

    sql_block = next(n for n in graph["nodes"] if n["type"] == "SqlBlock")
    program = next(n for n in graph["nodes"] if n["type"] == "Program")
    edge = next(
        e for e in graph["edges"]
        if e["type"] == "contains_step" and e["to"] == sql_block["id"]
    )
    assert edge["from"] == program["id"]


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("run pipeline: all checks passed")


if __name__ == "__main__":
    demo()
