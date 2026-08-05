"""Macro index (dev plan section 15.1-15.3, 21 Phase 4)."""

from pathlib import Path

import conftest  # noqa: F401

from sas_graph.macro_index import build_macro_index

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_finds_macro_defined_in_basic_adae_fixture():
    """section 22's own fixture: macros/gm_derive.sas defines %gm_derive."""
    index = build_macro_index([FIXTURES / "basic_adae" / "macros"])

    assert index.is_known("gm_derive")
    assert not index.has_conflict("gm_derive")
    sites = index.sites_for("gm_derive")
    assert len(sites) == 1
    assert sites[0].file == "gm_derive.sas"


def test_unknown_macro_name_is_not_known():
    index = build_macro_index([FIXTURES / "basic_adae" / "macros"])
    assert not index.is_known("gm_missing")
    assert index.sites_for("gm_missing") == []


def test_duplicate_macro_definitions_across_files_is_a_conflict(tmp_path):
    """Section 15.3's own scenario: same macro name in two files."""
    (tmp_path / "a.sas").write_text("%macro dup(x=);\n%mend dup;\n", encoding="utf-8")
    (tmp_path / "b.sas").write_text("%macro dup(y=);\n%mend dup;\n", encoding="utf-8")

    index = build_macro_index([tmp_path])

    assert index.has_conflict("dup")
    assert len(index.sites_for("dup")) == 2


def test_declared_root_that_does_not_exist_yields_empty_index():
    index = build_macro_index([Path("does") / "not" / "exist"])
    assert not index.is_known("anything")


def test_macro_boundaries_ignore_commented_and_quoted_mend(tmp_path):
    (tmp_path / "test.sas").write_text(
        "%macro test(inds=, outds=);\n"
        "/* %mend test; */\n"
        '%let note = "%mend test;";\n'
        "data &outds.; set &inds.; run;\n"
        "%mend test;\n"
        "/* %macro hidden(); %mend hidden; */\n",
        encoding="utf-8",
    )

    index = build_macro_index([tmp_path])

    site = index.sites_for("test")[0]
    assert "data &outds." in site.body
    assert not index.is_known("hidden")


def test_repeated_and_overlapping_macro_roots_do_not_create_conflicts(tmp_path):
    child = tmp_path / "macros"
    child.mkdir()
    (child / "test.sas").write_text(
        "%macro test(); %mend test;", encoding="utf-8"
    )

    repeated = build_macro_index([child, child])
    overlapping = build_macro_index([tmp_path, child])

    assert len(repeated.sites_for("test")) == 1
    assert len(overlapping.sites_for("test")) == 1
    assert not repeated.has_conflict("test")
    assert not overlapping.has_conflict("test")


def test_prepared_template_sources_use_full_paths_and_file_line_offsets(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    left_file = left / "common.sas"
    right_file = right / "common.sas"
    left_file.write_text(
        "/* preface */\n\n%macro left_test(inds=, outds=);\n"
        "data &outds.; set &inds.; run;\n%mend left_test;\n",
        encoding="utf-8",
    )
    right_file.write_text(
        "%macro right_test(); %mend right_test;", encoding="utf-8"
    )

    index = build_macro_index([tmp_path])
    left_site = index.sites_for("left_test")[0]
    right_site = index.sites_for("right_test")[0]
    opener = left_site.template_blocks[0].opener

    assert opener.file == str(left_file.resolve().as_posix())
    assert opener.line_start == 4
    assert left_site.path != right_site.path
    assert right_site.template_blocks == ()


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            import inspect

            if len(inspect.signature(value).parameters) == 0:
                value()
                print(f"ok  {name}")
    print("macro index: all checks passed (tmp_path-dependent tests need pytest)")


if __name__ == "__main__":
    demo()
