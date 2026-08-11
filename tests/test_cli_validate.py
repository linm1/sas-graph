"""`sas-graph validate` end to end (dev plan sections 20, 21 Phase 1).

Exit codes are the contract a CI job depends on, so they get their own checks:
0 clean, 1 validation FAILED, 2 not-yet-built.
"""

import json
import tempfile
from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)
import pytest

from sas_graph import cli as cli_module
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
        assert manifest["config"]["main_programs"] == []


def test_run_on_the_section_22_fixture_succeeds():
    """Phase 4: `run` now builds a real graph instead of refusing."""
    assert main(["run", "--config", str(FIXTURE)]) == 0
    run_dir = max((FIXTURE.parent / "graph_runs" / "runs").iterdir())
    assert {path.name for path in run_dir.iterdir()} == {
        "graph.json", "graph.mmd", "findings.md", "manifest.json", "programs"
    }
    program_dir = next((run_dir / "programs").glob("*_adae.sas"))
    assert {path.name for path in program_dir.iterdir()} == {
        "graph.json", "findings.md", "manifest.json"
    }


def _multi_program_project(tmp, names, output_dir="graph_runs"):
    """Ticket 02: N declared programs, all trivial/empty (comment-only) so
    the merged parse stays COMPLETE with no findings."""
    root = Path(tmp)
    root.mkdir(parents=True, exist_ok=True)
    for name in (*names, "setup.sas"):
        (root / name).write_text("/* fixture */\n", encoding="utf-8")
    program_list = "".join(f"  - {name}\n" for name in names)
    cfg = root / "project.yaml"
    cfg.write_text(
        f"main_program:\n{program_list}"
        "setup_file: setup.sas\n"
        "allowed_roots:\n  - .\n"
        f"output_dir: {output_dir}\n",
        encoding="utf-8",
    )
    return cfg


def test_three_program_run_produces_merged_plus_per_program_debug_artifacts(tmp_path):
    names = ["prog_a.sas", "prog_b.sas", "prog_c.sas"]
    cfg = _multi_program_project(tmp_path, names)

    assert main(["run", "--config", str(cfg)]) == 0

    run_dir = max((tmp_path / "graph_runs" / "runs").iterdir())
    assert {p.name for p in run_dir.iterdir()} == {
        "graph.json", "graph.mmd", "findings.md", "manifest.json", "programs"
    }
    assert {p.name for p in (run_dir / "programs").iterdir()} == {
        f"{index:03d}_{name}" for index, name in enumerate(names)
    }

    merged_manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(merged_manifest["config"]["main_programs"]) == 3

    for name in names:
        program_dir = next((run_dir / "programs").glob(f"*_{name}"))
        assert {p.name for p in program_dir.iterdir()} == {
            "graph.json", "findings.md", "manifest.json"
        }
        program_manifest = json.loads((program_dir / "manifest.json").read_text(encoding="utf-8"))
        assert program_manifest["run_id"] == run_dir.name
        expected = [p for p in merged_manifest["config"]["main_programs"] if p.endswith(name)]
        assert program_manifest["config"]["main_programs"] == expected


def test_overall_status_reflects_a_worse_per_program_debug_result_but_exit_code_follows_merged(
    tmp_path, capsys
):
    """The discriminating test: a real cross-program coupling makes one
    program's isolated debug re-parse genuinely worse than the merged run,
    and that must surface in the printed overall status without changing the
    process exit code, which stays governed solely by the merged graph's own
    status.

    The coupling: `_called_gm_macro_names`'s lazy-contract pre-pass unions
    `%gm...` names across *all* declared programs (without expanding
    `%include`) to decide which `.md` contracts to load. `prog_a.sas` calls
    `%gm_shared` directly, so the merged run's pre-pass finds that name and
    loads its contract -- which then also covers `prog_b.sas`'s own call,
    reachable only after `%include "child.sas"` expands. `prog_b.sas` also
    mentions an unrelated `%gm_dummy` name in a comment (regex text scan, no
    parsing) so its own pre-pass is non-empty and filtered, not the
    empty-set full-scan fallback. Isolated to just `prog_b.sas`, the
    pre-pass never sees `gm_shared` at all, the contract is never loaded,
    and `child.sas`'s call resolves to nothing -- `UNRESOLVED_MACRO_SOURCE`,
    which degrades that debug parse alone to PARTIAL."""
    root = tmp_path
    contracts = root / "contracts"
    contracts.mkdir()
    contracts.joinpath("gm_shared.md").write_text(
        "# gm_shared\n\n## Purpose\n\nShared macro.\n\n## Parameters\n\n"
        "| Parameter | Description | Acceptable Values | Default Value |\n"
        "|---|---|---|---|\n"
        "| inds | Input. | LIBRARY.DATASET | REQUIRED |\n"
        "| outds | Output. | LIBRARY.DATASET | REQUIRED |\n",
        encoding="utf-8",
    )
    (root / "setup.sas").write_text("/* setup */\n", encoding="utf-8")
    (root / "prog_a.sas").write_text(
        "%gm_shared(inds=sdtm.ae, outds=work.a);\n", encoding="utf-8"
    )
    (root / "prog_b.sas").write_text(
        '/* %gm_dummy(); */\n%include "child.sas";\n', encoding="utf-8"
    )
    (root / "child.sas").write_text(
        "%gm_shared(inds=sdtm.dv, outds=work.b);\n", encoding="utf-8"
    )
    cfg = root / "project.yaml"
    cfg.write_text(
        "main_program:\n  - prog_a.sas\n  - prog_b.sas\n"
        "setup_file: setup.sas\n"
        "allowed_roots:\n  - .\n"
        "macro_contracts:\n  - contracts\n"
        "output_dir: graph_runs\n",
        encoding="utf-8",
    )

    exit_code = main(["run", "--config", str(cfg)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "run COMPLETE:" in out
    assert "overall (incl. per-program debug): PARTIAL" in out

    run_dir = max((root / "graph_runs" / "runs").iterdir())
    merged_manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert merged_manifest["run_status"] == "COMPLETE"

    prog_a_dir = next((run_dir / "programs").glob("*_prog_a.sas"))
    prog_a_manifest = json.loads((prog_a_dir / "manifest.json").read_text(encoding="utf-8"))
    assert prog_a_manifest["run_status"] == "COMPLETE"
    prog_b_dir = next((run_dir / "programs").glob("*_prog_b.sas"))
    prog_b_manifest = json.loads((prog_b_dir / "manifest.json").read_text(encoding="utf-8"))
    assert prog_b_manifest["run_status"] == "PARTIAL"


def test_run_removes_entire_nested_tree_when_a_program_debug_manifest_write_raises(
    tmp_path, monkeypatch
):
    """Ticket 02: cleanup must remove the whole nested tree, not just the
    merged files, when a failure happens during per-program debug writing."""
    cfg = _project(tmp_path)
    real_save_manifest = cli_module.save_manifest
    calls = {"n": 0}

    def fake_save_manifest(manifest, path):
        calls["n"] += 1
        if calls["n"] > 1:
            # Confirms the tree really is partially written at failure time
            # -- this isn't just an order-dependent assumption.
            assert (Path(path).parent / "graph.json").exists()
            raise OSError("disk full during per-program manifest write")
        return real_save_manifest(manifest, path)

    monkeypatch.setattr(cli_module, "save_manifest", fake_save_manifest)

    with pytest.raises(OSError, match="disk full during per-program manifest write"):
        main(["run", "--config", str(cfg)])

    runs = tmp_path / "graph_runs" / "runs"
    assert not runs.exists() or not list(runs.iterdir())


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


def test_run_survives_two_declared_programs_sharing_a_basename(tmp_path):
    """Regression: `a/adae.sas` and `b/adae.sas` used to collide on the bare
    `program.name` debug directory, crashing `mkdir` mid-run and discarding
    the whole (otherwise-good) merged output. The per-program directory is
    now keyed by declaration index, so same-basename programs from different
    directories each get their own directory."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "adae.sas").write_text("data work.a; run;\n", encoding="utf-8")
    (tmp_path / "b" / "adae.sas").write_text("data work.b; run;\n", encoding="utf-8")
    (tmp_path / "setup.sas").write_text("/* fixture */\n", encoding="utf-8")
    cfg = tmp_path / "project.yaml"
    cfg.write_text(
        "main_program:\n  - a/adae.sas\n  - b/adae.sas\n"
        "setup_file: setup.sas\n"
        "allowed_roots:\n  - .\n"
        "output_dir: graph_runs\n",
        encoding="utf-8",
    )

    assert main(["run", "--config", str(cfg)]) == 0

    run_dir = max((tmp_path / "graph_runs" / "runs").iterdir())
    program_dirs = sorted((run_dir / "programs").iterdir())
    assert [d.name for d in program_dirs] == ["000_adae.sas", "001_adae.sas"]

    a_manifest = json.loads((program_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
    b_manifest = json.loads((program_dirs[1] / "manifest.json").read_text(encoding="utf-8"))
    assert a_manifest["config"]["main_programs"] == [str((tmp_path / "a" / "adae.sas").resolve())]
    assert b_manifest["config"]["main_programs"] == [str((tmp_path / "b" / "adae.sas").resolve())]


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print("cli validate: all checks passed")


if __name__ == "__main__":
    demo()
