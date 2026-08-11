"""Write `manifest.json` (dev plan sections 4, 4.2 step 7, 21 Phase 1).

Phase 1 ships the skeleton: run identity, config provenance, and source hashes.
The graph-derived counts arrive with the parser, so `run_status` is whatever the
caller knows at write time — the config status until Phase 4 has a graph.

Hashing is why this belongs in Phase 1 rather than Phase 0: a hash needs the
validated file list the config loader produces.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION
from .graph_io import save_graph


def new_run_id(now=None):
    """UTC timestamp, sortable, filesystem-safe.

    `runs/<run_id>/` is section 4's layout, so this has to survive being a
    directory name on Windows: no colons.
    """
    moment = now or datetime.now(timezone.utc)
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"


def create_run_dir(output_dir, now=None):
    """Atomically reserve a fresh run directory without overwriting a prior run."""
    for _ in range(10):
        run_id = new_run_id(now)
        run_dir = Path(output_dir, "runs", run_id)
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return run_id, run_dir
    raise RuntimeError("could not reserve a unique run directory")


def _source_entries(snapshots):
    return [
        {"path": str(path), **snapshot}
        for path, snapshot in sorted(snapshots.items(), key=lambda item: str(item[0]))
    ]


def build_manifest(
    config_result, run_id=None, run_status=None, sources=None, findings=None, now=None
):
    """Assemble the manifest dict for one run.

    `sources` is the read-time byte snapshot collected by the pipeline.
    `findings` is the run graph's own findings list; a config-only call (the
    `validate` command, or a FAILED run that never reached the parser) has no
    graph and so falls back to `config_result.findings` -- the config/safety
    findings are the only ones that exist at that point.
    """
    config = config_result

    if sources is None:
        sources = config.source_snapshots

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or new_run_id(now),
        "run_status": run_status or config.status,
        # Same instant, same suffix as run_id: a consumer parsing both should
        # not meet `Z` in one field and `+00:00` in the other.
        "generated_at": (now or datetime.now(timezone.utc)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "config": {
            "path": str(config.config_path) if config.config_path else None,
            "main_programs": [str(p) for p in config.main_programs],
            "setup_file": str(config.setup_file) if config.setup_file else None,
            "allowed_roots": [str(p) for p in config.allowed_roots],
            "macro_roots": [str(p) for p in config.macro_roots],
            "macro_contracts": [str(p) for p in config.macro_contracts],
            "output_dir": str(config.output_dir) if config.output_dir else None,
        },
        "sources": _source_entries(sources),
        "findings_count": len(findings) if findings is not None else len(config.findings),
    }


def save_manifest(manifest, path):
    """Write manifest.json. Same JSON writer as graph.json — one format, one place."""
    return save_graph(manifest, path)
