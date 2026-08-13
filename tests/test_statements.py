"""Comment separation and statement splitting (dev plan sections 7, 11.6, 21 Phase 2).

Phase 2 owns one job: turn SAS text into an ordered statement stream that carries
its own source location. Every test here is either a splitting rule or a
traceability rule, because those are the two things Phases 3-4 cannot rebuild if
this layer loses them.

Plain asserts and a `demo()` entry point, matching the config and renderer tests.
"""

from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.statements import split_statements

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "basic_adae" / "adae.sas"


def texts(result):
    return [s.text for s in result.statements]


def only(result):
    assert len(result.statements) == 1, texts(result)
    return result.statements[0]


def fixture():
    return split_statements(FIXTURE.read_text(encoding="utf-8"), FIXTURE.name)


# --- statement splitting ---------------------------------------------------


def test_splits_on_semicolons():
    result = split_statements("data work.a;\n  set sdtm.ae;\nrun;\n", "t.sas")

    assert texts(result) == ["data work.a;", "set sdtm.ae;", "run;"]


def test_statement_order_starts_at_one_and_increments():
    result = split_statements("data work.a;\n  set sdtm.ae;\nrun;\n", "t.sas")

    assert [s.statement_order for s in result.statements] == [1, 2, 3]


def test_trailing_text_without_a_semicolon_is_still_a_statement():
    """A file that ends mid-statement must not silently drop it (section 26)."""
    result = split_statements("data work.a;\n  set sdtm.ae\n", "t.sas")

    assert texts(result) == ["data work.a;", "set sdtm.ae"]
    assert result.statements[-1].terminated is False


def test_whitespace_only_tail_is_not_a_statement():
    result = split_statements("run;\n\n   \n", "t.sas")

    assert texts(result) == ["run;"]


def test_blank_input_yields_no_statements():
    result = split_statements("", "t.sas")

    assert result.statements == []
    assert result.comments == []


# --- quoting ---------------------------------------------------------------


def test_semicolon_inside_double_quotes_does_not_split():
    result = split_statements('where x = "a;b";\n', "t.sas")

    assert texts(result) == ['where x = "a;b";']


def test_semicolon_inside_single_quotes_does_not_split():
    result = split_statements("where x = 'a;b';\n", "t.sas")

    assert texts(result) == ["where x = 'a;b';"]


def test_doubled_quote_is_an_escape_not_a_close():
    """`"it""s;"` is one string containing a semicolon, not two strings."""
    result = split_statements('put "it""s; fine";\n', "t.sas")

    assert texts(result) == ['put "it""s; fine";']


def test_the_other_quote_style_inside_a_string_is_literal():
    result = split_statements("""put "don't stop";\n""", "t.sas")

    assert texts(result) == ["""put "don't stop";"""]


def test_comment_opener_inside_a_string_is_literal():
    result = split_statements('put "/* not a comment */";\n', "t.sas")

    assert texts(result) == ['put "/* not a comment */";']
    assert result.comments == []


def test_unterminated_string_is_reported_not_swallowed():
    result = split_statements('put "oops;\nrun;\n', "t.sas")

    assert any(f["type"] == "unterminated_string" for f in result.findings)


# --- block comments --------------------------------------------------------


def test_block_comment_is_removed_from_active_code():
    result = split_statements("/* note */\ndata work.a;\n", "t.sas")

    assert texts(result) == ["data work.a;"]


def test_block_comment_is_captured_as_inactive_evidence():
    """Section 11.6: commented code is evidence, never an active node."""
    result = split_statements("/* set sdtm.suppae; */\nrun;\n", "t.sas")

    assert len(result.comments) == 1
    assert "sdtm.suppae" in result.comments[0].text
    assert result.comments[0].kind == "block"


def test_block_comments_do_not_nest():
    """SAS closes at the first `*/`; a depth counter would swallow the tail."""
    result = split_statements("/* a /* b */ data work.a;\n", "t.sas")

    assert texts(result) == ["data work.a;"]


def test_semicolon_inside_a_block_comment_does_not_split():
    """`text` is normalised for rule matching, so the cut leaves no gap.

    The comment survives verbatim in `original_text` and in `result.comments`;
    leaving a double space in `text` would only be a trap for the Phase 4 rules
    that pattern-match against it.
    """
    result = split_statements("data /* ; ; ; */ work.a;\n", "t.sas")

    assert texts(result) == ["data work.a;"]
    assert only(result).original_text == "data /* ; ; ; */ work.a;"


def test_unterminated_block_comment_is_reported():
    result = split_statements("/* forever\ndata work.a;\n", "t.sas")

    assert any(f["type"] == "unterminated_comment" for f in result.findings)
    assert texts(result) == []


def test_a_broken_delimiter_does_not_fail_the_whole_run():
    """Section 5.2 vs 5.3: statements parsed before the damage are still true.

    `config.py` makes any BLOCKED finding FAILED, and 5.3 reserves FAILED for a
    parser that cannot identify statements at all. An unbalanced delimiter after
    a clean prefix does not meet that bar, so this must not emit BLOCKED.
    """
    result = split_statements("data work.a;\nrun;\n/* never closed\n", "t.sas")

    assert texts(result) == ["data work.a;", "run;"]
    assert result.findings
    assert not any(f["status"] == "BLOCKED" for f in result.findings)


def test_broken_delimiter_findings_render_through_the_findings_renderer():
    """Whatever status is used must be one the section-18 renderer knows."""
    from sas_graph.renderer_findings import render

    result = split_statements('put "oops;\n', "t.sas")
    out = render({"run_status": "PARTIAL", "nodes": [], "findings": result.findings})

    assert "UNRECOGNISED STATUS" not in out


# --- star comments ---------------------------------------------------------


def test_star_comment_at_statement_start_is_a_comment():
    result = split_statements("* a note;\nrun;\n", "t.sas")

    assert texts(result) == ["run;"]
    assert result.comments[0].kind == "star"


def test_star_after_a_terminated_statement_is_a_comment():
    result = split_statements("run;\n* a note;\ndata work.a;\n", "t.sas")

    assert texts(result) == ["run;", "data work.a;"]
    assert len(result.comments) == 1


def test_star_mid_statement_is_multiplication_not_a_comment():
    """`x = a * b;` must survive: `*` is only a comment at statement start."""
    result = split_statements("x = a * b;\n", "t.sas")

    assert texts(result) == ["x = a * b;"]
    assert result.comments == []


def test_macro_star_comment_is_a_comment():
    result = split_statements("%* macro note;\nrun;\n", "t.sas")

    assert texts(result) == ["run;"]
    assert result.comments[0].kind == "macro_star"


# --- source traceability (section 7) ---------------------------------------


def test_statements_carry_the_file_name():
    result = split_statements("run;\n", "adae.sas")

    assert only(result).file == "adae.sas"


def test_line_numbers_survive_comment_removal():
    """The whole point of marking spans instead of deleting text.

    A stripped-then-split scanner reports line 1 here and every downstream
    finding points at the wrong source line.
    """
    text = "/* one\n   two\n   three */\ndata work.a;\n"
    result = split_statements(text, "t.sas")

    assert only(result).line_start == 4


def test_multi_line_statement_spans_its_real_line_range():
    text = "data work.a;\n  set\n    sdtm.ae\n    adam.adsl;\n"
    result = split_statements(text, "t.sas")

    second = result.statements[1]
    assert (second.line_start, second.line_end) == (2, 4)


def test_original_text_keeps_the_statement_indentation():
    """Section 7 stores `original_text`; stripping the indent loses audit value."""
    text = "data work.a;\n  set sdtm.ae;\nrun;\n"
    result = split_statements(text, "t.sas")

    assert result.statements[0].original_text == "data work.a;"
    assert result.statements[1].original_text == "  set sdtm.ae;"


def test_text_is_normalised_but_original_text_is_not():
    result = split_statements("data\n   work.a;\n", "t.sas")

    statement = only(result)
    assert statement.text == "data work.a;"
    assert statement.original_text == "data\n   work.a;"


def test_comments_carry_their_line_range():
    result = split_statements("run;\n/* a\n   b */\n", "t.sas")

    comment = result.comments[0]
    assert (comment.line_start, comment.line_end) == (2, 3)


def test_comment_records_the_statement_order_it_followed():
    """Section 11.6 evidence is only readable if it can be placed in the flow."""
    result = split_statements("data work.a;\nrun;\n/* after the run */\n", "t.sas")

    assert result.comments[0].after_statement_order == 2


def test_crlf_line_endings_do_not_shift_line_numbers():
    result = split_statements("data work.a;\r\nrun;\r\n", "t.sas")

    assert [s.line_start for s in result.statements] == [1, 2]


def test_statement_source_matches_the_section_7_shape():
    result = split_statements("data work.a;\n", "adae.sas")

    source = only(result).as_source("data_step_start")
    assert set(source) == {
        "file",
        "line_start",
        "line_end",
        "statement_order",
        "original_text",
        "rule",
    }
    assert source["rule"] == "data_step_start"


# --- bare macro call ends a statement without a `;` ------------------------


def test_bare_macro_call_followed_by_another_call_splits_without_semicolons():
    """The real qc_adae setup.sas pattern: no `;` between sibling calls."""
    result = split_statements(
        "%os_fvars(mvar=_trim, projpath=a:b:trim)\n"
        "%os_fvars(mvar=_xpt, projpath=a:b:xpt)\n",
        "t.sas",
    )

    assert texts(result) == [
        "%os_fvars(mvar=_trim, projpath=a:b:trim)",
        "%os_fvars(mvar=_xpt, projpath=a:b:xpt)",
    ]
    assert all(s.terminated for s in result.statements)


def test_bare_macro_call_immediately_followed_by_end_splits_cleanly():
    """The exact merge that corrupted %end; depth-counting before this fix."""
    result = split_statements(
        "%if x = y %then %do;\n"
        "%os_fvars(mvar=_trim, projpath=a:b:trim)\n"
        "%os_fvars(mvar=_xpt, projpath=a:b:xpt)\n"
        "%end;\n",
        "t.sas",
    )

    assert texts(result) == [
        "%if x = y %then %do;",
        "%os_fvars(mvar=_trim, projpath=a:b:trim)",
        "%os_fvars(mvar=_xpt, projpath=a:b:xpt)",
        "%end;",
    ]


def test_bare_macro_call_with_trailing_semicolon_is_unaffected():
    """A semicolon-terminated call falls through to normal `;`-based emission
    unchanged -- the new close-then-peek check only fires when NOT followed
    by `;`, so `.text` keeps its own trailing `;` exactly like any other
    statement (see test_splits_on_semicolons)."""
    result = split_statements("%os_fvars(mvar=_x, projpath=a:b);\n", "t.sas")

    assert texts(result) == ["%os_fvars(mvar=_x, projpath=a:b);"]
    assert only(result).terminated is True


def test_macro_call_embedded_in_a_larger_statement_keeps_accumulating():
    """`%sysfunc(...)` used inside a `%let` must not be split at its own close."""
    result = split_statements("%let x = %sysfunc(compress(a)) plus more;\n", "t.sas")

    assert texts(result) == ["%let x = %sysfunc(compress(a)) plus more;"]


def test_bare_macro_call_with_quoted_paren_in_args_is_not_desynced():
    """A literal `)` inside a quoted arg must not close the call early."""
    result = split_statements(
        '%os_fvars(mvar=_x, projpath="a)b")\n%os_fvars(mvar=_y, projpath=c)\n',
        "t.sas",
    )

    assert texts(result) == [
        '%os_fvars(mvar=_x, projpath="a)b")',
        "%os_fvars(mvar=_y, projpath=c)",
    ]


def test_macro_definition_header_with_parameter_list_is_unaffected():
    """`%macro name(params);` must never be mistaken for a bare call on `%macro`."""
    result = split_statements("%macro setup(a, b);\n%mend setup;\n", "t.sas")

    assert texts(result) == ["%macro setup(a, b);", "%mend setup;"]


def test_bare_macro_call_at_end_of_file_with_no_trailing_semicolon():
    result = split_statements("%os_fvars(mvar=_x, projpath=a:b)", "t.sas")

    assert texts(result) == ["%os_fvars(mvar=_x, projpath=a:b)"]
    assert only(result).terminated is True


# --- the committed fixture -------------------------------------------------


def test_fixture_active_statements_exclude_the_commented_block():
    assert not any("suppae" in s.text for s in fixture().statements)


def test_fixture_commented_block_is_kept_as_evidence():
    assert any("sdtm.suppae" in c.text for c in fixture().comments)


def test_fixture_macro_calls_survive_as_statements():
    result = fixture()

    assert any(s.text.startswith("%gm_derive(") for s in result.statements)
    assert any("&unknown_out." in s.text for s in result.statements)


def test_fixture_statement_order_is_gapless():
    orders = [s.statement_order for s in fixture().statements]

    assert orders == list(range(1, len(orders) + 1))


def test_fixture_is_clean():
    assert fixture().findings == []


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("statement splitter: all checks passed")


if __name__ == "__main__":
    demo()
