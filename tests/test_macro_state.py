"""Macro variable state and `%include` expansion (dev plan sections 11.2, 11.3,
11.7, 12.8, 14.7, 21 Phase 3).

Two invariants this phase must not break, carried from the Phase 3 handoff:

- **No final global macro table.** Section 11.3, verbatim: resolution is
  per-statement against state as of that `statement_order`. `resolve_text`
  below always takes a `statement_order` argument; there is no `.value`
  shortcut that hands out "the" current binding.
- **Statement order is the spine.** Splicing an `%include` renumbers
  `statement_order` for every statement, and `Comment.after_statement_order`
  and each finding's `source["statement_order"]` must be renumbered with it --
  otherwise a spliced statement's evidence points at the wrong place.

Two design choices settled before writing this file (handoff + advisor):

- `%include` is expanded *before* macro resolution runs (dev plan section 9,
  steps 5-6 precede step 7; step 12 precedes 13). An include path that itself
  needs macro resolution (`%include "&root./x.sas";`) is therefore not
  supported in v0: it is reported and skipped, not guessed.
  ponytail: interleaved expand/resolve would handle it; add if a real config
  needs a macro-variable path in `%include`.
- `%let` right-hand sides resolve against state *strictly before* their own
  statement_order (`<`, not `<=`), so `%let a = &a.;` cannot resolve against
  itself and a use site in the same call always sees the fully-resolved value.
"""

from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.macro_state import (
    expand_includes,
    resolve_text,
    walk_let_statements,
    walk_runtime,
)
from sas_graph.statements import split_statements

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def split(text, file_name="t.sas"):
    return split_statements(text, file_name)


# --- %let resolution, per statement_order -----------------------------------


def test_let_value_resolves_after_its_own_statement():
    result = split("%let domain = ae;\n%put &domain.;\n")
    events = walk_let_statements(result.statements)

    assert resolve_text("&domain.", statement_order=2, events=events) == "ae"


def test_let_value_is_not_visible_before_its_statement_order():
    """No final global table: state as of order 1 must not see order 2's %let."""
    result = split("%put &domain.;\n%let domain = ae;\n")
    events = walk_let_statements(result.statements)

    _, unresolved = resolve_text(
        "&domain.", statement_order=1, events=events, return_unresolved=True
    )
    assert unresolved == ["domain"]


def test_same_macro_variable_reassigned_resolves_per_call_site():
    """The handoff's worked example: two calls, two different resolved values."""
    result = split(
        "%let domain = ae;\n"
        "%gm_derive(inds=sdtm.&domain.);\n"
        "%let domain = cm;\n"
        "%gm_derive(inds=sdtm.&domain.);\n"
    )
    events = walk_let_statements(result.statements)

    first_call, second_call = result.statements[1], result.statements[3]
    assert resolve_text(first_call.text, first_call.statement_order, events) == (
        "%gm_derive(inds=sdtm.ae);"
    )
    assert resolve_text(second_call.text, second_call.statement_order, events) == (
        "%gm_derive(inds=sdtm.cm);"
    )


def test_macro_variable_lookup_is_case_insensitive_and_later_assignment_wins():
    result = split("%let GLOB = sdtm.ae;\n%let glob = sdtm.lb;\n%put &gLoB.;\n")
    events = walk_let_statements(result.statements)

    assert [event.name for event in events] == ["GLOB", "glob"]
    assert resolve_text("&gLoB.", 3, events) == "sdtm.lb"


def test_let_rhs_resolves_against_state_strictly_before_its_own_order():
    """`%let a = &a.;` must not resolve against itself (advisor point 3)."""
    result = split("%let a = one;\n%let a = &a.two;\n")
    events = walk_let_statements(result.statements)

    assert resolve_text("&a.", statement_order=3, events=events) == "onetwo"


def test_dot_terminator_is_consumed_not_kept():
    """Section 15.6's own example: `work.&domain._pre` -> `work.ae_pre`."""
    result = split("%let domain = ae;\n%put work.&domain._pre;\n")
    events = walk_let_statements(result.statements)

    assert resolve_text("work.&domain._pre", 2, events) == "work.ae_pre"


def test_double_dot_collapses_to_one_literal_dot():
    """`&lib..&ds` -> one literal dot, not two."""
    result = split("%let lib = sdtm;\n%let ds = ae;\n%put &lib..&ds;\n")
    events = walk_let_statements(result.statements)

    assert resolve_text("&lib..&ds", 3, events) == "sdtm.ae"


def test_unresolved_macro_variable_is_reported_not_guessed():
    result = split("%put &unknown_out.;\n")
    events = walk_let_statements(result.statements)

    _, unresolved = resolve_text("&unknown_out.", 1, events, return_unresolved=True)
    assert unresolved == ["unknown_out"]


def test_non_simple_reference_is_left_untouched_not_guessed():
    """`&&x` is not "simple" per section 3.2 -- one guard, not a general
    evaluator. It is neither substituted nor reported as a clean unresolved
    `x` reference; see `test_bound_double_ampersand_reference_is_not_substituted`
    for why it must not be silently treated as one."""
    result = split("%put &&x;\n")
    events = walk_let_statements(result.statements)

    resolved, unresolved = resolve_text("&&x", 1, events, return_unresolved=True)
    assert resolved == "&&x"
    assert unresolved == []


# --- %PUT is ignored (section 11.7) -----------------------------------------


def test_put_does_not_create_an_unresolved_variable_finding():
    result = split("%put &undefined.;\n")

    findings = walk_let_statements(result.statements, findings_only=True)
    assert findings == []


def test_put_statement_produces_no_let_binding():
    result = split("%put x = &something.;\n")
    events = walk_let_statements(result.statements)

    assert events == []


# --- CALL SYMPUTX / PROC SQL INTO: are evidence only (12.8, 14.7) -----------


def test_call_symputx_does_not_create_a_let_binding():
    result = split("data _null_;\n  call symputx('domain', 'ae');\nrun;\n")
    events = walk_let_statements(result.statements)

    assert events == []


def test_call_symputx_is_flagged_not_usable_for_static_resolution():
    result = split("data _null_;\n  call symputx('domain', 'ae');\nrun;\n")
    findings = walk_let_statements(result.statements, findings_only=True)

    assert any(
        f["type"] == "runtime_macro_variable_creation"
        and "symputx" in f["message"].lower()
        for f in findings
    )
    assert all(f["status"] != "BLOCKED" for f in findings)


def test_proc_sql_into_does_not_create_a_let_binding():
    result = split("proc sql;\n  select domain into :domain from work.a;\nquit;\n")
    events = walk_let_statements(result.statements)

    assert events == []


def test_proc_sql_into_is_flagged_not_usable_for_static_resolution():
    result = split("proc sql;\n  select domain into :domain from work.a;\nquit;\n")
    findings = walk_let_statements(result.statements, findings_only=True)

    assert any(
        f["type"] == "runtime_macro_variable_creation"
        and "into" in f["message"].lower()
        for f in findings
    )


def test_a_prior_let_still_resolves_after_a_runtime_creation_statement():
    """Section 12.8: runtime creation does not invalidate an existing %let."""
    result = split(
        "%let domain = ae;\n"
        "data _null_;\n  call symputx('other', 'x');\nrun;\n"
        "%put &domain.;\n"
    )
    events = walk_let_statements(result.statements)
    last = result.statements[-1]

    assert resolve_text("&domain.", last.statement_order, events) == "ae"


# --- %include expansion (section 11.2) --------------------------------------


def test_include_is_spliced_in_place():
    included = FIXTURES / "includes" / "child.sas"
    text = f'data work.a;\n%include "{included}";\nrun;\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    assert [s.text for s in expanded.statements] == [
        "data work.a;",
        "set sdtm.child;",
        "run;",
    ]


def test_spliced_statements_keep_their_own_file_and_are_renumbered_gaplessly():
    included = FIXTURES / "includes" / "child.sas"
    text = f'data work.a;\n%include "{included}";\nrun;\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    files = [s.file for s in expanded.statements]
    assert files == ["parent.sas", "child.sas", "parent.sas"]

    orders = [s.statement_order for s in expanded.statements]
    assert orders == [1, 2, 3]


def test_spliced_comment_after_statement_order_is_renumbered_with_its_statement():
    included = FIXTURES / "includes" / "child_with_comment.sas"
    text = f'data work.a;\n%include "{included}";\nrun;\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    # child_with_comment.sas: one statement, then a trailing comment.
    assert len(expanded.comments) == 1
    assert expanded.comments[0].after_statement_order == 2


def test_spliced_child_comment_between_two_statements_keeps_its_position():
    """A comment mid-child must land between the same two statements after
    splicing, not get pushed to the end (the bug: appending statements then
    comments wholesale instead of interleaving by position)."""
    included = FIXTURES / "includes" / "child_with_mid_comment.sas"
    text = f'%include "{included}";\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    assert [s.text for s in expanded.statements] == [
        "set sdtm.a;",
        "set sdtm.b;",
    ]
    assert len(expanded.comments) == 1
    assert expanded.comments[0].after_statement_order == 1


def test_comment_trailing_the_include_line_itself_survives_splicing():
    included = FIXTURES / "includes" / "child.sas"
    text = f'%include "{included}";\n/* why we include this */\nrun;\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    assert len(expanded.comments) == 1
    assert "why we include this" in expanded.comments[0].text
    assert expanded.comments[0].after_statement_order == 1


def test_include_outside_allowed_roots_is_rejected_not_read():
    outside = FIXTURES / "includes" / "child.sas"
    text = f'data work.a;\n%include "{outside}";\nrun;\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "unrelated"]
    )

    # The %include statement itself is left in place, unexpanded.
    assert [s.text for s in expanded.statements] == [
        "data work.a;",
        f'%include "{outside}";',
        "run;",
    ]
    assert any(f["type"] == "include_outside_allowed_roots" for f in expanded.findings)
    assert all(f["status"] != "BLOCKED" for f in expanded.findings)


def test_include_of_prohibited_artifact_is_rejected():
    text = 'data work.a;\n%include "/study/adae/prod/x.sas7bdat";\nrun;\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=["/study/adae"]
    )

    assert any(f["type"] == "include_prohibited_artifact" for f in expanded.findings)


def test_missing_include_file_is_reported_not_raised():
    missing = FIXTURES / "includes" / "does_not_exist.sas"
    text = f'data work.a;\n%include "{missing}";\nrun;\n'
    result = split(text, "parent.sas")

    source_paths = {}
    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"],
        source_paths=source_paths,
    )

    assert any(f["type"] == "include_file_missing" for f in expanded.findings)
    assert all(f["status"] != "BLOCKED" for f in expanded.findings)
    assert missing.resolve() not in source_paths


def test_unreadable_include_is_reported_not_raised():
    """A path that exists but is a directory must not crash the whole run."""
    directory = FIXTURES / "includes"
    text = f'data work.a;\n%include "{directory}";\nrun;\n'
    result = split(text, "parent.sas")

    source_paths = {}
    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"],
        source_paths=source_paths,
    )

    assert any(f["type"] == "include_file_unreadable" for f in expanded.findings)
    assert all(f["status"] != "BLOCKED" for f in expanded.findings)
    assert directory.resolve() not in source_paths


def test_self_including_file_does_not_hang():
    """Section 11.2 expands includes inside included files; guard the cycle."""
    looping = FIXTURES / "includes" / "self_loop.sas"
    text = looping.read_text(encoding="utf-8")
    result = split(text, "self_loop.sas")

    expanded = expand_includes(
        result.statements,
        result.comments,
        allowed_roots=[FIXTURES / "includes"],
        base_dir=FIXTURES / "includes",
    )

    assert any(f["type"] == "include_cycle_detected" for f in expanded.findings)


def test_nested_include_expands_recursively():
    outer = FIXTURES / "includes" / "nests_another.sas"
    text = outer.read_text(encoding="utf-8")
    result = split(text, "nests_another.sas")

    expanded = expand_includes(
        result.statements,
        result.comments,
        allowed_roots=[FIXTURES / "includes"],
        base_dir=FIXTURES / "includes",
    )

    assert [s.text for s in expanded.statements] == ["set sdtm.child;"]


def test_include_findings_render_through_the_findings_renderer():
    from sas_graph.renderer_findings import render

    text = f'%include "{FIXTURES / "includes" / "does_not_exist.sas"}";\n'
    result = split(text, "parent.sas")
    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    out = render(
        {"run_status": "PARTIAL", "nodes": [], "findings": expanded.findings}
    )
    assert "UNRECOGNISED STATUS" not in out


def test_child_files_own_parse_error_findings_are_not_dropped():
    """A malformed included file (unterminated comment) must still surface its
    own scanner finding through the parent's expand_includes call, not vanish
    just because it was reached via %include rather than parsed directly."""
    included = FIXTURES / "includes" / "child_with_parse_error.sas"
    text = f'%include "{included}";\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    assert any(f["type"] == "unterminated_comment" for f in expanded.findings)
    assert all(f["status"] != "BLOCKED" for f in expanded.findings)


def test_child_parse_error_statement_order_is_final_not_local_when_parent_has_a_prefix():
    """Regression: an earlier revision keyed the deferred-rename lookup by the
    child's own local statement number, which the scanner's forward-pointer
    finding (statement_order = len(statements) + 1, a slot that is never
    filled) can never match -- so it silently kept the pre-splice number.
    Two statements ahead of the %include exposes the bug: the stale local
    number (2) would coincidentally look plausible only when the parent
    prefix also happened to be 1 statement long, which every other fixture
    in this file uses."""
    included = FIXTURES / "includes" / "child_with_parse_error.sas"
    text = f'set sdtm.prefix1;\nset sdtm.prefix2;\n%include "{included}";\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    finding = next(f for f in expanded.findings if f["type"] == "unterminated_comment")
    # prefix1 (1), prefix2 (2), child's own `set sdtm.a;` (3) -- the broken
    # comment shares that same count: nothing after it was ever emitted, so
    # its final position is 3, not the child's stale local number (2).
    assert finding["source"]["statement_order"] == 3
    assert finding["source"]["file"] == "child_with_parse_error.sas"


def test_two_sibling_includes_with_parse_errors_do_not_collide_on_id():
    """Regression: each child's own `_Scanner` numbers its findings from 1
    locally, and the pass-through path did not rebuild `id`, so two
    independently-broken included files produced the identical finding id."""
    a = FIXTURES / "includes" / "sibling_a_broken.sas"
    b = FIXTURES / "includes" / "sibling_b_broken.sas"
    text = f'%include "{a}";\n%include "{b}";\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    broken = [f for f in expanded.findings if f["type"] == "unterminated_comment"]
    assert len(broken) == 2
    assert len({f["id"] for f in broken}) == 2


def test_sibling_includes_that_each_break_before_any_statement_do_not_collide_on_id():
    """Regression: when a %include's own file breaks before producing even one
    statement (its very first token is the unterminated comment), `emitted`
    at that point is identical for every sibling that does the same -- 0 with
    no parent prefix. Position alone can't disambiguate the ids; a per-finding
    counter must."""
    a = FIXTURES / "includes" / "broken_immediately_a.sas"
    b = FIXTURES / "includes" / "broken_immediately_b.sas"
    text = f'%include "{a}";\n%include "{b}";\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    broken = [f for f in expanded.findings if f["type"] == "unterminated_comment"]
    assert len(broken) == 2
    assert broken[0]["source"]["statement_order"] == broken[1]["source"]["statement_order"]
    assert len({f["id"] for f in broken}) == 2


def test_include_finding_statement_order_reflects_final_spliced_position():
    """The core renumbering invariant: a finding attached to a statement that
    comes after a *successfully* spliced include must report that statement's
    position in the final output, not its pre-splice local number."""
    outer = FIXTURES / "includes" / "nests_missing.sas"
    text = outer.read_text(encoding="utf-8")
    result = split(text, "nests_missing.sas")

    expanded = expand_includes(
        result.statements,
        result.comments,
        allowed_roots=[FIXTURES / "includes"],
        base_dir=FIXTURES / "includes",
    )

    missing_finding = next(
        f for f in expanded.findings if f["type"] == "include_file_missing"
    )
    # nests_missing.sas: `set sdtm.real;` (1), then a %include to a valid but
    # multi-statement child before the missing one -- if the missing include's
    # source were built from its pre-splice local order (2, its position in
    # this file) rather than the final spliced position, this would be wrong.
    assert missing_finding["source"]["statement_order"] == 3


def test_include_with_trailing_options_is_still_expanded():
    """`%include "path" / SOURCE2;` -- the trailing option must not make this
    silently fall through as an ordinary, unrecognised statement."""
    included = FIXTURES / "includes" / "child.sas"
    text = f'%include "{included}" / SOURCE2;\n'
    result = split(text, "parent.sas")

    expanded = expand_includes(
        result.statements, result.comments, allowed_roots=[FIXTURES / "includes"]
    )

    assert [s.text for s in expanded.statements] == ["set sdtm.child;"]


def test_top_level_parse_error_finding_is_renumbered_through_findings_param():
    """Closes the documented gap: the *top-level* file's own parse-error
    findings (not a child's) previously had no path through expand_includes
    at all. Passed via `findings=`, they must join the same renumbering pass
    a child's findings already get, at position 0 (nothing precedes them)."""
    result = split("data work.a;\n/* unterminated\n", "parent.sas")
    assert any(f["type"] == "unterminated_comment" for f in result.findings)

    expanded = expand_includes(
        result.statements,
        result.comments,
        allowed_roots=["."],
        findings=result.findings,
    )

    assert any(f["type"] == "unterminated_comment" for f in expanded.findings)
    broken = next(f for f in expanded.findings if f["type"] == "unterminated_comment")
    assert broken["source"]["statement_order"] == 1


def test_bound_double_ampersand_reference_is_not_substituted():
    """`&&x` must stay unresolved/untouched even when `x` itself is bound --
    the lookahead-only guard would otherwise substitute the second `&` and
    silently produce `&ae` instead of refusing to guess."""
    result = split("%let x = ae;\n%put &&x;\n")
    events = walk_let_statements(result.statements)

    resolved, unresolved = resolve_text("&&x", 2, events, return_unresolved=True)
    assert resolved == "&&x"
    assert unresolved == []


# --- walk_runtime: %os_fvars binding -----------------------------------------


def test_os_fvars_composes_base_plus_colon_path_with_one_trailing_slash():
    result = split("%os_fvars(mvar=_x, projpath=a:b:c);\n")
    surviving, events, findings, skipped = walk_runtime(
        result.statements, os_fvars_base="/base/"
    )

    assert [e.value for e in events if e.name == "_x"] == ["/base/a/b/c/"]
    assert findings == []
    assert skipped == set()
    assert surviving == []


def test_os_fvars_projpath_resolves_when_type_bound_earlier():
    result = split(
        "%let _type = interim;\n%os_fvars(mvar=_x, projpath=&_type.:data:rawxls);\n"
    )
    _, events, findings, _ = walk_runtime(result.statements, os_fvars_base="/base/")

    assert [e.value for e in events if e.name == "_x"] == ["/base/interim/data/rawxls/"]
    assert findings == []


def test_os_fvars_projpath_unbound_type_produces_no_binding_and_a_finding():
    result = split("%os_fvars(mvar=_x, projpath=&_type.:data:rawxls);\n")
    _, events, findings, _ = walk_runtime(result.statements, os_fvars_base="/base/")

    assert [e for e in events if e.name == "_x"] == []
    assert any(f["type"] == "os_fvars_unbound" for f in findings)


def test_os_fvars_without_a_declared_base_binds_nothing():
    """Regression guard: an existing config with no os_fvars_base declared
    must behave exactly as it does today -- no binding, no finding."""
    result = split("%os_fvars(mvar=_x, projpath=a:b:c);\n")
    surviving, events, findings, _ = walk_runtime(result.statements, os_fvars_base=None)

    assert surviving == []
    assert events == []
    assert findings == []


# --- walk_runtime: %IF/%ELSE branch selection --------------------------------


def test_taken_branch_binds_and_sibling_untaken_branch_does_not_overwrite():
    result = split(
        "%let _type = interim;\n"
        "%IF &_type. = tabulate %then %do;\n"
        "  %let _metadata = wrong;\n"
        "%end;\n"
        "%IF &_type. = interim %THEN %do;\n"
        "  %let _metadata = right;\n"
        "%end;\n"
    )
    surviving, events, findings, skipped = walk_runtime(result.statements)

    assert [e.value for e in events if e.name == "_metadata"] == ["right"]
    assert any(f["type"] == "macro_branch_not_taken" for f in findings)
    # The untaken branch's own %let never reaches surviving_statements either.
    assert "%let _metadata = wrong;" not in [s.text for s in surviving]


def test_not_equal_operator_selects_correct_branch():
    result = split(
        "%let _type = odr;\n"
        "%IF &_type. ^= odr %THEN %do;\n"
        "  %let flag = should_not_bind;\n"
        "%end;\n"
    )
    _, events, findings, skipped = walk_runtime(result.statements)

    assert [e for e in events if e.name == "flag"] == []
    assert any(f["type"] == "macro_branch_not_taken" for f in findings)
    assert skipped == {2, 3, 4}


def test_in_condition_selects_correct_branch():
    result = split(
        "%let _type = interim;\n"
        "%IF &_type. in (odr interim) %THEN %do;\n"
        "  %let flag = yes;\n"
        "%end;\n"
    )
    _, events, _, _ = walk_runtime(result.statements)

    assert [e.value for e in events if e.name == "flag"] == ["yes"]


def test_multiline_or_chain_selects_correct_branch():
    result = split(
        "%let _type = primary;\n"
        "%IF &_type. = dmc     or\n"
        "    &_type. = interim or\n"
        "    &_type. = primary or\n"
        "    &_type. = odr        %THEN %do;\n"
        "  %let flag = yes;\n"
        "%end;\n"
    )
    _, events, _, _ = walk_runtime(result.statements)

    assert [e.value for e in events if e.name == "flag"] == ["yes"]


def test_else_branch_taken_when_if_is_false():
    result = split(
        "%let _type = interim;\n"
        "%IF &_type. = tabulate %then %do;\n"
        "  %let branch = tabulate;\n"
        "%end;\n"
        "%ELSE %DO;\n"
        "  %let branch = other;\n"
        "%end;\n"
    )
    _, events, findings, _ = walk_runtime(result.statements)

    assert [e.value for e in events if e.name == "branch"] == ["other"]
    assert any(f["type"] == "macro_branch_not_taken" for f in findings)


def test_symexist_true_when_bound_earlier_false_when_not():
    result = split(
        "%let _rawrand = /some/path/;\n"
        "%if %symexist(_rawrand) %then %do;\n"
        "  %let bound_flag = yes;\n"
        "%end;\n"
        "%if %symexist(_rawspec) %then %do;\n"
        "  %let unbound_flag = yes;\n"
        "%end;\n"
    )
    _, events, findings, skipped = walk_runtime(result.statements)

    assert [e.value for e in events if e.name == "bound_flag"] == ["yes"]
    assert [e for e in events if e.name == "unbound_flag"] == []
    assert any(f["type"] == "macro_branch_not_taken" for f in findings)


def test_unresolvable_condition_yields_a_finding_and_keeps_its_body():
    """`&_pgm` is never bound -- keeps today's posture: reported, not skipped."""
    result = split(
        "%IF &_pgm = gmlibvarlen_gmlib2xpt.sas %then %do;\n"
        "  LIBNAME trim \"&_trim\" COMPRESS=yes;\n"
        "%end;\n"
    )
    surviving, _, findings, skipped = walk_runtime(result.statements)

    assert any(f["type"] == "macro_condition_unresolved" for f in findings)
    assert 'LIBNAME trim "&_trim" COMPRESS=yes;' in [s.text for s in surviving]
    assert skipped == set()


def test_nested_conditionals_skip_without_leaking_statements():
    """Skipping the outer branch must consume its inner %IF/%end pairs whole,
    not leak the inner branch's statements into surviving_statements."""
    result = split(
        "%let _type = listings;\n"
        "%IF &_type. = dmc or &_type. = interim %THEN %do;\n"
        "  %os_fvars(mvar=_metadata, projpath=&_type.:data:metadata);\n"
        "  %IF &_type. = interim %THEN %do;\n"
        "    %let inner = should_not_bind;\n"
        "  %end;\n"
        "  %IF &_type. ^= odr %THEN %do;\n"
        "    %let inner2 = should_not_bind;\n"
        "  %end;\n"
        "%end;\n"
    )
    surviving, events, findings, skipped = walk_runtime(
        result.statements, os_fvars_base="/base/"
    )

    assert [e for e in events if e.name in ("inner", "inner2", "_metadata")] == []
    assert [s.text for s in surviving] == ["%let _type = listings;"]
    # Outer span: statements 2 (%IF) through 10 (outer %end), inclusive --
    # both inner %IF/%end pairs (4-6, 7-9) consumed by depth-counting, not
    # left dangling or double-counted.
    assert skipped == {2, 3, 4, 5, 6, 7, 8, 9, 10}
    assert sum(f["type"] == "macro_branch_not_taken" for f in findings) == 1


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("macro state: all checks passed")


if __name__ == "__main__":
    demo()
