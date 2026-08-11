"""LIBNAME rule (dev plan section 11.4, 21 Phase 4)."""

import conftest  # noqa: F401

from sas_graph import rules_libname
from sas_graph.blocks import group_blocks
from sas_graph.graph_model import GraphContext
from sas_graph.macro_state import walk_let_statements
from sas_graph.statements import split_statements


def build(text, file_name="setup.sas"):
    result = split_statements(text, file_name)
    _, unattached = group_blocks(result.statements)
    events = walk_let_statements(result.statements)
    ctx = GraphContext(main_programs=["adae.sas"], setup_file=file_name, run_id="r1")
    return unattached, ctx, events


def test_section_22_fixture_libname_creates_library_node_and_resolves_path():
    """Section 22's own setup.sas example."""
    statements, ctx, events = build(
        '%let root = /study/demo;\nlibname sdtm "&root./sdtm";\n'
    )
    libname_statement = statements[1]
    assert rules_libname.is_libname(libname_statement.text)

    rules_libname.apply(libname_statement, ctx, events)

    node = next(n for n in ctx.nodes if n["id"] == "library:sdtm")
    assert node["type"] == "Library"
    assert node["path_expression"] == '"&root./sdtm"'
    assert node["resolved_path"] == "/study/demo/sdtm"
    assert ctx.libref_map["sdtm"] == "/study/demo/sdtm"


def test_libname_with_unresolved_macro_variable_is_flagged():
    statements, ctx, events = build('libname sdtm "&missing./sdtm";\n')
    rules_libname.apply(statements[0], ctx, events)

    assert any(f["status"] == "UNRESOLVED_MACRO_VARIABLE" for f in ctx.findings)


def test_xlsx_engine_libname_creates_externalfile_node_and_keeps_libref_mapping():
    statements, ctx, events = build(
        'libname xlsxlib XLSX "/data/raw/lookup.xlsx" access=readonly;\n'
    )
    statement = statements[0]
    assert rules_libname.is_libname(statement.text)

    rules_libname.apply(statement, ctx, events)

    external_file = next(n for n in ctx.nodes if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:/data/raw/lookup.xlsx"
    assert external_file["source"] is None
    assert not any(n["type"] == "Library" for n in ctx.nodes)
    assert ctx.libref_map["xlsxlib"] == "/data/raw/lookup.xlsx"


def test_xlsx_engine_libname_with_unresolved_path_keeps_externalfile_evidence():
    statements, ctx, events = build('libname x XLSX "&missing./lookup.xlsx";\n')
    rules_libname.apply(statements[0], ctx, events)

    external_file = next(n for n in ctx.nodes if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:&missing./lookup.xlsx"
    assert any(f["status"] == "UNRESOLVED_MACRO_VARIABLE" for f in ctx.findings)


def test_path_keyed_and_connection_libname_engines_remain_unmatched():
    for statement_text in (
        'libname x PCFILES path="/data/raw/lookup.xlsx"; ',
        'libname x EXCEL path="/data/raw/lookup.xlsx"; ',
        'libname x ODBC dsn="clinical"; ',
        'libname x OLEDB provider="provider"; ',
    ):
        assert rules_libname.is_libname(statement_text) is False


def test_is_libname_false_for_other_statements():
    assert rules_libname.is_libname("data work.a;") is False
    assert rules_libname.is_libname("%let x = 1;") is False


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("libname rule: all checks passed")


if __name__ == "__main__":
    demo()
