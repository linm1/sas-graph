"""Standalone tests for the PROC SQL IR parser, resolver, and emitter."""

import ast

import conftest  # noqa: F401

from sas_graph import sql_ir
from sas_graph.graph_model import GraphContext
from sas_graph.ir import IRComparison, IRLiteral, IRVariableRef
from sas_graph.macro_state import walk_let_statements
from sas_graph.statements import split_statements
from sas_graph.blocks import group_blocks
from sas_graph import rules_proc_sql


def source(text="select a.id from work.a as a", order=1):
    return {
        "file": "sql.sas",
        "line_start": 1,
        "line_end": 1,
        "statement_order": order,
        "original_text": text,
        "rule": "proc_sql_create_table",
    }


def parse(text, events=()):
    return sql_ir.parse_sql(
        text,
        "CREATE_TABLE",
        "work.out",
        source(text, order=2),
        events,
    )


def test_parse_reuses_common_ir_for_columns_literals_and_predicates():
    ir = parse(
        "create table work.out as select a.id, 'Y' as flag "
        "from work.a as a where a.id = 1"
    )

    assert isinstance(ir.select_items[0].expression, IRVariableRef)
    assert isinstance(ir.select_items[1].expression, IRLiteral)
    assert isinstance(ir.where, IRComparison)
    assert isinstance(ir.where.left, IRVariableRef)
    assert isinstance(ir.where.right, IRLiteral)


def test_parse_records_explicit_join_kind_case_group_and_order():
    ir = parse(
        "create table work.out as "
        "select case when a.flag = 'Y' then b.value else 0 end as label "
        "from work.a as a left join work.b as b on a.id = b.id "
        "group by a.flag order by b.value desc"
    )

    assert len(ir.sources) == 2
    assert ir.joins[0].kind == "LEFT"
    assert isinstance(ir.joins[0].predicate, IRComparison)
    assert isinstance(ir.select_items[0].expression, sql_ir.IRCaseExpression)
    assert ir.group_by[0].name == "a.flag"
    assert ir.order_by[0].direction == "DESC"


def test_resolver_keeps_ambiguous_unqualified_reference_unbound():
    ir = parse(
        "create table work.out as select id from work.a as a, work.b as b"
    )
    resolved = sql_ir.resolve_sql(
        ir,
        ("dataset:work.a", "dataset:work.b"),
        {"a": "dataset:work.a", "b": "dataset:work.b"},
    )

    binding = resolved.select_items[0].expression.bindings[0]
    assert binding.exact is False
    assert binding.dataset_id is None
    assert binding.candidates == ("dataset:work.a", "dataset:work.b")


def test_resolver_binds_qualified_reference_through_statement_alias():
    ir = parse("create table work.out as select b.id from work.a as a, work.b as b")
    resolved = sql_ir.resolve_sql(
        ir,
        ("dataset:work.a", "dataset:work.b"),
        {"a": "dataset:work.a", "b": "dataset:work.b"},
    )

    binding = resolved.select_items[0].expression.bindings[0]
    assert binding.exact is True
    assert binding.dataset_id == "dataset:work.b"
    assert binding.variable_name == "id"


def test_emit_sql_new_edges_require_exact_bindings_and_keep_unknown_finding():
    text = (
        "proc sql; create table work.out as select a.id "
        "from work.a as a left join work.b as b on a.id=b.id "
        "where a.flag='Y' group by a.id order by a.id; quit;"
    )
    split = split_statements(text, "sql.sas")
    blocks, _ = group_blocks(split.statements)
    ctx = GraphContext(main_programs=["sql.sas"], setup_file="setup.sas", run_id="r1")
    rules_proc_sql.apply(blocks[0], ctx, walk_let_statements(split.statements))

    edge_types = {edge["type"] for edge in ctx.edges}
    assert {"joins_on", "filters_dataset", "groups_by", "sorts_by"} <= edge_types

    ambiguous = (
        "proc sql; create table work.out as select id from work.a as a, "
        "work.b as b where id=1; quit;"
    )
    split = split_statements(ambiguous, "sql.sas")
    blocks, _ = group_blocks(split.statements)
    ctx = GraphContext(main_programs=["sql.sas"], setup_file="setup.sas", run_id="r2")
    rules_proc_sql.apply(blocks[0], ctx, walk_let_statements(split.statements))

    assert not any(edge["type"] == "filters_dataset" for edge in ctx.edges)
    assert any(f["type"] == "unqualified_variable_reference" for f in ctx.findings)
    assert any(node["type"] == "UnknownVariable" for node in ctx.nodes)


def test_emit_function_has_no_regex_calls():
    tree = ast.parse(open(sql_ir.__file__, encoding="utf-8").read())
    emit = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "emit_sql")
    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "re"
        for node in ast.walk(emit)
    )


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print("ok  {}".format(name))
    print("sql ir: all checks passed")


if __name__ == "__main__":
    demo()
