"""Capture source bytes at the same read that feeds parsing/indexing."""

import hashlib
import io
from pathlib import Path


class SourceChangedDuringRunError(RuntimeError):
    """A source path yielded more than one byte version in one run."""


def read_bytes(path, snapshots=None):
    """Read one source and retain its exact byte identity when requested."""
    path = Path(path)
    data = path.read_bytes()
    if snapshots is not None:
        path = path.resolve()
        snapshot = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        previous = snapshots.get(path)
        if previous is not None and previous != snapshot:
            raise SourceChangedDuringRunError(
                f"source changed during run: {path}"
            )
        snapshots[path] = snapshot
    return data


def read_text(path, encoding, snapshots=None):
    """Decode bytes returned by ``read_bytes`` without changing codec behavior."""
    return io.TextIOWrapper(io.BytesIO(read_bytes(path, snapshots)), encoding=encoding).read()
