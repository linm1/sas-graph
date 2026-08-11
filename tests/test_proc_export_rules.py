"""PROC EXPORT rules (external-file-sources ticket 04)."""

import conftest  # noqa: F401

from sas_graph import rules_proc_export
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


def test_export_creates_externalfile_node_and_writes_external_file_edge():
    """PROC EXPORT accepts DATA=/OUTFILE= in either option order."""
    blocks, ctx, events = build(
        'proc export outfile="/data/exports/ae.xlsx" dbms=xlsx replace data=work.ae;\n'
        "run;\n"
    )
    rules_proc_export.apply(blocks[0], ctx, events)

    external_file = next(n for n in ctx.nodes if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:/data/exports/ae.xlsx"
    assert external_file["source"] is None

    edge = next(e for e in ctx.edges if e["type"] == "writes_external_file")
    assert (edge["from"], edge["to"], edge["dbms"]) == (
        "dataset:work.ae", external_file["id"], "xlsx",
    )


def test_data_literal_inside_outfile_is_not_mistaken_for_data_option():
    blocks, ctx, events = build(
        'proc export outfile="/data/data=not_a_dataset.xlsx" data=work.ae dbms=xlsx;\n'
        "run;\n"
    )
    rules_proc_export.apply(blocks[0], ctx, events)

    edge = next(e for e in ctx.edges if e["type"] == "writes_external_file")
    assert edge["from"] == "dataset:work.ae"
    assert "dataset:not_a_dataset.xlsx" not in {n["id"] for n in ctx.nodes}


def test_data_options_in_parens_do_not_leak_into_dataset_id():
    blocks, ctx, events = build(
        'proc export data=work.ae(keep=USUBJID) outfile="/data/ae.csv" dbms=csv;\n'
        "run;\n"
    )
    rules_proc_export.apply(blocks[0], ctx, events)

    edge = next(e for e in ctx.edges if e["type"] == "writes_external_file")
    assert edge["from"] == "dataset:work.ae"


def test_unresolved_outfile_macro_variable_still_creates_externalfile_node():
    blocks, ctx, events = build(
        'proc export data=work.ae outfile="&missing.ae.xlsx" dbms=xlsx;\n'
        "run;\n"
    )
    rules_proc_export.apply(blocks[0], ctx, events)

    external_file = next(n for n in ctx.nodes if n["type"] == "ExternalFile")
    assert external_file["id"] == "externalfile:&missing.ae.xlsx"
    finding = next(f for f in ctx.findings if f["status"] == "UNRESOLVED_MACRO_VARIABLE")
    assert finding["affected_nodes"] == [external_file["id"]]
    edge = next(e for e in ctx.edges if e["type"] == "writes_external_file")
    assert edge["to"] == external_file["id"]


def test_unresolved_data_macro_variable_creates_unknown_dataset():
    blocks, ctx, events = build(
        'proc export data=work.&missing. outfile="/data/ae.xlsx" dbms=xlsx;\n'
        "run;\n"
    )
    rules_proc_export.apply(blocks[0], ctx, events)

    unknown = next(n for n in ctx.nodes if n["type"] == "UnknownDataset")
    edge = next(e for e in ctx.edges if e["type"] == "writes_external_file")
    assert edge["from"] == unknown["id"]


def test_no_data_or_outfile_produces_no_externalfile_evidence():
    blocks, ctx, events = build("proc export dbms=xlsx replace;\nrun;\n")
    rules_proc_export.apply(blocks[0], ctx, events)

    assert not any(n["type"] == "ExternalFile" for n in ctx.nodes)
    assert not any(e["type"] == "writes_external_file" for e in ctx.edges)


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("proc export rules: all checks passed")


if __name__ == "__main__":
    demo()
