"""Block-grouping layer (dev plan sections 12-14, 21 Phase 4).

The fixture in dev plan section 22 shows why this exists: `step:001`'s
`original_text` spans `data work.adae_pre; set sdtm.ae adam.adsl; run;` --
three statements, one node. No rule module can fire against a lone
`Statement`; grouping has to happen first.
"""

import conftest  # noqa: F401

from sas_graph.blocks import group_blocks
from sas_graph.statements import split_statements


def split(text, file_name="t.sas"):
    return split_statements(text, file_name).statements


def test_data_step_is_one_block_spanning_its_run():
    statements = split("data work.a;\n  set sdtm.ae;\nrun;\n")
    blocks, unattached = group_blocks(statements)

    assert unattached == []
    assert len(blocks) == 1
    block = blocks[0]
    assert block.kind == "DATA"
    assert block.proc_name is None
    assert [s.text for s in block.statements] == [
        "data work.a;",
        "set sdtm.ae;",
        "run;",
    ]
    assert block.terminated is True
    assert block.line_start == 1
    assert block.line_end == 3
    assert block.statement_order == 1


def test_proc_sort_block_includes_its_by_statement():
    statements = split(
        "proc sort data=work.a out=work.a_srt;\n  by usubjid aeseq;\nrun;\n"
    )
    blocks, _ = group_blocks(statements)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.kind == "PROC"
    assert block.proc_name == "sort"
    assert [s.text for s in block.statements] == [
        "proc sort data=work.a out=work.a_srt;",
        "by usubjid aeseq;",
        "run;",
    ]


def test_proc_sql_block_closes_on_quit():
    statements = split(
        "proc sql;\n  create table work.a as select * from sdtm.ae;\nquit;\n"
    )
    blocks, _ = group_blocks(statements)

    assert len(blocks) == 1
    assert blocks[0].proc_name == "sql"
    assert blocks[0].terminated is True


def test_two_consecutive_blocks_do_not_merge():
    statements = split(
        "data work.a;\n  set sdtm.ae;\nrun;\n"
        "proc sort data=work.a out=work.a_srt;\n  by usubjid;\nrun;\n"
    )
    blocks, _ = group_blocks(statements)

    assert len(blocks) == 2
    assert blocks[0].kind == "DATA"
    assert blocks[1].kind == "PROC"
    assert blocks[1].statement_order == 4


def test_missing_quit_closes_at_next_opener_not_fatal():
    """Section 14.6: no quit; the block still closes, unterminated."""
    statements = split(
        "proc sql;\n  create table work.a as select * from sdtm.ae;\n"
        "data work.b;\n  set work.a;\nrun;\n"
    )
    blocks, _ = group_blocks(statements)

    assert len(blocks) == 2
    sql_block, data_block = blocks
    assert sql_block.proc_name == "sql"
    assert sql_block.terminated is False
    assert [s.text for s in sql_block.statements] == [
        "proc sql;",
        "create table work.a as select * from sdtm.ae;",
    ]
    assert data_block.kind == "DATA"
    assert data_block.terminated is True


def test_missing_terminator_at_end_of_file_closes_unterminated():
    statements = split("proc sql;\n  create table work.a as select * from sdtm.ae;\n")
    blocks, _ = group_blocks(statements)

    assert len(blocks) == 1
    assert blocks[0].terminated is False


def test_let_and_macro_call_are_unattached_not_swallowed_into_a_block():
    statements = split(
        "%let domain = ae;\n"
        "data work.a;\n  set sdtm.ae;\nrun;\n"
        "%gm_derive(inds=work.a, outds=adam.adae);\n"
    )
    blocks, unattached = group_blocks(statements)

    assert len(blocks) == 1
    assert [s.text for s in unattached] == [
        "%let domain = ae;",
        "%gm_derive(inds=work.a, outds=adam.adae);",
    ]


def test_libname_between_blocks_is_unattached():
    statements = split(
        'libname sdtm "/study/demo/sdtm";\n'
        "data work.a;\n  set sdtm.ae;\nrun;\n"
    )
    blocks, unattached = group_blocks(statements)

    assert len(blocks) == 1
    assert [s.text for s in unattached] == ['libname sdtm "/study/demo/sdtm";']


def test_block_as_source_spans_all_member_statements():
    statements = split("data work.a;\n  set sdtm.ae;\nrun;\n")
    blocks, _ = group_blocks(statements)

    source = blocks[0].as_source("data_step_set")
    assert source["line_start"] == 1
    assert source["line_end"] == 3
    assert source["statement_order"] == 1
    assert source["original_text"] == "data work.a;\n  set sdtm.ae;\nrun;"
    assert source["rule"] == "data_step_set"


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("blocks: all checks passed")


if __name__ == "__main__":
    demo()
