"""CI smoke check: run the synthetic multi-program fixture through the
installed `sas-graph` CLI and assert the documented artifact contract
(dev-plan section 4.2 / wayfinder: lock-multi-program-public-contract.md).

Run from the repo root after `pip install -e .`:

    python .github/scripts/cli_smoke_check.py
"""

import json
import pathlib
import subprocess
import sys

CONFIG = "tests/fixtures/synthetic_multi_program/project.yaml"
FIXTURE_DIR = pathlib.Path("tests/fixtures/synthetic_multi_program")
ROOT_ARTIFACTS = ("graph.json", "graph.mmd", "findings.md", "manifest.json")
PROGRAM_ARTIFACTS = ("graph.json", "findings.md", "manifest.json")


def fail(message):
    print(f"CLI smoke check FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def main():
    result = subprocess.run(["sas-graph", "run", "--config", CONFIG])
    if result.returncode != 0:
        fail(f"`sas-graph run` exited {result.returncode}, expected 0")

    runs_dir = FIXTURE_DIR / "graph_runs" / "runs"
    run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    if not run_dirs:
        fail("no run directory produced under graph_runs/runs")
    run_dir = run_dirs[-1]

    for name in ROOT_ARTIFACTS:
        if not (run_dir / name).exists():
            fail(f"missing root artifact {name}")

    graph = json.loads((run_dir / "graph.json").read_text(encoding="utf-8"))
    if graph.get("schema_version") != "0.2.0":
        fail(f"schema_version {graph.get('schema_version')!r}, expected '0.2.0'")

    program_ids = sorted(n["id"] for n in graph["nodes"] if n["type"] == "Program")
    if len(program_ids) != 2:
        fail(f"expected 2 declared Program nodes, found {len(program_ids)}")
    if program_ids != sorted(program_ids):
        fail("Program node ids are not declaration-index-ordered")

    program_dirs = sorted(p for p in (run_dir / "programs").iterdir() if p.is_dir())
    if len(program_dirs) != 2:
        fail(f"expected 2 per-program debug directories, found {len(program_dirs)}")
    for program_dir in program_dirs:
        for name in PROGRAM_ARTIFACTS:
            if not (program_dir / name).exists():
                fail(f"missing {name} in debug directory {program_dir.name}")

    print("CLI smoke check passed:")
    print(f"  run_dir: {run_dir}")
    print(f"  schema_version: {graph['schema_version']}")
    print(f"  programs: {program_ids}")


if __name__ == "__main__":
    main()
