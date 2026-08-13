"""wayfinder: variable-lineage-acceptance-evidence-fixture.

End-to-end acceptance evidence for variable-level lineage: one small
three-program fixture (`sdtm.lb -> adam.adlb -> a TLF summary`) parsed
through the real pipeline, proving flag impact, ordered imputed-date use,
and category coverage against the real `walk_variable_impact` -- not a
hand-built graph dict, unlike `test_variable_lineage_walk.py`'s algorithm
tests. `tests/fixtures/qc_adae/` is untouched by this file.
"""

import tempfile
from pathlib import Path

import conftest  # noqa: F401

from sas_graph.config import load_config
from sas_graph.graph_io import load_graph, save_graph
from sas_graph.run_pipeline import run
from sas_graph.variable_lineage_walk import walk_variable_impact

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "variable_lineage"


def _graph():
    result = load_config(FIXTURE_DIR / "project.yaml")
    assert result.ok, result.findings
    graph = run(result, run_id="variable-lineage-evidence")
    with tempfile.TemporaryDirectory() as tmp:
        return load_graph(save_graph(graph, Path(tmp) / "graph.json"))


def test_fixture_parses_to_a_complete_run_with_only_expected_findings():
    graph = _graph()
    assert graph["run_status"] == "COMPLETE"
    # The only finding this fixture should produce: `raw.lb_raw` is a true
    # external input, never written by any parsed program.
    assert {f["type"] for f in graph["findings"]} == {"dataset_read_never_written"}


def test_flag_impact_walk_reaches_the_tlf_program():
    """`adam.adlb.anl01fl` -- the map's own named proof case."""
    graph = _graph()
    result = walk_variable_impact(graph, "variable:adam.adlb.anl01fl")

    assert "variable:work.tlf_flag.anl01fl" in result["reached_variables"]
    reached_sql = next(
        op for op in result["reached_operations"] if op["type"] == "SqlStatement"
    )
    assert reached_sql["program"] == "program:002_tlf_summary.sas"
    values = {fact["value"] for fact in result["category_facts"]}
    assert "Y" in values  # the WHERE predicate's explicit value
    assert None in values  # the bare SELECT column's unknown value


def test_imputed_date_use_is_ordered_write_before_read():
    """An imputed date -- the map's own named proof case. Write and read
    live in the same `Step` (source-only order is only meaningful within
    one step's own statement sequence), so `statement_order` alone proves
    the write precedes the read."""
    graph = _graph()
    write = next(
        e for e in graph["edges"]
        if e["type"] == "writes_variable" and e["to"] == "variable:adam.adlb.imputed_dt"
    )
    read = next(
        e for e in graph["edges"]
        if e["type"] == "reads_variable" and e["from"] == "variable:adam.adlb.imputed_dt"
    )
    assert write["source"]["statement_order"] < read["source"]["statement_order"]

    result = walk_variable_impact(graph, "variable:adam.adlb.imputed_dt")
    assert "variable:adam.adlb.anldt" in result["reached_variables"]


def test_category_coverage_walk_reports_explicit_value_and_unknown():
    """`sdtm.lb.lbnrind` -- the map's own named proof case. Structural only:
    an explicit `ABNORMAL` (the WHERE predicate) and an `unknown` (the bare
    SELECT column, `value: null`), never a clinical judgment."""
    graph = _graph()
    result = walk_variable_impact(graph, "variable:sdtm.lb.lbnrind")

    reached_sql = next(
        op for op in result["reached_operations"] if op["type"] == "SqlStatement"
    )
    assert reached_sql["program"] == "program:002_tlf_summary.sas"
    values = {fact["value"] for fact in result["category_facts"]}
    assert "ABNORMAL" in values
    assert None in values


def test_no_category_value_node_or_variable_use_node_exists():
    graph = _graph()
    node_types = {n["type"] for n in graph["nodes"]}
    assert "CategoryValue" not in node_types
    assert "VariableUse" not in node_types


def test_no_schema_wide_pass_through_edge_for_a_variable_never_named_in_source():
    """`raw.lb_raw`'s own columns are never named anywhere in the fixture's
    source text, so no `Variable` node for them may exist -- proves the
    parser never infers a pass-through variable from a bare `SET`."""
    graph = _graph()
    assert not any(
        n["type"] == "Variable" and n["libref"] == "raw" for n in graph["nodes"]
    )


def test_no_guessed_sql_alias_resolution():
    """Both `tlf_summary.sas` SQL statements qualify their column through a
    real `AS` alias (`a`/`l`); the resolved `Variable` id must match the
    aliased table, never a guess."""
    graph = _graph()
    reads = [
        e for e in graph["edges"]
        if e["type"] == "reads_variable" and e["to"] == "sqlstatement:001"
    ]
    assert all(e["from"] == "variable:adam.adlb.anl01fl" for e in reads)
    assert not any(n["type"] == "UnknownVariable" for n in graph["nodes"])


def test_no_edge_from_an_unsafely_bound_macro_call():
    """This fixture is deliberately macro-free (the known static-binding
    boundary, wayfinder: attribute-shared-macro-reads-per-call-site) -- no
    `MacroCall` node should exist at all."""
    graph = _graph()
    assert not any(n["type"] == "MacroCall" for n in graph["nodes"])


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("variable lineage fixture: all checks passed")


if __name__ == "__main__":
    demo()
