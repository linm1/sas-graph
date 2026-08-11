"""Manifest skeleton (dev plan sections 4, 4.2 step 7, 21 Phase 1)."""

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import conftest  # noqa: F401  (puts src/ on sys.path)

from sas_graph.config import load_config
from sas_graph.manifest import build_manifest, create_run_dir, new_run_id, save_manifest
from sas_graph.run_pipeline import run
from sas_graph.source_snapshot import read_bytes

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "basic_adae" / "project.yaml"
FROZEN = datetime(2026, 7, 29, 23, 40, 11, tzinfo=timezone.utc)


def test_run_id_is_sortable_and_legal_as_a_directory_name():
    run_id = new_run_id(FROZEN)

    assert run_id.startswith("20260729T234011Z-")
    assert not set(run_id) & set(':\\/*?"<>|')
    assert new_run_id(FROZEN) < new_run_id(datetime(2026, 7, 30, tzinfo=timezone.utc))
    assert new_run_id(FROZEN) != new_run_id(FROZEN)


def test_run_directory_retries_without_overwriting_an_existing_run(tmp_path, monkeypatch):
    occupied = tmp_path / "runs" / "occupied"
    occupied.mkdir(parents=True)
    (occupied / "manifest.json").write_text("original", encoding="utf-8")
    run_ids = iter(("occupied", "fresh"))
    monkeypatch.setattr("sas_graph.manifest.new_run_id", lambda now=None: next(run_ids))

    run_id, run_dir = create_run_dir(tmp_path)

    assert (run_id, run_dir.name) == ("fresh", "fresh")
    assert (occupied / "manifest.json").read_text(encoding="utf-8") == "original"


def test_hash_covers_exact_bytes_so_crlf_and_lf_differ():
    """Provenance is over bytes, not over equivalent-looking source."""
    with tempfile.TemporaryDirectory() as tmp:
        crlf = Path(tmp) / "crlf.sas"
        lf = Path(tmp) / "lf.sas"
        crlf.write_bytes(b"data x;\r\nrun;\r\n")
        lf.write_bytes(b"data x;\nrun;\n")

        snapshots = {}
        read_bytes(crlf, snapshots)
        read_bytes(lf, snapshots)

        assert snapshots[crlf.resolve()]["sha256"] != snapshots[lf.resolve()]["sha256"]
        assert len(snapshots[lf.resolve()]["sha256"]) == 64


def test_manifest_uses_bytes_captured_during_reads_after_sources_change(tmp_path):
    (tmp_path / "macros").mkdir()
    (tmp_path / "contracts").mkdir()
    setup = tmp_path / "setup.sas"
    main = tmp_path / "main.sas"
    child = tmp_path / "child.sas"
    macro = tmp_path / "macros" / "source.sas"
    contract = tmp_path / "contracts" / "source.md"
    config = tmp_path / "project.yaml"
    setup.write_bytes(b"\xef\xbb\xbf%let domain=ae;\n")
    main.write_bytes(b'%include "child.sas";\n%source(inds=sdtm.ae, outds=adam.ae);\n')
    child.write_bytes(b"data work.child; run;\n")
    macro.write_bytes(b"%macro source(inds=, outds=); %mend source;\n")
    contract.write_bytes(
        b"# source\n\n## Purpose\n\nDoes a thing.\n\n## Parameters\n\n"
        b"| Parameter | Description | Acceptable Values | Default Value |\n"
        b"|---|---|---|---|\n"
        b"| inds | Input. | LIBRARY.DATASET | REQUIRED |\n"
        b"| outds | Output. | LIBRARY.DATASET | REQUIRED |\n"
    )
    config.write_bytes(
        b"main_program: main.sas\nsetup_file: setup.sas\nallowed_roots:\n  - .\noutput_dir: graph_runs\nmacro_roots:\n  - macros\nmacro_contracts:\n  - contracts\n"
    )
    expected = {path.resolve(): path.read_bytes() for path in (config, setup, main, child, macro, contract)}
    snapshots = {}

    result = load_config(config, snapshots)
    run(result, "snapshot", snapshots)
    for path in expected:
        path.write_bytes(b"changed after read")
    child.unlink()

    manifest = build_manifest(result, sources=snapshots)
    actual = {Path(entry["path"]): entry for entry in manifest["sources"]}

    assert set(actual) == set(expected)
    assert {path: entry["sha256"] for path, entry in actual.items()} == {
        path: hashlib.sha256(data).hexdigest() for path, data in expected.items()
    }
    assert {path: entry["bytes"] for path, entry in actual.items()} == {
        path: len(data) for path, data in expected.items()
    }


def test_manifest_records_config_provenance_and_source_hashes():
    result = load_config(FIXTURE)
    manifest = build_manifest(result, now=FROZEN)

    assert manifest["schema_version"] == "0.2.0"
    assert manifest["run_id"].startswith("20260729T234011Z-")
    assert manifest["run_status"] == "COMPLETE"
    assert manifest["generated_at"].startswith("2026-07-29T23:40:11")
    assert manifest["findings_count"] == 0

    assert [Path(p).name for p in manifest["config"]["main_programs"]] == ["adae.sas"]
    assert manifest["config"]["allowed_roots"]

    hashed = {Path(entry["path"]).name for entry in manifest["sources"]}
    assert hashed == {"project.yaml", "setup.sas", "adae.sas"}
    assert all(len(entry["sha256"]) == 64 for entry in manifest["sources"])
    assert all(entry["bytes"] > 0 for entry in manifest["sources"])


def test_manifest_findings_count_reflects_the_run_graph_not_the_config_result():
    """A successful run's config_result.findings is always empty (config was
    valid) -- build_manifest must not silently report 0 findings when the
    graph itself has real ones. A prior acceptance run exposed this exact
    mismatch between manifest.json and graph.json."""
    result = load_config(FIXTURE)
    graph = run(result, run_id="findings-count-check")

    manifest = build_manifest(
        result, run_status=graph["run_status"], findings=graph["findings"], now=FROZEN
    )

    assert manifest["findings_count"] == len(graph["findings"])
    assert manifest["findings_count"] > 0


def test_sources_are_sorted_for_deterministic_manifests():
    manifest = build_manifest(load_config(FIXTURE), now=FROZEN)

    assert [entry["path"] for entry in manifest["sources"]] == sorted(
        entry["path"] for entry in manifest["sources"]
    )


def test_failed_config_still_produces_a_manifest_naming_the_problem():
    """A FAILED run has no graph, so the manifest is the only record it happened."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "project.yaml"
        cfg.write_text("allowed_roots:\n  - .\noutput_dir: out\n", encoding="utf-8")
        result = load_config(cfg)
        manifest = build_manifest(result, now=FROZEN)

    assert manifest["run_status"] == "FAILED"
    assert manifest["findings_count"] >= 2
    assert [Path(entry["path"]).name for entry in manifest["sources"]] == ["project.yaml"]
    assert manifest["config"]["main_programs"] == []


def test_saved_manifest_round_trips_as_json():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "runs" / "demo" / "manifest.json"
        manifest = build_manifest(load_config(FIXTURE), now=FROZEN)
        written = save_manifest(manifest, target)

        assert written == target
        assert json.loads(target.read_text(encoding="utf-8")) == manifest


def demo():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")
    print(json.dumps(build_manifest(load_config(FIXTURE), now=FROZEN), indent=2))
    print("manifest: all checks passed")


if __name__ == "__main__":
    demo()
