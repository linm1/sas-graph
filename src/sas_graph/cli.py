"""Command line entry point (dev plan section 20).

Phase 0 shipped the render-only commands; Phase 1 added `validate`; Phase 4
adds `run`, section 4.2's own generation order: parse, build graph, save,
reload, render. The reload is not a formality -- renderers are only ever
tested against a loaded dict, so `run` must go through the same path or it
would be exercising code the renderers were never proven against.

Exit codes: 0 success, 1 validation/run FAILED, 2 argparse usage error.
"""

import argparse
import dataclasses
import shutil
import sys
from pathlib import Path

from .config import load_config
from .graph_io import load_graph, save_graph
from .manifest import build_manifest, create_run_dir, save_manifest
from .renderer_findings import render as render_findings
from .renderer_mermaid import render as render_mermaid
from .run_pipeline import run as run_pipeline


def _render(graph_path, out_path, renderer):
    text = renderer(load_graph(graph_path))

    if out_path is None:
        sys.stdout.write(text)
        return 0

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    return 0


def _write_manifest(result):
    """Record the validation attempt under `output_dir/runs/<run_id>/`.

    Section 4's layout, minus the three artifacts the parser owns. A FAILED
    validate has no graph, so this manifest is the only record it happened —
    which is exactly why it is written before the exit code is decided.

    Skipped when `output_dir` did not survive validation, since there is then no
    declared place to write.
    """
    if result.output_dir is None:
        return None

    run_id, run_dir = create_run_dir(result.output_dir)
    try:
        manifest = build_manifest(result, run_id=run_id)
        return save_manifest(manifest, run_dir / "manifest.json")
    except Exception:
        _discard_run_dir(run_dir, result.output_dir)
        raise


_STATUS_RANK = {"COMPLETE": 0, "PARTIAL": 1, "FAILED": 2}


def _worst_status(statuses):
    return max(statuses, key=lambda status: _STATUS_RANK[status])


def _write_program_debug_artifact(config_result, index, program, run_id, run_dir):
    """One independent re-parse of a single declared program (ticket 02).

    Filtering the merged graph can't isolate one program's slice -- shared
    dataset nodes carry no single owning file to filter by -- so this reuses
    `run_pipeline.run` with `main_programs` narrowed to just this program.
    No mermaid: it adds render cost without adding debugging value beyond the
    merged diagram plus this findings.md. Own manifest per program (a forced
    choice per the ticket, not left to `build_manifest`'s defaults).

    The directory is keyed by index, not bare `program.name`: two declared
    programs can share a basename from different directories (config.py has
    no duplicate-basename check), and `program.name` alone would collide,
    crashing `mkdir` and discarding the whole run.
    """
    narrowed = dataclasses.replace(config_result, main_programs=(program,))
    program_dir = run_dir / "programs" / f"{index:03d}_{program.name}"
    program_dir.mkdir(parents=True)

    # Fresh snapshots dict, not the merged run's shared one: this parse only
    # touches the files this one program's parse actually reads, so its
    # manifest's sources describe only that program -- consistent with
    # main_programs already describing only this one program, not all N.
    # ponytail: a source edited between the merged parse and this debug parse
    # won't raise SourceChangedDuringRunError since the two dicts never
    # compare against each other; widen to a shared dict if that matters.
    program_snapshots = {}
    graph = run_pipeline(narrowed, run_id, program_snapshots)
    graph_path = save_graph(graph, program_dir / "graph.json")
    loaded = load_graph(graph_path)

    (program_dir / "findings.md").write_text(render_findings(loaded), encoding="utf-8")

    manifest = build_manifest(
        narrowed, run_id=run_id, run_status=loaded["run_status"],
        sources=program_snapshots, findings=loaded["findings"],
    )
    save_manifest(manifest, program_dir / "manifest.json")
    return loaded["run_status"]


def _discard_run_dir(run_dir, output_dir):
    """Remove only the exact reserved child after a failed publication."""
    run_dir = Path(run_dir).resolve()
    runs_dir = Path(output_dir, "runs").resolve()
    if run_dir.parent != runs_dir:
        raise ValueError(f"refusing to remove unreserved run directory: {run_dir}")
    shutil.rmtree(run_dir)


def _validate(config_path):
    """Check config and safety only. No parsing, so no graph is written."""
    snapshots = {}
    result = load_config(config_path, snapshots)
    written = _write_manifest(result)

    if result.ok:
        print(f"config {result.status}: {result.config_path}")
        for main_program in result.main_programs:
            print(f"  main_program: {main_program}")
        print(f"  setup_file:   {result.setup_file}")
        for root in result.allowed_roots:
            print(f"  allowed_root: {root}")
        if written:
            print(f"  manifest:     {written}")

    if result.findings:
        # Reuse the section-18 renderer so validate reads exactly like the
        # findings.md a real run would produce.
        sys.stderr.write(
            render_findings(
                {"run_status": result.status, "nodes": [], "findings": result.findings}
            )
        )

    return 0 if result.ok else 1


def _run(config_path):
    """Parse and emit graph.json/graph.mmd/findings.md/manifest.json.

    Section 4.2's order, verbatim: parse+build, save, reload, render from the
    reload. A config FAILED never reaches the parser -- section 9 steps 1-3
    gate everything after them, same posture `validate` already has.
    """
    snapshots = {}
    result = load_config(config_path, snapshots)
    if not result.ok:
        if result.findings:
            sys.stderr.write(
                render_findings(
                    {"run_status": result.status, "nodes": [], "findings": result.findings}
                )
            )
        _write_manifest(result)
        return 1

    run_id, run_dir = create_run_dir(result.output_dir)
    try:
        graph = run_pipeline(result, run_id, snapshots)
        graph_path = save_graph(graph, run_dir / "graph.json")
        loaded = load_graph(graph_path)

        (run_dir / "graph.mmd").write_text(render_mermaid(loaded), encoding="utf-8")
        (run_dir / "findings.md").write_text(render_findings(loaded), encoding="utf-8")

        manifest = build_manifest(
            result, run_id=run_id, run_status=loaded["run_status"],
            sources=snapshots, findings=loaded["findings"],
        )
        save_manifest(manifest, run_dir / "manifest.json")

        program_statuses = [
            _write_program_debug_artifact(result, index, program, run_id, run_dir)
            for index, program in enumerate(result.main_programs)
        ]
    except Exception:
        _discard_run_dir(run_dir, result.output_dir)
        raise

    # Informational only -- the exit code below stays governed by the merged
    # graph's own status, never by a per-program debug parse alone.
    overall_status = _worst_status([loaded["run_status"], *program_statuses])
    print(f"run {loaded['run_status']}: {run_dir}")
    if overall_status != loaded["run_status"]:
        print(f"  overall (incl. per-program debug): {overall_status}")
    return 0 if loaded["run_status"] != "FAILED" else 1


def build_parser():
    parser = argparse.ArgumentParser(prog="sas-graph")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("run", "validate"):
        pending = sub.add_parser(name)
        pending.add_argument("--config", required=True)

    mermaid = sub.add_parser("render-mermaid")
    mermaid.add_argument("--graph", required=True)
    mermaid.add_argument("--out")

    findings = sub.add_parser("render-findings")
    findings.add_argument("--graph", required=True)
    findings.add_argument("--out")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "validate":
        return _validate(args.config)

    if args.command == "run":
        return _run(args.config)

    if args.command == "render-mermaid":
        return _render(args.graph, args.out, render_mermaid)

    return _render(args.graph, args.out, render_findings)


if __name__ == "__main__":
    raise SystemExit(main())
