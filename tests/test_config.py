"""Config loader and allowed-root validation (dev plan sections 8, 5.3, 21 Phase 1).

One test per FAILED bullet in section 5.3 that Phase 1 can reach. Plain asserts
and a `demo()` entry point, matching the renderer tests: no pytest fixtures, so
`python tests/test_config.py` still works.
"""

import tempfile
from contextlib import contextmanager
from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.config import load_config

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "basic_adae" / "project.yaml"

GOOD_CONFIG = """\
main_program: adae.sas
setup_file: setup.sas
allowed_roots:
  - .
macro_roots:
  - macros
macro_contracts:
  - contracts
output_dir: graph_runs
"""


@contextmanager
def project(config_text=GOOD_CONFIG, files=("adae.sas", "setup.sas"), dirs=("macros", "contracts")):
    """Build a throwaway project dir and yield the path to its project.yaml.

    `dirs` exists because a declared-but-absent `macro_roots` is a NOT_EXECUTED
    finding, so a test that wants a clean COMPLETE has to create them. Pass
    `dirs=()` to exercise the missing-root path.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in dirs:
            (root / name).mkdir(parents=True, exist_ok=True)
        for name in files:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("/* fixture */\n", encoding="utf-8")
        config_path = root / "project.yaml"
        config_path.write_text(config_text, encoding="utf-8")
        yield config_path


def statuses(result):
    return {finding["status"] for finding in result.findings}


def objects(result):
    return {finding["object"] for finding in result.findings}


# --- the committed fixture -------------------------------------------------


def test_repo_fixture_validates_clean():
    result = load_config(FIXTURE)

    assert result.status == "COMPLETE"
    assert result.findings == []
    assert result.main_program.name == "adae.sas"
    assert result.setup_file.name == "setup.sas"


def test_relative_paths_resolve_against_the_config_file_not_the_cwd():
    result = load_config(FIXTURE)

    assert result.main_program == (FIXTURE.parent / "adae.sas").resolve()
    assert result.main_program.is_absolute()


# --- section 5.3 FAILED bullets -------------------------------------------


def test_missing_main_program_key_fails():
    with project(GOOD_CONFIG.replace("main_program: adae.sas\n", "")) as cfg:
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert statuses(result) == {"BLOCKED"}
    assert "main_program" in objects(result)


def test_main_program_file_absent_from_disk_fails():
    with project(files=("setup.sas",)) as cfg:
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert "main_program" in objects(result)


def test_missing_setup_file_fails():
    with project(files=("adae.sas",)) as cfg:
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert "setup_file" in objects(result)


def test_more_than_one_setup_file_fails():
    text = GOOD_CONFIG.replace(
        "setup_file: setup.sas\n",
        "setup_file:\n  - setup.sas\n  - setup2.sas\n",
    )
    with project(text, files=("adae.sas", "setup.sas", "setup2.sas")) as cfg:
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert any("exactly one" in f["message"] for f in result.findings)


def test_single_element_list_setup_file_is_accepted():
    text = GOOD_CONFIG.replace("setup_file: setup.sas\n", "setup_file:\n  - setup.sas\n")
    with project(text) as cfg:
        result = load_config(cfg)

    assert result.status == "COMPLETE"


def test_main_program_outside_allowed_roots_fails():
    text = GOOD_CONFIG.replace("main_program: adae.sas", "main_program: ../outside.sas")
    with project(text, files=("adae.sas", "setup.sas", "../outside.sas")) as cfg:
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert any("allowed_roots" in f["message"] for f in result.findings)


def test_dotdot_traversal_back_inside_is_allowed():
    """`.`/`..` that resolves back inside a root is containment, not escape."""
    text = GOOD_CONFIG.replace("main_program: adae.sas", "main_program: sub/../adae.sas")
    with project(text) as cfg:
        result = load_config(cfg)

    assert result.status == "COMPLETE"


def test_sibling_directory_with_shared_prefix_is_not_inside_the_root():
    """`/study/adae-evil` must not count as inside `/study/adae` (string prefix trap)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "adae").mkdir()
        (root / "adae-evil").mkdir()
        (root / "adae" / "setup.sas").write_text("x\n", encoding="utf-8")
        (root / "adae-evil" / "adae.sas").write_text("x\n", encoding="utf-8")

        cfg = root / "adae" / "project.yaml"
        cfg.write_text(
            "main_program: ../adae-evil/adae.sas\n"
            "setup_file: setup.sas\n"
            "allowed_roots:\n  - .\n"
            "output_dir: graph_runs\n",
            encoding="utf-8",
        )
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert any("allowed_roots" in f["message"] for f in result.findings)


def test_unreadable_required_file_fails():
    """A directory standing where a file is declared is unreadable on every OS.

    `chmod 0o000` is a no-op on Windows, so it cannot be used here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "adae.sas").mkdir()  # a directory, not a file
        (root / "setup.sas").write_text("x\n", encoding="utf-8")
        cfg = root / "project.yaml"
        cfg.write_text(GOOD_CONFIG, encoding="utf-8")
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert any("read" in f["message"] for f in result.findings)


def test_prohibited_dataset_extension_fails():
    text = GOOD_CONFIG.replace("main_program: adae.sas", "main_program: ae.sas7bdat")
    with project(text, files=("ae.sas7bdat", "setup.sas")) as cfg:
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert any("prohibited" in f["message"] for f in result.findings)


def test_prohibited_production_path_segment_fails():
    text = GOOD_CONFIG.replace("main_program: adae.sas", "main_program: production/adae.sas")
    with project(text, files=("production/adae.sas", "setup.sas")) as cfg:
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert any("prohibited" in f["message"] for f in result.findings)


def test_prohibited_matching_is_on_path_segments_not_substrings():
    """`reproduce/` contains 'prod' but is not a production path."""
    text = GOOD_CONFIG.replace("main_program: adae.sas", "main_program: reproduce/adae.sas")
    with project(text, files=("reproduce/adae.sas", "setup.sas")) as cfg:
        result = load_config(cfg)

    assert result.status == "COMPLETE"


def test_declared_but_absent_macro_root_is_partial_not_silent():
    """Section 26: expose the unknown. An empty macro index needs a reason."""
    with project(dirs=()) as cfg:
        result = load_config(cfg)

    assert result.status == "PARTIAL"
    assert result.ok  # PARTIAL still yields a usable config
    assert statuses(result) == {"NOT_EXECUTED"}
    assert "macro_roots" in objects(result)
    assert result.macro_roots == ()


def test_absent_macro_root_does_not_fail_the_run():
    """Section 5.3 does not list a missing macro root; 5.2 makes it PARTIAL."""
    with project(dirs=()) as cfg:
        result = load_config(cfg)

    assert result.status != "FAILED"
    assert result.main_program is not None


def test_malformed_yaml_fails_rather_than_raising():
    with project("main_program: [unclosed\n") as cfg:
        result = load_config(cfg)

    assert result.status == "FAILED"
    assert any("YAML" in f["message"] for f in result.findings)


def test_missing_config_file_fails():
    with tempfile.TemporaryDirectory() as tmp:
        result = load_config(Path(tmp) / "nope.yaml")

    assert result.status == "FAILED"


# --- reporting shape -------------------------------------------------------


def test_all_errors_are_reported_in_one_pass():
    """`validate` must not stop at the first problem."""
    with project(files=()) as cfg:
        result = load_config(cfg)

    assert objects(result) >= {"main_program", "setup_file"}


def test_findings_carry_the_config_file_as_their_source():
    with project(files=("setup.sas",)) as cfg:
        result = load_config(cfg)
        expected = cfg.name

    source = result.findings[0]["source"]
    assert source["file"] == expected
    assert source["rule"]


def test_findings_render_through_the_existing_findings_renderer():
    """Config findings must use a status the section-18 renderer knows."""
    from sas_graph.renderer_findings import render

    with project(files=("setup.sas",)) as cfg:
        result = load_config(cfg)

    out = render({"run_status": result.status, "nodes": [], "findings": result.findings})
    assert "UNRECOGNISED STATUS" not in out
    assert "main_program" in out


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("config loader: all checks passed")


if __name__ == "__main__":
    demo()
