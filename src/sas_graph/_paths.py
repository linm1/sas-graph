"""Section 2's path-safety boundary, shared by `config.py` and `macro_state.py`.

Split out so the `%include` safety check in `macro_state.py` (section 11.2)
applies exactly the same prohibited-artifact and containment rules as
`config.py` applies to `project.yaml`'s declared paths, instead of a second
copy that could silently drift.
"""

from pathlib import Path

# Extensions and path segments only -- never open the file to decide, that
# would be reading the artifact this list exists to refuse.
PROHIBITED_SUFFIXES = {
    ".sas7bdat",  # dataset
    ".sas7bndx",  # dataset index
    ".sas7bcat",  # format catalog
    ".xpt",  # transport dataset
    ".log",  # SAS log
    ".lst",  # SAS listing output
}

# Matched against whole path segments, case-folded. A substring test would fail
# a run for `reproduce/` because it contains "prod".
PROHIBITED_SEGMENTS = {
    "production",
    "prod",
    "logs",
    "outputs",
    "mprint",
}


def prohibited_reason(path):
    """Return why section 2 forbids this path, or None."""
    path = Path(path)
    if path.suffix.lower() in PROHIBITED_SUFFIXES:
        return f"file extension `{path.suffix}`"
    for part in path.parts:
        if part.casefold() in PROHIBITED_SEGMENTS:
            return f"path segment `{part}`"
    return None


def _is_relative_to(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def is_inside(path, roots):
    """True when `path` is contained by any allowed root.

    Both sides must already be resolved (`..` and symlinks gone) --
    `is_relative_to` compares whole path components, which is what makes
    `/study/adae-evil` correctly fail against `/study/adae`; a `startswith`
    check would accept it.
    """
    return any(path == root or _is_relative_to(path, root) for root in roots)
