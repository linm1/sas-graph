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
    ctx = GraphContext(main_programs=[file_name], setup_file="setup.sas", run_id="r1")
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
    assert reads[0]["from"] == "dataset:sdtm.ae"
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

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_dataset"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_dataset"}
    assert reads == {"dataset:work.ae_new"}
    assert writes == {"dataset:work.ae_all"}


def test_insert_set_writes_each_column_with_its_literal_value():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  insert into work.out set amount=1, note='x';\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"], edge["source"]["rule"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == set()
    assert writes == {
        ("variable:work.out.amount", "1", None, "proc_sql_insert_set"),
        ("variable:work.out.note", "x", None, "proc_sql_insert_set"),
    }
    assert ctx.findings == []


def test_insert_values_maps_literal_values_to_explicit_columns_by_position():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  insert into work.out (id, amount) values (1, 2);\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"], edge["source"]["rule"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == set()
    assert writes == {
        ("variable:work.out.id", "1", None, "proc_sql_insert_values"),
        ("variable:work.out.amount", "2", None, "proc_sql_insert_values"),
    }
    assert ctx.findings == []


def test_insert_select_maps_explicit_target_columns_by_position():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  insert into work.out (id, amount)\n"
        "  select x, y from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.in.x", "proc_sql_select_column", None, None),
        ("variable:work.in.y", "proc_sql_select_column", None, None),
    }
    assert writes == {
        ("variable:work.out.id", "proc_sql_select_column", None, None),
        ("variable:work.out.amount", "proc_sql_select_column", None, None),
    }
    assert ctx.findings == []


def test_insert_select_without_target_columns_keeps_select_name_writes():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  insert into work.out select x, y from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.in.x", "proc_sql_select_column", None, None),
        ("variable:work.in.y", "proc_sql_select_column", None, None),
    }
    assert writes == {
        ("variable:work.out.x", "proc_sql_select_column", None, None),
        ("variable:work.out.y", "proc_sql_select_column", None, None),
    }
    assert ctx.findings == []


def test_insert_dataset_options_keep_select_name_writes():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  insert into work.out(compress=yes) select a, b from t;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.t.a", "proc_sql_select_column", None, None),
        ("variable:work.t.b", "proc_sql_select_column", None, None),
    }
    assert writes == {
        ("variable:work.out.a", "proc_sql_select_column", None, None),
        ("variable:work.out.b", "proc_sql_select_column", None, None),
    }
    assert ctx.findings == []


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

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_dataset"}
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


def test_resolved_macro_in_select_expression_is_scanned_as_a_literal():
    blocks, ctx, events = build(
        "%let n=1;\n"
        "proc sql;\n"
        "  create table work.out as\n"
        "  select %unquote(&n) as flag\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    sql_statement = next(node for node in ctx.nodes if node["type"] == "SqlStatement")
    assert reads == set()
    assert writes == {(
        sql_statement["id"], "variable:work.out.flag", "1", None,
    )}
    assert not ctx.findings
    assert {
        node["id"] for node in ctx.nodes
        if node["type"] in ("Variable", "UnknownVariable")
    } == {"variable:work.out.flag"}


def test_unresolved_macro_in_select_expression_stays_unknown():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select &missing as flag\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    sql_statement = next(node for node in ctx.nodes if node["type"] == "SqlStatement")
    unknowns = [node for node in ctx.nodes if node["type"] == "UnknownVariable"]
    assert len(unknowns) == 1
    assert unknowns[0]["unresolved_expression"] == "missing"
    assert {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    } == {(unknowns[0]["id"], sql_statement["id"], None, None)}
    assert {
        (edge["from"], edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    } == {(
        sql_statement["id"], "variable:work.out.flag", None, None,
    )}
    assert {
        (finding["type"], finding["status"], finding["severity"])
        for finding in ctx.findings
    } == {("unqualified_variable_reference", "UNQUALIFIED_VARIABLE", "WARNING")}


def test_macro_resolution_respects_single_and_double_quoted_select_literals():
    blocks, ctx, events = build(
        "%let dose=999;\n"
        "proc sql;\n"
        "  create table work.out as\n"
        "  select 'the &dose is \"literal\", not macro' as single_note,\n"
        "         \"the &dose is 'literal', not macro\" as double_note,\n"
        "         &dose as bare_dose\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    sql_statement = next(node for node in ctx.nodes if node["type"] == "SqlStatement")
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
        (sql_statement["id"], "variable:work.out.single_note", 'the &dose is "literal", not macro', None),
        (sql_statement["id"], "variable:work.out.double_note", "the 999 is 'literal', not macro", None),
        (sql_statement["id"], "variable:work.out.bare_dose", "999", None),
    }
    assert not ctx.findings


def test_group_having_order_by_extracts_all_clause_column_reads():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select count(id) as n\n"
        "  from work.in\n"
        "  group by trt\n"
        "  having trt > 0\n"
        "  order by score desc;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.in.id", "proc_sql_select_expression", None, None),
        ("variable:work.in.trt", "proc_sql_group_by", None, None),
        ("variable:work.in.trt", "proc_sql_having_condition", "0", ">"),
        ("variable:work.in.score", "proc_sql_order_by", None, None),
    }
    assert writes == {("variable:work.out.n", None, None)}
    assert ctx.findings == []


def test_group_and_order_by_multi_column_lists_read_every_column():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select id\n"
        "  from work.in\n"
        "  group by trt, sex\n"
        "  order by score desc, visit asc;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.in.id", "proc_sql_select_column"),
        ("variable:work.in.trt", "proc_sql_group_by"),
        ("variable:work.in.sex", "proc_sql_group_by"),
        ("variable:work.in.score", "proc_sql_order_by"),
        ("variable:work.in.visit", "proc_sql_order_by"),
    }
    assert writes == {("variable:work.out.id", None, None)}
    assert ctx.findings == []


def test_mixed_group_by_list_keeps_valid_reads_before_unsupported_item():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select id\n"
        "  from work.in\n"
        "  group by trt, sex, calculated n;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    findings = {
        (finding["type"], finding["source"]["rule"], finding["message"])
        for finding in ctx.findings
    }
    assert reads == {
        ("variable:work.in.id", "proc_sql_select_column", None, None),
        ("variable:work.in.trt", "proc_sql_group_by", None, None),
        ("variable:work.in.sex", "proc_sql_group_by", None, None),
    }
    assert writes == {("variable:work.out.id", None, None)}
    assert findings == {
        (
            "proc_sql_unsupported_syntax",
            "proc_sql_group_by",
            "GROUP BY item `calculated n` is not a bare column reference; it was not parsed.",
        ),
    }


def test_unsupported_having_produces_finding_without_fabricated_edges():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select id\n"
        "  from work.in\n"
        "  having trt > 0 and score > 1;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    findings = {
        (finding["type"], finding["source"]["rule"])
        for finding in ctx.findings
    }
    assert reads == {
        ("variable:work.in.id", "proc_sql_select_column", None, None),
    }
    assert writes == {("variable:work.out.id", None, None)}
    assert findings == {
        ("proc_sql_unsupported_syntax", "proc_sql_having_condition"),
    }


def test_select_and_where_variable_edges_remain_unchanged_with_clause_dispatch():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.id + 1 as id_plus\n"
        "  from work.in as a\n"
        "  where a.flag = 'Y';\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.in.id", "proc_sql_select_expression", None, None),
        ("variable:work.in.flag", "proc_sql_where_condition", "Y", "="),
    }
    assert writes == {("variable:work.out.id_plus", None, None)}
    assert ctx.findings == []


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


def test_select_bare_column_creates_read_and_write_variable_edges():
    """Ticket sql-select-alias-variable-edges, acceptance criterion 1."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.ae1 as\n"
        "  select aeterm\n"
        "  from sdtm.ae;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    sql_statement = next(n for n in ctx.nodes if n["type"] == "SqlStatement")
    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]

    assert len(reads) == 1 and len(writes) == 1
    assert reads[0]["from"] == "variable:sdtm.ae.aeterm"
    assert reads[0]["to"] == sql_statement["id"]
    assert reads[0]["value"] is None and reads[0]["operator"] is None
    assert reads[0]["source"]["rule"] == "proc_sql_select_column"

    assert writes[0]["from"] == sql_statement["id"]
    assert writes[0]["to"] == "variable:work.ae1.aeterm"
    assert writes[0]["value"] is None and writes[0]["operator"] is None

    variable_ids = {n["id"] for n in ctx.nodes if n["type"] == "Variable"}
    assert {"variable:sdtm.ae.aeterm", "variable:work.ae1.aeterm"} <= variable_ids


def test_select_qualified_column_resolves_via_join_alias_table():
    """Ticket sql-select-alias-variable-edges, acceptance criterion 2."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.aeterm, b.usubjid\n"
        "  from sdtm.ae as a left join sdtm.dm as b on a.usubjid=b.usubjid;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}

    assert reads == {"variable:sdtm.ae.aeterm", "variable:sdtm.dm.usubjid"}
    assert writes == {"variable:work.out.aeterm", "variable:work.out.usubjid"}
    assert not any(n["type"] == "UnknownVariable" for n in ctx.nodes)


def test_select_unqualified_column_with_two_sources_is_ambiguous():
    """Ticket sql-select-alias-variable-edges, acceptance criterion 3."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select usubjid\n"
        "  from sdtm.ae as a left join sdtm.dm as b on a.usubjid=b.usubjid;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    unknowns = [n for n in ctx.nodes if n["type"] == "UnknownVariable"]
    assert len(unknowns) == 1
    assert unknowns[0]["unresolved_expression"] == "usubjid"

    findings = [f for f in ctx.findings if f["type"] == "unqualified_variable_reference"]
    assert len(findings) == 1
    assert findings[0]["status"] == "UNQUALIFIED_VARIABLE"
    assert findings[0]["severity"] == "WARNING"

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    assert reads[0]["from"] == unknowns[0]["id"]

    # The write side still happens -- only the source resolution is ambiguous.
    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]
    assert writes[0]["to"] == "variable:work.out.usubjid"


def test_select_star_produces_finding_and_no_variable_edges():
    """Ticket sql-select-alias-variable-edges, acceptance criterion 4."""
    blocks, ctx, events = build(
        "proc sql;\n  create table work.ae1 as select * from sdtm.ae;\nquit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    star_findings = [f for f in ctx.findings if f["type"] == "proc_sql_select_star_unsupported"]
    assert len(star_findings) == 1
    assert star_findings[0]["status"] == "NOT_EXECUTED"
    assert star_findings[0]["severity"] == "WARNING"
    assert not any(e["type"] in ("reads_variable", "writes_variable") for e in ctx.edges)
    assert not any(n["type"] in ("Variable", "UnknownVariable") for n in ctx.nodes)


def test_select_alias_star_uses_wildcard_finding_and_keeps_other_columns():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.*, a.x as y\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    star_findings = [
        f for f in ctx.findings if f["type"] == "proc_sql_select_star_unsupported"
    ]
    assert len(star_findings) == 1
    assert not any(
        f["type"] == "proc_sql_select_expression_unaliased" for f in ctx.findings
    )
    assert any(
        e["type"] == "reads_variable" and e["from"] == "variable:work.a.x"
        for e in ctx.edges
    )
    assert any(
        e["type"] == "writes_variable" and e["to"] == "variable:work.out.y"
        for e in ctx.edges
    )


def test_select_list_boundary_ignores_nested_scalar_from_and_keeps_following_column():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.*, (select h.x from work.h as h where h.id = a.id) as scalar_x,\n"
        "         a.after as after_col\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert len([
        f for f in ctx.findings if f["type"] == "proc_sql_select_star_unsupported"
    ]) == 1
    assert not any(
        f["type"] == "proc_sql_select_expression_unaliased" for f in ctx.findings
    )
    assert {
        e["from"] for e in ctx.edges if e["type"] == "reads_dataset"
    } == {"dataset:work.a"}
    assert {
        e["to"] for e in ctx.edges if e["type"] == "writes_dataset"
    } == {"dataset:work.out"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert {"variable:work.out.scalar_x", "variable:work.out.after_col"} <= writes


def test_select_list_boundary_keeps_multiple_case_subquery_columns():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select case when a.flag1 = 'Y' then (select h.value from work.h as h where h.id = a.id)\n"
        "              else '' end as out1 length=1,\n"
        "         case when a.flag2 = 'Y' then (select q.value from work.q as q where q.id = a.id)\n"
        "              else '' end as out2 length=1\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert {"variable:work.out.out1", "variable:work.out.out2"} <= writes
    assert not any(
        f["type"] == "proc_sql_select_expression_unaliased" for f in ctx.findings
    )
    assert {
        e["from"] for e in ctx.edges if e["type"] == "reads_dataset"
    } == {"dataset:work.a"}


def test_nested_exists_columns_keep_unsupported_findings_on_their_own_text():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.*,\n"
        "         case when a.trtemfl = 'Y' and exists (select 1 from work.h as h where a.x = h.x)\n"
        "              then 'Y' else '' end as flag1 length=1,\n"
        "         case when a.trtemfl = 'Y' and exists (select 1 from work.q as q where a.y = q.y)\n"
        "              then 'Y' else '' end as flag2 length=1\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    case_findings = [
        f for f in ctx.findings
        if f["type"] == "proc_sql_unsupported_syntax"
        and f["source"]["rule"] == "proc_sql_select_case"
    ]
    assert len(case_findings) == 2
    messages = [f["message"] for f in case_findings]
    assert any("a.x = h.x" in message and "a.y = q.y" not in message for message in messages)
    assert any("a.y = q.y" in message and "a.x = h.x" not in message for message in messages)
    assert not any(
        f["type"] == "proc_sql_select_expression_unaliased" for f in ctx.findings
    )
    assert {
        e["from"] for e in ctx.edges
        if e["type"] == "reads_dataset"
    } == {"dataset:work.a"}
    assert {
        e["to"] for e in ctx.edges
        if e["type"] == "writes_dataset"
    } == {"dataset:work.out"}
    assert {
        e["from"] for e in ctx.edges
        if e["type"] == "reads_variable"
    } == set()
    assert {
        e["to"] for e in ctx.edges
        if e["type"] == "writes_variable"
    } == set()
    assert [
        (finding["type"], finding["source"]["rule"])
        for finding in ctx.findings
    ] == [
        ("proc_sql_select_star_unsupported", "proc_sql_select_star_unsupported"),
        ("proc_sql_unsupported_syntax", "proc_sql_select_case"),
        ("proc_sql_unsupported_syntax", "proc_sql_select_case"),
    ]


def test_nested_subquery_boundaries_use_outer_where_clause():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.*,\n"
        "         case when a.flag = 'Y' and exists (select 1 from work.h as h where a.id = h.id)\n"
        "              then 'Y' else '' end as flag length=1\n"
        "  from work.a as a\n"
        "  where a.keep = 'Y';\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert {
        e["from"] for e in ctx.edges if e["type"] == "reads_dataset"
    } == {"dataset:work.a"}
    where_reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges
        if e["type"] == "reads_variable"
        and e["source"]["rule"] == "proc_sql_where_condition"
    }
    assert where_reads == {("variable:work.a.keep", "Y", "=")}
    assert not any(
        finding["source"]["rule"] == "proc_sql_where_condition"
        for finding in ctx.findings
    )


def test_top_level_boundaries_ignore_paren_inside_quoted_select_literal():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select 'has (paren' as label,\n"
        "         a.keep\n"
        "  from work.a as a\n"
        "  where a.keep = 'Y';\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert {
        e["from"] for e in ctx.edges if e["type"] == "reads_dataset"
    } == {"dataset:work.a"}
    assert {
        e["to"] for e in ctx.edges if e["type"] == "writes_dataset"
    } == {"dataset:work.out"}
    where_reads = {
        (e["from"], e["value"], e["operator"])
        for e in ctx.edges
        if e["type"] == "reads_variable"
        and e["source"]["rule"] == "proc_sql_where_condition"
    }
    assert where_reads == {("variable:work.a.keep", "Y", "=")}
    assert not any(
        finding["source"]["rule"] == "proc_sql_where_condition"
        for finding in ctx.findings
    )


def test_parenthesized_from_dataset_option_keeps_existing_decline():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as select a.x\n"
        "  from work.a(keep=x) as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert not any(n["id"] == "dataset:work.a" for n in ctx.nodes)
    assert any(
        finding["type"] == "proc_sql_unsupported_syntax"
        and "work.a(keep=x) as a" in finding["message"]
        for finding in ctx.findings
    )


def test_select_computed_expression_creates_reads_for_each_variable():
    """Ticket sql-select-alias-variable-edges, acceptance criterion 5."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.var1 + a.var2 as total\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]

    assert {e["from"] for e in reads} == {"variable:work.a.var1", "variable:work.a.var2"}
    assert all(e["value"] is None and e["operator"] is None for e in reads)
    assert all(e["source"]["rule"] == "proc_sql_select_expression" for e in reads)

    assert len(writes) == 1
    assert writes[0]["to"] == "variable:work.out.total"
    assert writes[0]["value"] is None and writes[0]["operator"] is None


def test_select_computed_alias_with_length_attribute_writes_real_alias():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select coalesce(a.studyid, b.studyid) as studyid length=200\n"
        "  from work.a as a, work.b as b;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert reads == {"variable:work.a.studyid", "variable:work.b.studyid"}
    assert writes == {"variable:work.out.studyid"}
    assert not any(
        f["type"] == "proc_sql_select_expression_unaliased" for f in ctx.findings
    )


def test_select_bare_alias_with_length_attribute_writes_real_alias():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.visit as ecvisit length=200\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert reads == {"variable:work.a.visit"}
    assert writes == {"variable:work.out.ecvisit"}


def test_select_case_alias_with_length_attribute_writes_real_alias():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select case when a.flag = 'Y' then 'Y' else '' end as outflag length=1\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert reads == {"variable:work.a.flag"}
    assert writes == {"variable:work.out.outflag"}
    assert not any(
        f["type"] == "proc_sql_select_expression_unaliased" for f in ctx.findings
    )


def test_select_alias_format_and_label_attributes_are_recognized():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.visit as formatted format=$20.,\n"
        "         a.visit as labeled label='Visit'\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert writes == {
        "variable:work.out.formatted",
        "variable:work.out.labeled",
    }


def test_select_alias_without_attribute_clause_still_writes_alias():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.visit as ecvisit\n"
        "  from work.a as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert writes == {"variable:work.out.ecvisit"}


def test_select_computed_expression_without_alias_still_produces_finding():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select coalesce(a.studyid, b.studyid)\n"
        "  from work.a as a, work.b as b;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    findings = [
        f for f in ctx.findings
        if f["type"] == "proc_sql_select_expression_unaliased"
    ]
    assert len(findings) == 1
    assert not any(e["type"] == "writes_variable" for e in ctx.edges)


def test_select_literal_suffixes_and_special_missing_do_not_create_reads():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select '01JAN2020:12:30'dt as date_out, '534153'x as hex_out, "
        ".A as missing_out, id\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    writes = {e["to"] for e in ctx.edges if e["type"] == "writes_variable"}
    assert reads == {"variable:work.in.id"}
    assert writes == {
        "variable:work.out.date_out",
        "variable:work.out.hex_out",
        "variable:work.out.missing_out",
        "variable:work.out.id",
    }
    assert not any(
        n["id"].rsplit(".", 1)[-1] in {"dt", "x", "a"}
        for n in ctx.nodes
        if n["type"] in ("Variable", "UnknownVariable")
    )


def test_select_format_and_informat_names_are_not_source_reads():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select input(y, best32.) as converted, put(y, yymmdd10.) as formatted, id\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {e["from"] for e in ctx.edges if e["type"] == "reads_variable"}
    assert reads == {"variable:work.in.y", "variable:work.in.id"}
    assert not any(
        n["id"].rsplit(".", 1)[-1] in {"best32", "yymmdd10"}
        for n in ctx.nodes
        if n["type"] in ("Variable", "UnknownVariable")
    )


def test_select_comma_separated_from_list_makes_unqualified_column_ambiguous():
    """codex-review fix: a comma-separated FROM list (no explicit JOIN) must
    be recognized as more than one source table, same as an explicit JOIN."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select usubjid\n"
        "  from sdtm.ae as a, sdtm.dm as b;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    unknowns = [n for n in ctx.nodes if n["type"] == "UnknownVariable"]
    assert len(unknowns) == 1
    assert unknowns[0]["unresolved_expression"] == "usubjid"
    assert {e["from"] for e in ctx.edges if e["type"] == "reads_dataset"} == {
        "dataset:sdtm.ae", "dataset:sdtm.dm"
    }


def test_select_comma_separated_from_list_resolves_qualified_column():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select b.usubjid\n"
        "  from sdtm.ae as a, sdtm.dm as b;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    assert reads[0]["from"] == "variable:sdtm.dm.usubjid"


def test_select_distinct_bare_column_creates_read_and_write_variable_edges():
    """codex-review fix: DISTINCT is a SELECT-list modifier, not part of the
    first column's own expression."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select distinct a.usubjid\n"
        "  from sdtm.ae as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]
    assert reads[0]["from"] == "variable:sdtm.ae.usubjid"
    assert writes[0]["to"] == "variable:work.out.usubjid"
    assert not any(n["type"] == "UnknownVariable" for n in ctx.nodes)


def test_select_distinct_aggregate_does_not_read_reserved_word():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select distinct x, count(distinct y) as n\n"
        "  from work.in as t;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    assert {e["from"] for e in reads} == {
        "variable:work.in.x", "variable:work.in.y"
    }
    assert not any(
        n["id"] in {"variable:work.in.distinct", "variable:work.in.t.distinct"}
        for n in ctx.nodes
    )


def test_select_all_does_not_read_reserved_word():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select all x\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    assert [e["from"] for e in reads] == ["variable:work.in.x"]
    assert not any(n["id"] == "variable:work.in.all" for n in ctx.nodes)


def test_select_unique_strips_modifier_and_preserves_column_edges():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select unique id as id_out\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]
    assert [e["from"] for e in reads] == ["variable:work.in.id"]
    assert [e["to"] for e in writes] == ["variable:work.out.id_out"]
    assert not any(n["id"] == "variable:work.in.unique" for n in ctx.nodes)


def test_select_calculated_excludes_keyword_and_alias_as_source_reads():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select (x+1) as high, (calculated high+2) as range\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]
    assert [e["from"] for e in reads] == ["variable:work.in.x"]
    assert {e["to"] for e in writes} == {
        "variable:work.out.high", "variable:work.out.range"
    }
    assert not any(
        e["from"] in {"variable:work.in.calculated", "variable:work.in.high"}
        for e in reads
    )


def test_select_user_does_not_read_reserved_word():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select user as owner\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert not any(e["type"] == "reads_variable" for e in ctx.edges)
    assert any(
        e["type"] == "writes_variable" and e["to"] == "variable:work.out.owner"
        for e in ctx.edges
    )
    assert not any(n["id"] == "variable:work.in.user" for n in ctx.nodes)


def test_select_null_comparison_does_not_read_reserved_word():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select 3 > null as null_cmp\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert not any(e["type"] == "reads_variable" for e in ctx.edges)
    assert any(
        e["type"] == "writes_variable" and e["to"] == "variable:work.out.null_cmp"
        for e in ctx.edges
    )
    assert not any(n["id"] == "variable:work.in.null" for n in ctx.nodes)


def test_select_bare_literal_expression_captures_value_like_a_data_step_assignment():
    """codex-review fix: `select 'Y' as flag` is the SQL analogue of a
    DATA-step `flag = 'Y';`, which captures `value`; masking must not lose
    the literal before the edge is minted."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select 'Y' as flag\n"
        "  from sdtm.ae as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]
    assert writes[0]["to"] == "variable:work.out.flag"
    assert writes[0]["value"] == "Y"
    assert not any(e["type"] == "reads_variable" for e in ctx.edges)
    assert not any(
        f["type"] == "proc_sql_select_expression_unaliased" for f in ctx.findings
    )


def test_select_name_literal_column_produces_finding_and_no_fabricated_edges():
    """codex-review fix: SAS name-literal syntax (`'odd name'n`) is not
    parsed -- must not silently split into stray bare identifiers."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select a.'odd name'n as outcol\n"
        "  from sdtm.ae as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    findings = [f for f in ctx.findings if f["type"] == "proc_sql_unsupported_syntax"]
    assert len(findings) == 1
    assert findings[0]["status"] == "NOT_EXECUTED"
    assert findings[0]["severity"] == "WARNING"
    assert not any(e["type"] in ("reads_variable", "writes_variable") for e in ctx.edges)
    assert not any(n["type"] in ("Variable", "UnknownVariable") for n in ctx.nodes)


def test_select_from_function_call_source_produces_finding_not_a_fake_dataset():
    """codex-review fix: a function-call-shaped FROM source must not be
    truncated into a fabricated dataset name."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select f.x\n"
        "  from myfunc('arg') as f;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    assert not any(n["id"] == "dataset:work.myfunc" for n in ctx.nodes)
    findings = [f for f in ctx.findings if f["type"] == "proc_sql_unsupported_syntax"]
    assert len(findings) == 1


def test_where_literal_comparison_creates_reads_variable_edge_with_value_and_operator():
    """Ticket sql-where-case-variable-edges, acceptance criterion 1."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.ae1 as\n"
        "  select aeterm\n"
        "  from sdtm.ae\n"
        "  where aeser = 'Y';\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    where_reads = [
        e for e in ctx.edges
        if e["type"] == "reads_variable" and e["source"]["rule"] == "proc_sql_where_condition"
    ]
    assert len(where_reads) == 1
    assert where_reads[0]["from"] == "variable:sdtm.ae.aeser"
    assert where_reads[0]["value"] == "Y"
    assert where_reads[0]["operator"] == "="

    # The SELECT column still resolves independently of the WHERE predicate.
    select_reads = [
        e for e in ctx.edges
        if e["type"] == "reads_variable" and e["source"]["rule"] == "proc_sql_select_column"
    ]
    assert select_reads[0]["from"] == "variable:sdtm.ae.aeterm"


def test_case_when_creates_reads_and_writes_variable_edges():
    """Ticket sql-where-case-variable-edges, acceptance criterion 2."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select case when a.aeser = 'Y' then 'Serious' else 'Not Serious' end as aesev\n"
        "  from sdtm.ae as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = [e for e in ctx.edges if e["type"] == "reads_variable"]
    writes = [e for e in ctx.edges if e["type"] == "writes_variable"]

    assert len(reads) == 1
    assert reads[0]["from"] == "variable:sdtm.ae.aeser"
    assert reads[0]["value"] == "Y"
    assert reads[0]["operator"] == "="
    assert reads[0]["source"]["rule"] == "proc_sql_select_case"

    assert len(writes) == 1
    assert writes[0]["to"] == "variable:work.out.aesev"
    assert writes[0]["value"] is None and writes[0]["operator"] is None


def test_case_then_else_result_expressions_read_their_variables():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select case when status = 'Y' then cats(prefix, id) else fallback end as label\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.in.status", "proc_sql_select_case", "Y", "="),
        ("variable:work.in.prefix", "proc_sql_select_case", None, None),
        ("variable:work.in.id", "proc_sql_select_case", None, None),
        ("variable:work.in.fallback", "proc_sql_select_case", None, None),
    }
    assert writes == {("variable:work.out.label", None, None)}
    assert ctx.findings == []


def test_case_literal_then_else_results_do_not_create_reads():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select case when status = 'Y' then 'Y' else '' end as label\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.in.status", "proc_sql_select_case", "Y", "="),
    }
    assert writes == {("variable:work.out.label", None, None)}
    assert ctx.findings == []


def test_case_without_else_still_reads_then_expression():
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select case when status = 'Y' then fallback end as label\n"
        "  from work.in;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    reads = {
        (edge["from"], edge["source"]["rule"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "reads_variable"
    }
    writes = {
        (edge["to"], edge["value"], edge["operator"])
        for edge in ctx.edges if edge["type"] == "writes_variable"
    }
    assert reads == {
        ("variable:work.in.status", "proc_sql_select_case", "Y", "="),
        ("variable:work.in.fallback", "proc_sql_select_case", None, None),
    }
    assert writes == {("variable:work.out.label", None, None)}
    assert ctx.findings == []


def test_where_in_predicate_produces_finding_and_no_fabricated_edge():
    """Ticket sql-where-case-variable-edges, acceptance criterion 3."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select aeterm\n"
        "  from sdtm.ae\n"
        "  where aeser in ('Y','N');\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    findings = [f for f in ctx.findings if f["type"] == "proc_sql_unsupported_syntax"]
    assert len(findings) == 1
    assert findings[0]["status"] == "NOT_EXECUTED"
    assert findings[0]["severity"] == "WARNING"
    assert not any(
        e["type"] == "reads_variable" and e["from"] == "variable:sdtm.ae.aeser"
        for e in ctx.edges
    )


def test_case_multi_when_fallthrough_produces_finding_and_no_fabricated_edge():
    """Ticket sql-where-case-variable-edges, acceptance criterion 3."""
    blocks, ctx, events = build(
        "proc sql;\n"
        "  create table work.out as\n"
        "  select case when a.aeser='Y' then 'A' when a.aeser='N' then 'B' else 'C' end as aesev\n"
        "  from sdtm.ae as a;\n"
        "quit;\n"
    )
    rules_proc_sql.apply(blocks[0], ctx, events)

    findings = [f for f in ctx.findings if f["type"] == "proc_sql_unsupported_syntax"]
    assert len(findings) == 1
    assert not any(e["type"] in ("reads_variable", "writes_variable") for e in ctx.edges)
    assert not any(n["id"] == "variable:work.out.aesev" for n in ctx.nodes)


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("proc sql rules: all checks passed")


if __name__ == "__main__":
    demo()
