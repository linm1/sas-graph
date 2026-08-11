"""PROC IMPORT rules (external-file-sources ticket 01)."""

import conftest  # noqa: F401

from sas_graph import rules_proc_import
from sas_graph.blocks import group_blocks
from sas_graph.graph_model import GraphContext
from sas_graph.macro_state import walk_let_statements
from sas_graph.statements import split_statements


def build(text, file_name="main.sas"):
    result = split_statements(text, file_name)
    blocks, _ = group_blocks(result.statements)
    events = walk_let_statements(result.statements)
    ctx = GraphContext(main_programs=[file_name], setup_file="setup.sas", run_id="r1")
    return blocks, ctx, events


def test_basic_import_creates_externalfile_node_and_reads_external_file_edge():
    """`sheet=`/`getnames=` can be on their own block-body lines, not the opener."""
    blocks, ctx, events = build(
        'proc import datafile="/data/raw/ae.xlsx"\n'
        "    out=work.ae_raw\n"
        "    dbms=xlsx replace;\n"
        "    getnames=no;\n"
        '    sheet="Active PT Codes";\n'
        "run;\n"
    )
    rules_proc_import.apply(blocks[0], ctx, events)

    external_file = next(n for n in ctx.nodes if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:/data/raw/ae.xlsx"
    assert external_file["source"] is None

    edge = next(e for e in ctx.edges if e["type"] == "reads_external_file")
    assert edge["from"] == "externalfile:/data/raw/ae.xlsx"
    assert edge["to"] == "dataset:work.ae_raw"
    assert edge["dbms"] == "xlsx"
    assert edge["sheet"] == "Active PT Codes"
    assert edge["getnames"] == "no"


def test_out_option_dataset_options_in_parens_do_not_leak_into_dataset_id():
    """Dataset options following `out=` must not leak into the dataset id."""
    blocks, ctx, events = build(
        'proc import datafile="/data/raw/ae.xlsx"\n'
        "    out=smq_ptcounts_raw(keep=A B C)\n"
        "    dbms=xlsx replace;\n"
        "run;\n"
    )
    rules_proc_import.apply(blocks[0], ctx, events)

    edge = next(e for e in ctx.edges if e["type"] == "reads_external_file")
    assert edge["to"] == "dataset:work.smq_ptcounts_raw"


def test_out_literal_inside_quoted_datafile_path_is_not_mistaken_for_out_option():
    """Regression (reviewer-caught HIGH): a raw quoted DATAFILE= path can
    legitimately contain the literal text "out=" as part of a filename --
    that must never be misread as PROC IMPORT's own OUT= option. Before the
    fix, `_OUT_RE` matched inside the quoted DATAFILE= span itself and
    produced `dataset:x.xlsx` instead of the real `out=work.a`."""
    blocks, ctx, events = build(
        'proc import datafile="/data/out=x.xlsx" out=work.a dbms=xlsx replace;\n'
        "run;\n"
    )
    rules_proc_import.apply(blocks[0], ctx, events)

    external_file = next(n for n in ctx.nodes if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:/data/out=x.xlsx"

    edge = next(e for e in ctx.edges if e["type"] == "reads_external_file")
    assert edge["to"] == "dataset:work.a"
    assert "dataset:x.xlsx" not in {n["id"] for n in ctx.nodes}


def test_multiple_sheets_of_one_spreadsheet_collapse_to_one_externalfile_node():
    """Ticket 01's own requirement: node id is DATAFILE= alone, SHEET= is an
    edge attribute, so two imports of the same file share one node."""
    blocks, ctx, events = build(
        'proc import datafile="/data/raw/ae.xlsx" out=work.a dbms=xlsx replace;\n'
        '  sheet="Sheet1";\n'
        "run;\n"
        'proc import datafile="/data/raw/ae.xlsx" out=work.b dbms=xlsx replace;\n'
        '  sheet="Sheet2";\n'
        "run;\n"
    )
    rules_proc_import.apply(blocks[0], ctx, events)
    rules_proc_import.apply(blocks[1], ctx, events)

    external_files = [n for n in ctx.nodes if n["type"] == "ExternalFile"]
    assert len(external_files) == 1

    edges = {e["to"]: e["sheet"] for e in ctx.edges if e["type"] == "reads_external_file"}
    assert edges == {"dataset:work.a": "Sheet1", "dataset:work.b": "Sheet2"}


def test_unresolved_datafile_macro_variable_still_creates_externalfile_node():
    """Ticket 01's own requirement: an unbound macro variable in DATAFILE=
    still mints an ExternalFile node keyed on the raw text, plus a WARNING
    finding with UNRESOLVED_MACRO_VARIABLE status."""
    blocks, ctx, events = build(
        'proc import datafile="&missing.ae.xlsx" out=work.ae_raw dbms=xlsx replace;\n'
        "run;\n"
    )
    rules_proc_import.apply(blocks[0], ctx, events)

    external_file = next(n for n in ctx.nodes if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:&missing.ae.xlsx"

    unresolved_findings = [
        f for f in ctx.findings if f["status"] == "UNRESOLVED_MACRO_VARIABLE"
    ]
    assert len(unresolved_findings) == 1
    assert unresolved_findings[0]["affected_nodes"] == [external_file["id"]]

    edge = next(e for e in ctx.edges if e["type"] == "reads_external_file")
    assert edge["from"] == external_file["id"]


def test_unresolved_out_macro_variable_creates_unknown_dataset():
    """Same UnknownDataset/finding path every other dataset-name token uses,
    applied to PROC IMPORT's own OUT=."""
    blocks, ctx, events = build(
        'proc import datafile="/data/raw/ae.xlsx" out=work.&missing. dbms=xlsx replace;\n'
        "run;\n"
    )
    rules_proc_import.apply(blocks[0], ctx, events)

    unknown = [n for n in ctx.nodes if n["type"] == "UnknownDataset"]
    assert len(unknown) == 1
    edge = next(e for e in ctx.edges if e["type"] == "reads_external_file")
    assert edge["to"] == unknown[0]["id"]


def test_no_datafile_or_out_produces_no_externalfile_evidence():
    blocks, ctx, events = build("proc import dbms=xlsx replace;\nrun;\n")
    rules_proc_import.apply(blocks[0], ctx, events)

    assert not any(n["type"] == "ExternalFile" for n in ctx.nodes)
    assert not any(e["type"] == "reads_external_file" for e in ctx.edges)


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("proc import rules: all checks passed")


if __name__ == "__main__":
    demo()
