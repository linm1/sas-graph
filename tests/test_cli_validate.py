"""`sas-graph validate` end to end (dev plan sections 20, 21 Phase 1).

Exit codes are the contract a CI job depends on, so they get their own checks:
0 clean, 1 validation FAILED, 2 not-yet-built.
"""

import json
import tempfile
from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)
import pytest

from sas_graph.cli import main
from sas_graph.source_snapshot import SourceChangedDuringRunError

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "basic_adae" / "project.yaml"

CONFIG = """\
main_program: adae.sas
setup_file: setup.sas
allowed_roots:
  - .
output_dir: {output_dir}
"""


def _project(tmp, main_program="adae.sas", output_dir="graph_runs"):
    root = Path(tmp)
    for name in ("adae.sas", "setup.sas"):
        (root / name).write_text("/* fixture */\n", encoding="utf-8")
    cfg = root / "project.yaml"
    cfg.write_text(
        CONFIG.format(output_dir=output_dir).replace(
            "main_program: adae.sas", f"main_program: {main_program}"
        ),
        encoding="utf-8",
    )
    return cfg


def test_clean_config_exits_zero():
    with tempfile.TemporaryDirectory() as tmp:
        assert main(["validate", "--config", str(_project(tmp))]) == 0


def test_failed_config_exits_one():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _project(tmp, main_program="../escaped.sas")
        assert main(["validate", "--config", str(cfg)]) == 1


def test_validate_writes_the_section_4_run_layout():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _project(tmp)
        main(["validate", "--config", str(cfg)])

        runs = sorted((Path(tmp) / "graph_runs" / "runs").iterdir())
        assert len(runs) == 1

        manifest = json.loads((runs[0] / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["run_id"] == runs[0].name
        assert manifest["run_status"] == "COMPLETE"
        assert len(manifest["sources"]) == 3


def test_rapid_validations_use_distinct_run_directories():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _project(tmp)
        assert main(["validate", "--config", str(cfg)]) == 0
        assert main(["validate", "--config", str(cfg)]) == 0

        runs = list((Path(tmp) / "graph_runs" / "runs").iterdir())
        assert len(runs) == 2
        assert len({run.name for run in runs}) == 2
        assert all(json.loads((run / "manifest.json").read_text(encoding="utf-8"))["run_id"] == run.name for run in runs)


def test_failed_validate_still_leaves_a_manifest_behind():
    """A FAILED run has no graph, so the manifest is the only record of it."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _project(tmp, main_program="../escaped.sas")
        assert main(["validate", "--config", str(cfg)]) == 1

        runs = sorted((Path(tmp) / "graph_runs" / "runs").iterdir())
        manifest = json.loads((runs[0] / "manifest.json").read_text(encoding="utf-8"))

        assert manifest["run_status"] == "FAILED"
        assert manifest["findings_count"] >= 1
        assert manifest["config"]["main_program"] is None


def test_run_on_the_section_22_fixture_succeeds():
    """Phase 4: `run` now builds a real graph instead of refusing."""
    assert main(["run", "--config", str(FIXTURE)]) == 0
    run_dir = max((FIXTURE.parent / "graph_runs" / "runs").iterdir())
    assert {path.name for path in run_dir.iterdir()} == {
        "graph.json", "graph.mmd", "findings.md", "manifest.json"
    }


def test_run_removes_reserved_directory_when_the_pipeline_raises(tmp_path, monkeypatch):
    cfg = _project(tmp_path)
    monkeypatch.setattr("sas_graph.cli.run_pipeline", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        main(["run", "--config", str(cfg)])

    runs = tmp_path / "graph_runs" / "runs"
    assert not runs.exists() or not list(runs.iterdir())


def test_run_removes_reserved_directory_when_manifest_write_raises(tmp_path, monkeypatch):
    cfg = _project(tmp_path)
    monkeypatch.setattr("sas_graph.cli.save_manifest", lambda *args: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        main(["run", "--config", str(cfg)])

    runs = tmp_path / "graph_runs" / "runs"
    assert not runs.exists() or not list(runs.iterdir())


def test_repeated_unchanged_include_has_one_manifest_snapshot(tmp_path):
    cfg = _project(tmp_path)
    child = tmp_path / "child.sas"
    child.write_text("data work.child; run;\n", encoding="utf-8")
    (tmp_path / "adae.sas").write_text(
        '%include "child.sas";\n%include "child.sas";\n', encoding="utf-8"
    )

    assert main(["run", "--config", str(cfg)]) == 0

    run_dir = next((tmp_path / "graph_runs" / "runs").iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [entry["path"] for entry in manifest["sources"]].count(str(child.resolve())) == 1


def test_changed_repeated_include_aborts_without_publishing_a_mixed_run(tmp_path, monkeypatch):
    cfg = _project(tmp_path)
    child = tmp_path / "child.sas"
    child.write_text("data work.old; run;\n", encoding="utf-8")
    (tmp_path / "adae.sas").write_text(
        '%include "child.sas";\n%include "child.sas";\n', encoding="utf-8"
    )
    original_read_bytes = Path.read_bytes
    reads = 0

    def edit_after_first_child_read(path):
        nonlocal reads
        data = original_read_bytes(path)
        if Path(path).resolve() == child.resolve() and reads == 0:
            reads += 1
            child.write_text("data work.new; run;\n", encoding="utf-8")
        return data

    monkeypatch.setattr(Path, "read_bytes", edit_after_first_child_read)
    with pytest.raises(SourceChangedDuringRunError):
        main(["run", "--config", str(cfg)])

    runs = tmp_path / "graph_runs" / "runs"
    assert not runs.exists() or not list(runs.iterdir())


def test_run_manifest_tracks_only_files_the_pipeline_read(tmp_path):
    (tmp_path / "left").mkdir()
    (tmp_path / "right").mkdir()
    (tmp_path / "macros").mkdir()
    (tmp_path / "contracts").mkdir()
    (tmp_path / "setup.sas").write_text('%include "left/common.sas";\n', encoding="utf-8")
    (tmp_path / "left" / "common.sas").write_text('%include "nested.sas";\n', encoding="utf-8")
    (tmp_path / "left" / "nested.sas").write_text("data work.left; run;\n", encoding="utf-8")
    (tmp_path / "right" / "common.sas").write_text("data work.right; run;\n", encoding="utf-8")
    (tmp_path / "main.sas").write_text(
        '%include "right/common.sas";\n%include "missing.sas";\n%dupe();\n%gm_contract(inds=sdtm.ae, outds=adam.ae);\n%gm_bad();\n',
        encoding="utf-8",
    )
    for name in ("a.sas", "b.sas"):
        (tmp_path / "macros" / name).write_text("%macro dupe(); %mend dupe;\n", encoding="utf-8")
    (tmp_path / "contracts" / "gm_contract.md").write_text(
        "# gm_contract\n\n## Purpose\n\nMerges datasets.\n\n## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| inds | Input dataset. | LIBRARY.DATASET | REQUIRED |\n"
        "| outds | Output dataset. | LIBRARY.DATASET | REQUIRED |\n",
        encoding="utf-8",
    )
    (tmp_path / "contracts" / "gm_bad.md").write_text(
        "## Purpose\n\nNo heading, so this doc cannot be indexed by macro.\n",
        encoding="utf-8",
    )
    config = tmp_path / "project.yaml"
    config.write_text(
        "main_program: main.sas\nsetup_file: setup.sas\nallowed_roots:\n  - .\noutput_dir: graph_runs\nmacro_roots:\n  - macros\nmacro_contracts:\n  - contracts\n",
        encoding="utf-8",
    )

    assert main(["run", "--config", str(config)]) == 0
    run_dir = next((tmp_path / "graph_runs" / "runs").iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    sources = [Path(entry["path"]) for entry in manifest["sources"]]

    assert sources == sorted(sources, key=str)
    assert {path.relative_to(tmp_path).as_posix() for path in sources} == {
        "project.yaml", "setup.sas", "main.sas", "left/common.sas", "left/nested.sas",
        "right/common.sas", "macros/a.sas", "macros/b.sas",
        "contracts/gm_contract.md", "contracts/gm_bad.md",
    }
    assert not any(path.name == "missing.sas" for path in sources)
    assert "include_file_missing" in (run_dir / "findings.md").read_text(encoding="utf-8")


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("cli validate: all checks passed")


if __name__ == "__main__":
    demo()
