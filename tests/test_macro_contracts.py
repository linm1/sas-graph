"""Macro contracts parsed from `%gm` markdown docs (wayfinder:
map-macro-contract-md-parser.md). No YAML, no approval gate -- see
design-md-contract-shape.md for the locked shape."""

from pathlib import Path

import conftest  # noqa: F401

from sas_graph import macro_contracts as macro_contracts_module
from sas_graph.config import ConfigResult
from sas_graph.macro_contracts import (
    DocParameter,
    _extract_examples,
    load_macro_contracts,
    parse_macro_doc,
)
from sas_graph.run_pipeline import _called_gm_macro_names

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACTS = FIXTURES / "synthetic_multi_program" / "contracts"


def _write_contract_docs(directory):
    """Two neutral `%gm` docs -- two are needed so a name filter can be shown
    to exclude the macro that was not asked for."""
    (directory / "gmApplyLabels.md").write_text(
        "# gmApplyLabels\n\n## Purpose\n\nDoes a thing.\n\n## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| inds | Input. | LIBRARY.DATASET | REQUIRED |\n",
        encoding="utf-8",
    )
    (directory / "gmTrimNames.md").write_text(
        "# gmTrimNames\n\n## Purpose\n\nDoes a thing.\n\n## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| dataIn | Input. | LIBRARY.DATASET | REQUIRED |\n",
        encoding="utf-8",
    )


def test_fixture_contract_directory_loads_its_macro():
    """The synthetic fixture's own contracts/ dir is a real, loadable index."""
    index = load_macro_contracts([CONTRACTS])

    contract = index.get("gmApplyLabels")
    assert contract is not None
    assert contract.parameters["inds"].required


def test_extract_examples_excludes_a_different_macro_named_in_prose():
    """Synthetic, narrower version of the %nrstr bug: prose mentioning some
    other macro's call syntax must not be captured as this doc's example."""
    text = "See %othermacro(a=1) for a related macro. %gm(a=1, b=2);"

    assert _extract_examples(text, "gm") == ("%gm(a=1, b=2)",)


def test_extract_examples_paren_depth_ignores_parens_inside_quotes():
    """A `)` inside a quoted string literal must not end the call early --
    only the real closing paren (after the quote closes) should end it."""
    text = "%gm(x='a ) b', y=1);"

    assert _extract_examples(text, "gm") == ("%gm(x='a ) b', y=1)",)


def test_extract_examples_unbalanced_call_falls_back_to_next_semicolon():
    """Synthetic version of the gmTrimVarLen.md shape: an unbalanced regex
    fragment inside the call, followed immediately (same line, no
    whitespace break) by unrelated prose and another real call -- the
    fallback must stop at the call's own `;`, not swallow what follows."""
    text = (
        "%gm(dataIn = lb, excludeVars = .*id@((?<!^lb).)*;"
        "Unrelated trailing prose on the same line.%gm(dataIn = adsl);"
    )

    assert _extract_examples(text, "gm") == (
        "%gm(dataIn = lb, excludeVars = .*id@((?<!^lb).)*;",
        "%gm(dataIn = adsl)",
    )


def test_repeated_contract_read_rejects_changed_bytes(monkeypatch, tmp_path):
    """Guards the read-once/snapshot-consistency guarantee (see
    source_snapshot.SourceChangedDuringRunError): load_macro_contracts must
    feed parse_macro_doc the same snapshotted text it already tracked, not
    re-read the path a second time. This is the md-based equivalent of the
    deleted YAML-era test of the same name."""
    doc_path = tmp_path / "gm_flaky.md"
    original_text = (
        "# gm_flaky\n\n## Purpose\n\nOriginal purpose text.\n\n"
        "## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| x | y | z | REQUIRED |\n"
    )
    doc_path.write_text(original_text, encoding="utf-8")

    real_read_text = macro_contracts_module.read_text
    calls = []

    def flaky_read_text(path, encoding, snapshots=None):
        text = real_read_text(path, encoding, snapshots)
        calls.append(text)
        if len(calls) == 1:
            # Simulate the file changing after the tracked read but before
            # any second, untracked read would happen.
            Path(path).write_text(
                original_text.replace("Original purpose text.", "CHANGED purpose text."),
                encoding="utf-8",
            )
        return text

    monkeypatch.setattr(macro_contracts_module, "read_text", flaky_read_text)

    snapshots = {}
    index = load_macro_contracts([tmp_path], source_paths=snapshots)

    contract = index.get("gm_flaky")
    assert contract is not None
    # Must reflect the bytes captured at the tracked read, not the bytes
    # written afterward -- a second untracked read would return "CHANGED".
    assert contract.purpose == "Original purpose text."


def test_macro_name_case_preserved_but_lookup_is_case_insensitive(tmp_path):
    """Heading casing differs from call-syntax casing -- the index still
    finds it by any case, and preserves the heading's own casing."""
    _write_contract_docs(tmp_path)

    index = load_macro_contracts([tmp_path])

    contract = index.get("GMTRIMNAMES")
    assert contract is not None
    assert contract.macro == "gmTrimNames"
    assert contract.parameters["dataIn"].required


def test_unresolved_macro_has_no_contract(tmp_path):
    _write_contract_docs(tmp_path)

    index = load_macro_contracts([tmp_path])

    assert index.get("gm_missing") is None


def test_doc_with_no_heading_is_excluded_via_errors(tmp_path):
    (tmp_path / "no_heading.md").write_text(
        "## Purpose\n\nDoes a thing.\n", encoding="utf-8"
    )
    index = load_macro_contracts([tmp_path])
    assert index.get("no_heading") is None


def test_doc_with_no_parameters_table_is_retained_with_errors(tmp_path):
    (tmp_path / "gm_no_table.md").write_text(
        "# gm_no_table\n\n## Purpose\n\nSomething.\n", encoding="utf-8"
    )
    contract = parse_macro_doc(tmp_path / "gm_no_table.md")

    assert contract.macro == "gm_no_table"
    assert contract.parameters == {}
    assert "no Parameters table found" in contract.errors[0]


def test_duplicate_macro_name_across_files_makes_both_unusable(tmp_path):
    body = (
        "# gm_dup\n\n## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| x | y | z | REQUIRED |\n"
    )
    (tmp_path / "one.md").write_text(body, encoding="utf-8")
    (tmp_path / "two.md").write_text(body, encoding="utf-8")

    contract = load_macro_contracts([tmp_path]).get("gm_dup")
    assert contract is not None
    assert any("duplicate contracts for macro name" in error for error in contract.errors)


def test_declared_root_that_does_not_exist_yields_empty_index():
    index = load_macro_contracts([Path("does") / "not" / "exist"])
    assert index.get("anything") is None


def test_macro_names_filter_loads_only_matching_macros(tmp_path):
    _write_contract_docs(tmp_path)

    index = load_macro_contracts([tmp_path], macro_names={"gmTrimNames"})

    assert index.get("gmTrimNames") is not None
    assert "gmapplylabels" not in index.contracts


def test_macro_names_filter_case_insensitive_filename_match(tmp_path):
    _write_contract_docs(tmp_path)

    index = load_macro_contracts([tmp_path], macro_names={"GMTRIMNAMES"})

    contract = index.get("gmTrimNames")
    assert contract is not None
    assert contract.macro == "gmTrimNames"


def test_macro_names_filter_with_no_matching_file_yields_no_contract(tmp_path):
    _write_contract_docs(tmp_path)

    index = load_macro_contracts([tmp_path], macro_names={"gm_does_not_exist"})

    assert index.get("gm_does_not_exist") is None
    assert index.contracts == {}


def test_macro_names_filter_empty_preserves_full_scan_behavior(tmp_path):
    """An empty (falsy) filter is not "match nothing" -- it must fall back to
    the unfiltered full scan."""
    _write_contract_docs(tmp_path)

    filtered_full_scan = load_macro_contracts([tmp_path], macro_names=set())
    unfiltered = load_macro_contracts([tmp_path])

    assert set(filtered_full_scan.contracts) == set(unfiltered.contracts)
    assert filtered_full_scan.get("gmApplyLabels") is not None
    assert filtered_full_scan.get("gmTrimNames") is not None


def test_macro_names_filter_duplicate_filename_across_roots_still_collides(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    body = (
        "# gm_dup\n\n## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| x | y | z | REQUIRED |\n"
    )
    (root_a / "gm_dup.md").write_text(body, encoding="utf-8")
    (root_b / "gm_dup.md").write_text(body, encoding="utf-8")

    contract = load_macro_contracts(
        [root_a, root_b], macro_names={"gm_dup"}
    ).get("gm_dup")

    assert contract is not None
    assert any("duplicate contracts for macro name" in error for error in contract.errors)


def test_called_gm_macro_names_finds_calls_in_comments_and_inactive_branches(tmp_path):
    """The pre-pass is a raw regex scan, not `%include`-aware or macro-state
    aware: a name inside a comment or an untaken `%if 0` branch is still
    picked up (over-fetch is acceptable/expected), and no `%include`
    expansion is needed to find it. Also exercises `setup_file=None` and an
    empty `macro_roots` tuple, since a validated run always sets these but
    the pre-pass must not assume that."""
    (tmp_path / "main.sas").write_text(
        "/* %gmInComment(a=1); */\n"
        "%if 0 %then %do;\n"
        "  %gmInBranch(a=1);\n"
        "%end;\n"
        "%gmReal(a=1);\n",
        encoding="utf-8",
    )
    config_result = ConfigResult(
        status="SUCCESS",
        main_programs=(tmp_path / "main.sas",),
        setup_file=None,
        macro_roots=(),
    )

    names = _called_gm_macro_names(config_result)

    assert names == {"gmInComment", "gmInBranch", "gmReal"}


def test_called_gm_macro_names_scans_sas_files_under_macro_roots(tmp_path):
    """A `%gm` call reachable only via a macro_roots `.sas` file (not the
    main program or setup file) is still discovered."""
    main = tmp_path / "main.sas"
    main.write_text("%gmMain(a=1);\n", encoding="utf-8")
    macro_dir = tmp_path / "macros"
    (macro_dir / "nested").mkdir(parents=True)
    (macro_dir / "nested" / "helper.sas").write_text(
        "%gmFromMacroRoot(a=1);\n", encoding="utf-8",
    )
    config_result = ConfigResult(
        status="SUCCESS",
        main_programs=(main,),
        setup_file=None,
        macro_roots=(macro_dir,),
    )

    names = _called_gm_macro_names(config_result)

    assert names == {"gmMain", "gmFromMacroRoot"}


def test_called_gm_macro_names_scans_every_declared_program_not_just_the_first(tmp_path):
    """A `%gm` name called only by the third of three declared programs must
    still be found -- the pre-pass must not stop after `main_programs[0]`."""
    first = tmp_path / "first.sas"
    second = tmp_path / "second.sas"
    third = tmp_path / "third.sas"
    first.write_text("%gmFirst(a=1);\n", encoding="utf-8")
    second.write_text("%gmSecond(a=1);\n", encoding="utf-8")
    third.write_text("%gmThirdOnly(a=1);\n", encoding="utf-8")
    config_result = ConfigResult(
        status="SUCCESS",
        main_programs=(first, second, third),
        setup_file=None,
        macro_roots=(),
    )

    names = _called_gm_macro_names(config_result)

    assert names == {"gmFirst", "gmSecond", "gmThirdOnly"}


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            import inspect

            if len(inspect.signature(value).parameters) == 0:
                value()
                print(f"ok  {name}")
    print("macro contracts: all checks passed (tmp_path-dependent tests need pytest)")


if __name__ == "__main__":
    demo()
