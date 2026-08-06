"""Macro contracts parsed from `%gm` markdown docs (wayfinder:
map-macro-contract-md-parser.md). No YAML, no approval gate -- see
design-md-contract-shape.md for the locked shape."""

from pathlib import Path

import conftest  # noqa: F401
import pytest

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
CONTRACTS = FIXTURES / "qc_adae" / "contracts"
# qc_adae is a real, anonymized client SAS program excluded from the public
# repo (non-negotiable, see staging manifest). Skip the tests that depend on
# it rather than failing when the fixture directory is absent.
_QC_ADAE_AVAILABLE = CONTRACTS.exists()
_QC_ADAE_SKIP_REASON = "tests/fixtures/qc_adae/ excluded from the public repo"


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_parses_gmmergesupp_purpose_parameters_and_examples():
    if not _QC_ADAE_AVAILABLE:
        return
    contract = parse_macro_doc(CONTRACTS / "gmMergeSupp.md")

    assert contract.macro == "gmMergeSupp"
    assert not contract.errors
    assert contract.purpose.startswith("This macro is designed to perform")
    assert contract.parameters["dataMain"] == DocParameter(required=True)
    assert contract.parameters["dataOut"] == DocParameter(required=True)
    assert contract.parameters["selectType"] == DocParameter(required=False, default="ERROR")
    assert any(example.startswith("%gmMergeSupp(dataMain=raw.ds") for example in contract.examples)


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_gmmergesupp_examples_exact_tuple_excludes_nrstr_prose_reference():
    """Regression for the `%nrstr(%%)gmMergeSUPP(....)` prose bug: that line
    (in the Discussion's `Call execute` snippet) names the doc's own macro
    but through an `%nrstr` escape, not a real call -- it must not appear.
    Exact-equality here (not `any(...startswith...)`) is the point: the old
    assertion style let both the %nrstr bug and paren corruption slip past."""
    if not _QC_ADAE_AVAILABLE:
        return
    contract = parse_macro_doc(CONTRACTS / "gmMergeSupp.md")

    assert contract.examples == (
        "%gmMergeSupp( dataMain = ,dataSupp = ,dataOut = ,selectQnam = "
        ",selectQnamName = ,varsNum = ,qnamBlank = ,selectType = ERROR )",
        "%gmMergeSupp(dataMain=raw.ds, dataOut=dsPlus)",
        "%gmMergeSupp(dataMain=raw.lbCh, dataSupp= raw.suppLb, dataOut=lbChPlus, selectType = ABORT)",
        "%gmMergeSupp(dataMain=raw.lb, dataOut=lbPlus, varsNum =lbArRef@visDay)",
        "%gmMergeSupp(dataMain=raw.lb, dataSupp=raw.supplb, dataOut=lb, qnamBlank=blank01 @ blank02)",
        "%gmMergeSupp(dataMain=raw.ae, dataSupp=raw.suppae, dataOut=ae, selectQnam=Y, "
        "selectQnamNames=(qnam in (“MEDDRA”, “MEDDRAVER”)))",
    )


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_gmtrimvarlen_examples_exact_tuple_stops_unbalanced_call_at_semicolon():
    """Regression for the unbalanced-paren fallback bug: the whole Discussion
    section is one giant line in this fixture, so falling back to "next
    newline" swallowed every following example into one bogus entry. The
    fixed fallback stops at the call's own `;` instead, so the two later
    adsl examples still come back as their own separate entries."""
    if not _QC_ADAE_AVAILABLE:
        return
    contract = parse_macro_doc(CONTRACTS / "gmTrimVarLen.md")

    assert contract.examples == (
        "%GmTrimVarLen( dataIn= ,excludeVars= ,selectType=ABORT ,splitChar=@ )",
        "%gmTrimVarLen(dataIn = datasetName, excludeVars = "
        "&_fixedLengthVars@datasetSpecificVar1@datasetSpecificVar2)",
        "%gmTrimVarLen(dataIn = ie)",
        "%gmTrimVarLen(dataIn = lb, excludeVars = .*id@((?<!^lb).)*;",
        "%gmTrimVarLen(dataIn = adsl, excludeVars = usubjId#armCd, splitChar = #)",
        "%gmTrimVarLen(dataIn = adsl, excludeVars = (?!(var1|var2)$).*)",
    )


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


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_parses_gmcompare_examples_spanning_multiple_lines():
    if not _QC_ADAE_AVAILABLE:
        return
    contract = parse_macro_doc(CONTRACTS / "gmCompare.md")

    assert contract.macro == "gmCompare"
    assert contract.parameters["dataMain"] == DocParameter(required=True)
    assert contract.parameters["libraryQC"] == DocParameter(required=True)
    assert any("dataMain = main.ae" in example for example in contract.examples)


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_macro_name_case_preserved_but_lookup_is_case_insensitive():
    """gmTrimVarLen.md: heading casing differs from call-syntax casing --
    the index still finds it by any case."""
    if not _QC_ADAE_AVAILABLE:
        return
    index = load_macro_contracts([CONTRACTS])

    contract = index.get("GMTRIMVARLEN")
    assert contract is not None
    assert contract.macro == "gmTrimVarLen"
    assert contract.parameters["dataIn"].required


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_unresolved_macro_has_no_contract():
    if not _QC_ADAE_AVAILABLE:
        return
    index = load_macro_contracts([CONTRACTS])
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


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_macro_names_filter_loads_only_matching_macros():
    if not _QC_ADAE_AVAILABLE:
        return
    index = load_macro_contracts([CONTRACTS], macro_names={"gmTrimVarLen"})

    assert index.get("gmTrimVarLen") is not None
    assert "gmcompare" not in index.contracts
    assert "gmmergesupp" not in index.contracts
    assert "gmmapdsattrib" not in index.contracts


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_macro_names_filter_case_insensitive_filename_match():
    if not _QC_ADAE_AVAILABLE:
        return
    index = load_macro_contracts([CONTRACTS], macro_names={"GMTRIMVARLEN"})

    contract = index.get("gmTrimVarLen")
    assert contract is not None
    assert contract.macro == "gmTrimVarLen"


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_macro_names_filter_with_no_matching_file_yields_no_contract():
    if not _QC_ADAE_AVAILABLE:
        return
    index = load_macro_contracts([CONTRACTS], macro_names={"gm_does_not_exist"})

    assert index.get("gm_does_not_exist") is None
    assert index.contracts == {}


@pytest.mark.skipif(not _QC_ADAE_AVAILABLE, reason=_QC_ADAE_SKIP_REASON)
def test_macro_names_filter_empty_preserves_full_scan_behavior():
    if not _QC_ADAE_AVAILABLE:
        return
    filtered_full_scan = load_macro_contracts([CONTRACTS], macro_names=set())
    unfiltered = load_macro_contracts([CONTRACTS])

    assert filtered_full_scan.contracts == unfiltered.contracts
    assert set(unfiltered.contracts) == {
        "gmtrimvarlen", "gmcompare", "gmmergesupp", "gmmapdsattrib",
    }


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
        main_program=tmp_path / "main.sas",
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
        main_program=main,
        setup_file=None,
        macro_roots=(macro_dir,),
    )

    names = _called_gm_macro_names(config_result)

    assert names == {"gmMain", "gmFromMacroRoot"}


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
