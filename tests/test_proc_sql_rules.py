"""PROC SQL rules (dev plan section 14.2-14.6, 21 Phase 4)."""

import conftest  # noqa: F401

from sas_graph import rules_proc_sql
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


def test_create_table_creates_reads_writes_and_depends_on():
    """Section 14.2's own example."""
    blocks, ctx, events = build(
        "proc sql;\n  create table work.ae1 as\n  select *\n  from sdtm.ae;\nquit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    edge_types = {(e["type"], e["from"], e["to"]) for e in ctx.edges}
    reads = [e for e in ctx.edges if e["type"] == "reads_dataset"]
    writes = [e for e in ctx.edges if e["type"] == "writes_dataset"]
    assert reads[0]["to"] == "dataset:sdtm.ae"
    assert writes[0]["to"] == "dataset:work.ae1"
    assert ("depends_on", "dataset:work.ae1", "dataset:sdtm.ae") in edge_types

    sql_statement = next(n for n in ctx.nodes if n["type"] == "SqlStatement")
    assert sql_statement["sql_subtype"] == "CREATE_TABLE"


def test_multiple_create_table_statements_in_one_block_are_independent():
    """Section 14.3's own example."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.ae1 as select * from sdtm.ae;\n"
        "  create table work.dm1 as select * from sdtm.dm;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    sql_statements = [n for n in ctx.nodes if n["type"] == "SqlStatement"]
    assert len(sql_statements) == 2

    sql_block = next(n for n in ctx.nodes if n["type"] == "SqlBlock")
    contains_edges = [e for e in ctx.edges if e["type"] == "contains_sql_statement"]
    assert len(contains_edges) == 2
    assert all(e["from"] == sql_block["id"] for e in contains_edges)

    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert writes == {"dataset:work.ae1", "dataset:work.dm1"}


def test_create_view_marks_output_kind_view_not_materialized():
    """Section 14.4's own example."""
    blocks, ctx, events = build(
        "proc sql;\n  create view work.ae_v as\n  select * from sdtm.ae;\nquit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    sql_statement = next(n for n in ctx.nodes if n["type"] == "SqlStatement")
    assert sql_statement["sql_subtype"] == "CREATE_VIEW"

    write_edge = next(e for e in ctx.edges if e["type"] == "writes_dataset")
    assert write_edge["output_kind"] == "view"
    assert write_edge["materialized"] is False


def test_insert_into_marks_dataset_mutation_pattern():
    """Section 14.5's own example."""
    blocks, ctx, events = build(
        "proc sql;\n  insert into work.ae_all\n  select * from work.ae_new;\nquit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    sql_statement = next(n for n in ctx.nodes if n["type"] == "SqlStatement")
    assert sql_statement["sql_subtype"] == "INSERT_INTO"
    assert sql_statement["pattern"] == "DATASET_MUTATION"

    reads = {e["to"] for e in ctx.edges if e["type"] == "reads_dataset"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert reads == {"dataset:work.ae_new"}
    assert writes == {"dataset:work.ae_all"}


def test_missing_quit_is_recovered_not_fatal():
    """Section 14.6's own recovery case."""
    blocks, ctx, events = build(
        "proc sql;\n  create table work.a as select * from sdtm.ae;\n"
        "data work.b;\n  set work.a;\nrun;\n"
    )
    sql_block = blocks[0]
    assert sql_block.terminated is False

    rules_proc_sql.apply(sql_block, ctx, events)

    assert any(f["type"] == "sql_block_not_explicitly_closed" for f in ctx.findings)
    # The recognized CREATE TABLE inside the unterminated block still parsed.
    assert any(n["type"] == "SqlStatement" for n in ctx.nodes)
    assert all(f["status"] != "BLOCKED" for f in ctx.findings)


def test_unrecognized_sql_statement_is_not_executed_not_crashed():
    blocks, ctx, events = build("proc sql;\n  drop table work.a;\nquit;\n")
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert any(f["type"] == "proc_sql_no_recognized_statement" for f in ctx.findings)
    assert not any(n["type"] == "SqlStatement" for n in ctx.nodes)


def test_join_sources_each_create_a_read_and_dependency():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as select *\n"
        "  from work.a as a left join work.b as b on a.id=b.id;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {e["to"] for e in ctx.edges if e["type"] == "reads_dataset"}
    depends = {e["to"] for e in ctx.edges if e["type"] == "depends_on"}
    assert reads == depends == {"dataset:work.a", "dataset:work.b"}


def test_macro_dataset_names_resolve_at_the_sql_statement():
    blocks, ctx, events = build(
        "%let domain=ae;\n"
        "proc sql;\n"
        "  create table work.&domain._copy as select * from sdtm.&domain.;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    datasets = {n["id"] for n in ctx.nodes if n["type"] == "Dataset"}
    assert datasets == {"dataset:work.ae_copy", "dataset:sdtm.ae"}


def test_unresolved_sql_dataset_name_stays_unknown():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.a as select * from sdtm.&missing.;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert any(n["type"] == "UnknownDataset" for n in ctx.nodes)
    assert any(
        f["type"] == "unresolved_macro_variable_in_sql_dataset"
        for f in ctx.findings
    )


def test_sql_ids_restart_with_each_graph_context():
    first_blocks, first_ctx, first_events = build(
        "proc sql;\ncreate table work.a as select * from work.b;\nquit;\n"
    )
    second_blocks, second_ctx, second_events = build(
        "proc sql;\ncreate table work.c as select * from work.d;\nquit;\n"
    )
    rules_proc_sql.apply(first_blocks[0], first_ctx, first_events)
    rules_proc_sql.apply(second_blocks[0], second_ctx, second_events)

    first_ids = {n["id"] for n in first_ctx.nodes}
    second_ids = {n["id"] for n in second_ctx.nodes}
    assert "sqlblock:001" in first_ids & second_ids
    assert "sqlstatement:001" in first_ids & second_ids


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("proc sql rules: all checks passed")


if __name__ == "__main__":
    demo()
