"""Load and save `graph.json`.

Section 4.2 of the dev plan requires write -> reload -> render, so renderers
take the reloaded dict and never touch an in-memory graph object.
"""

import json
from pathlib import Path

SUPPORTED_SCHEMA_VERSIONS = {"0.1.0"}


def load_graph(path):
    """Read graph.json and reject a schema version this build cannot render."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"unsupported schema_version {version!r}; "
            f"this build reads {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )

    return data


def save_graph(graph, path):
    """Write graph.json. Canonical artifact, so keep it diff-friendly."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target


def nodes_by_id(graph):
    return {node["id"]: node for node in graph.get("nodes", [])}
