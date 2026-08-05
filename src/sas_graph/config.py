"""Load and validate `project.yaml` (dev plan sections 8, 9 steps 1-3, 21 Phase 1).

This is the first code that touches user-supplied paths, so the safety rules in
section 2 stop being theoretical here. Two properties matter:

- **Containment is checked before any read.** A path that is not inside
  `allowed_roots` is never opened, even to find out whether it exists.
- **Every problem is reported, not just the first.** `validate` should tell a
  programmer everything wrong with their config in one pass, so validation
  returns findings rather than raising on the first failure.

Section 5.3 makes every config problem FAILED, not PARTIAL: a graph built on
missing or unsafe context cannot be trusted. Findings use `BLOCKED` from
section 6 — "a required graph part cannot be trusted without resolution" —
so `renderer_findings` can print them without a new section.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ._paths import PROHIBITED_SEGMENTS, PROHIBITED_SUFFIXES
from ._paths import is_inside as _is_inside
from ._paths import prohibited_reason as _prohibited_reason
from .source_snapshot import read_bytes, read_text

PATH_LIST_KEYS = ("allowed_roots", "macro_roots", "macro_contracts")


@dataclass(frozen=True)
class ConfigResult:
    """Outcome of validating one `project.yaml`.

    `status` is a run-level status from section 5. On FAILED the path fields may
    be `None`; callers must check `status` before using them.
    """

    status: str
    findings: list = field(default_factory=list)
    config_path: Path = None
    main_program: Path = None
    setup_file: Path = None
    allowed_roots: tuple = ()
    macro_roots: tuple = ()
    macro_contracts: tuple = ()
    output_dir: Path = None
    source_snapshots: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.status != "FAILED"


class _Findings:
    """Accumulates findings against one config file.

    `BLOCKED` is the default because section 5.3 makes config problems FAILED.
    `NOT_EXECUTED` is for a declared-but-absent optional root: section 5.3 does
    not list it as a failure, but staying silent would leave a later phase's
    empty macro index unexplained.
    """

    def __init__(self, config_path):
        self._config_name = Path(config_path).name
        self.items = []

    def add(self, obj, rule, message, action, status="BLOCKED"):
        self.items.append(
            {
                "id": f"finding:config:{len(self.items) + 1:03d}",
                "status": status,
                "type": rule,
                "severity": "ERROR" if status == "BLOCKED" else "WARNING",
                "object": obj,
                "message": message,
                "suggested_action": action,
                "affected_nodes": [],
                "affected_edges": [],
                # Section 7. A config finding has no line range: the problem is
                # the value, and YAML round-tripping line numbers is not worth
                # a parser. The rule names which check rejected it.
                "source": {
                    "file": self._config_name,
                    "line_start": None,
                    "line_end": None,
                    "statement_order": None,
                    "original_text": None,
                    "rule": rule,
                },
            }
        )


def _resolve(value, base):
    """Resolve a declared path against the config file's directory.

    Section 8's example uses absolute paths; a committed fixture cannot. Relative
    values are read as relative to `project.yaml`, never to the current working
    directory, so a config means the same thing from any shell.
    """
    return Path(base, str(value)).resolve()


def _as_single(value):
    """Normalise a scalar-or-list config value to a list.

    `setup_file` is a scalar in section 8, but the "more than one setup file"
    failure in 5.3 is only reachable if a list can be written, so accept both
    and let the caller reject the wrong length.

    ponytail: two `setup_file:` keys in one document are invisible here —
    `yaml.safe_load` silently keeps the last. Catching that needs a duplicate-key
    loader; add one if a real config ever hits it.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _check_required_file(name, raw, base, roots, findings, source_snapshots):
    """Validate one required source file. Returns the resolved path or None.

    Order matters: containment is proven before the file is touched.
    """
    if raw in (None, ""):
        findings.add(
            name,
            "config_missing_key",
            f"`{name}` is not declared in the config.",
            f"Add `{name}:` to project.yaml. Section 8 requires it; the agent "
            "must not guess project context.",
        )
        return None

    path = _resolve(raw, base)

    reason = _prohibited_reason(path)
    if reason is not None:
        findings.add(
            name,
            "config_prohibited_artifact",
            f"`{name}` points at a prohibited artifact ({reason}): {path}.",
            "Section 2 forbids datasets, logs, MPRINT output and production "
            "artifacts. Point this at independent SAS source.",
        )
        return None

    if not _is_inside(path, roots):
        findings.add(
            name,
            "config_outside_allowed_roots",
            f"`{name}` resolves outside allowed_roots: {path}.",
            "Add the containing directory to `allowed_roots`, or move the file "
            "inside an existing root.",
        )
        return None

    if not path.exists():
        findings.add(
            name,
            "config_file_missing",
            f"`{name}` does not exist: {path}.",
            "Check the path spelling, or the directory the config is resolved "
            "against (relative paths resolve against project.yaml).",
        )
        return None

    try:
        read_bytes(path, source_snapshots)
    except OSError as exc:
        findings.add(
            name,
            "config_file_unreadable",
            f"`{name}` cannot be read: {path} ({exc.strerror or exc}).",
            "Check file permissions and that the path is a file, not a directory.",
        )
        return None

    return path


def load_config(config_path, source_snapshots=None):
    """Validate `project.yaml` and return a `ConfigResult`.

    Never raises for a bad config — a malformed file is a finding, because the
    caller wants to print all of them. Only programmer errors propagate.
    """
    config_path = Path(config_path)
    source_snapshots = source_snapshots if source_snapshots is not None else {}
    findings = _Findings(config_path)
    base = config_path.parent.resolve()

    try:
        text = read_text(config_path, "utf-8", source_snapshots)
    except OSError as exc:
        findings.add(
            str(config_path),
            "config_file_unreadable",
            f"config file cannot be read: {config_path} ({exc.strerror or exc}).",
            "Check the --config path.",
        )
        return ConfigResult("FAILED", findings.items, config_path, source_snapshots=source_snapshots)

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        findings.add(
            str(config_path),
            "config_malformed_yaml",
            f"config file is not valid YAML: {exc.__class__.__name__}.",
            "Fix the YAML syntax. See section 8 of the dev plan for the "
            "expected shape.",
        )
        return ConfigResult("FAILED", findings.items, config_path, source_snapshots=source_snapshots)

    if not isinstance(raw, dict):
        findings.add(
            str(config_path),
            "config_malformed_yaml",
            "config file must be a YAML mapping of keys to values.",
            "See section 8 of the dev plan for the expected shape.",
        )
        return ConfigResult("FAILED", findings.items, config_path, source_snapshots=source_snapshots)

    roots = tuple(_resolve(r, base) for r in _as_single(raw.get("allowed_roots")))
    if not roots:
        findings.add(
            "allowed_roots",
            "config_missing_key",
            "`allowed_roots` is not declared; every file would be outside it.",
            "Declare at least one root directory. Section 8 forbids a "
            "whole-study folder search by default.",
        )

    main_program = _check_required_file(
        "main_program", raw.get("main_program"), base, roots, findings, source_snapshots
    )

    setup_declared = _as_single(raw.get("setup_file"))
    setup_file = None
    if len(setup_declared) > 1:
        findings.add(
            "setup_file",
            "config_multiple_setup_files",
            f"exactly one setup file is required, {len(setup_declared)} declared.",
            "Section 8 allows one setup.sas per task. Merge the bootstrap into "
            "a single file, or split the task.",
        )
    else:
        setup_file = _check_required_file(
            "setup_file",
            setup_declared[0] if setup_declared else None,
            base,
            roots,
            findings,
            source_snapshots,
        )

    optional = {}
    for key in PATH_LIST_KEYS[1:]:
        resolved = []
        for value in _as_single(raw.get(key)):
            path = _resolve(value, base)
            reason = _prohibited_reason(path)
            if reason is not None:
                findings.add(
                    key,
                    "config_prohibited_artifact",
                    f"`{key}` includes a prohibited artifact ({reason}): {path}.",
                    "Section 2 forbids production artifacts. Remove the entry.",
                )
                continue
            if not _is_inside(path, roots):
                findings.add(
                    key,
                    "config_outside_allowed_roots",
                    f"`{key}` entry resolves outside allowed_roots: {path}.",
                    "Add it to `allowed_roots`, or drop the entry.",
                )
                continue
            if not path.is_dir():
                # Not FAILED — section 5.3 does not list it, and a missing macro
                # source is explicitly a PARTIAL condition (5.2). But silence
                # here would leave Phase 3's empty macro index unexplained, and
                # section 26 wants the unknown exposed, not absorbed.
                findings.add(
                    key,
                    "config_declared_root_missing",
                    f"`{key}` declares a directory that does not exist: {path}. "
                    "Nothing will be indexed from it.",
                    "Create the directory, correct the path, or remove the entry "
                    "so the config states what is actually there.",
                    status="NOT_EXECUTED",
                )
                continue
            resolved.append(path)
        optional[key] = tuple(resolved)

    # `output_dir` is a write target, not a source the parser reads, so it is
    # deliberately not held to `allowed_roots` — that list is the read boundary.
    # It is still refused if it points into a production or log location.
    output_dir = None
    if raw.get("output_dir") in (None, ""):
        findings.add(
            "output_dir",
            "config_missing_key",
            "`output_dir` is not declared; there is nowhere to write the run.",
            "Add `output_dir:` to project.yaml.",
        )
    else:
        output_dir = _resolve(raw["output_dir"], base)
        reason = _prohibited_reason(output_dir)
        if reason is not None:
            findings.add(
                "output_dir",
                "config_prohibited_artifact",
                f"`output_dir` points into a prohibited location ({reason}): "
                f"{output_dir}.",
                "Write runs to a directory outside production and log trees.",
            )
            output_dir = None

    # Section 5: a BLOCKED finding fails the run; anything softer leaves the
    # config usable but incomplete.
    if any(f["status"] == "BLOCKED" for f in findings.items):
        status = "FAILED"
    elif findings.items:
        status = "PARTIAL"
    else:
        status = "COMPLETE"
    return ConfigResult(
        status=status,
        findings=findings.items,
        config_path=config_path,
        main_program=main_program,
        setup_file=setup_file,
        allowed_roots=roots,
        macro_roots=optional.get("macro_roots", ()),
        macro_contracts=optional.get("macro_contracts", ()),
        output_dir=output_dir,
        source_snapshots=source_snapshots,
    )
