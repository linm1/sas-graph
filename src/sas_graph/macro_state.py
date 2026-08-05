"""Macro variable state and `%include` expansion (dev plan sections 11.2, 11.3,
11.7, 12.8, 14.7, 21 Phase 3).

Section 11.3, verbatim: "Do not use a final global macro table for the whole
program." `resolve_text` always takes the caller's `statement_order` and
resolves against `%let` events strictly before it -- there is no mutated dict
handed out that would let two calls at different points in the file see the
same binding.

`%include` is expanded before macro resolution runs, matching dev plan section
9's own ordering (steps 5-6 before 7; step 12 before 13). An include path that
itself needs macro-variable resolution is therefore not supported in v0: it is
reported and left in place, not guessed.
ponytail: an interleaved expand/resolve walker would cover it; add if a real
config needs a macro variable inside an `%include` path.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from ._paths import is_inside as _is_inside
from ._paths import prohibited_reason as _prohibited_reason
from .statements import Comment, SplitResult, split_statements
from .source_snapshot import read_text

_LET_RE = re.compile(
    r"^%let\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?);?$", re.IGNORECASE
)
_PUT_RE = re.compile(r"^%put\b", re.IGNORECASE)
_SYMPUTX_RE = re.compile(r"call\s+symputx\s*\(", re.IGNORECASE)
_SQL_INTO_RE = re.compile(r"\binto\s*:\s*[A-Za-z_]", re.IGNORECASE)
# Trailing `/ options` (e.g. `/SOURCE2`) is valid SAS; matched and discarded.
_INCLUDE_RE = re.compile(
    r'^%include\s+["\']([^"\']+)["\'](?:\s*/\s*\S.*)?\s*;$', re.IGNORECASE
)

# A `&name` reference, with an optional single `.` terminator consumed (not
# kept): section 15.6, `work.&domain._pre` -> `work.ae_pre`. A reference
# preceded by another `&` (`&&x`) is excluded deliberately -- section 3.2 only
# promises simple substitution, not the general macro-quoting functions -- and
# needs a lookbehind, not just a lookahead: `re.sub` retries at the *next*
# position, so `(?!&)` alone still matches the second `&` of `&&x` as a clean
# one-`&` reference to `x`.
_REF_RE = re.compile(r"(?<!&)&(?!&)([A-Za-z_][A-Za-z0-9_]*)\.?")


@dataclass(frozen=True)
class LetEvent:
    """One resolved `%let name = value;` binding, timestamped by statement_order.

    `value` is resolved against events strictly before `statement_order` at
    the point this event is created, so a chained `%let a = &a.b;` only ever
    needs one linear scan to resolve, here or at any later use site.
    """

    name: str
    value: str
    statement_order: int
    source: dict = None


def resolve_text(text, statement_order, events, return_unresolved=False):
    """Substitute `&name.` / `&name` references using state as of `statement_order`.

    Only events with `event.statement_order < statement_order` are visible.
    The strict inequality stops a `%let`'s own right-hand side from resolving
    against itself, and guarantees a use site in the *same* statement_order as
    a `%let` never sees that `%let`'s own value.
    """
    visible = {}
    for event in events:
        if event.statement_order < statement_order:
            visible[event.name.lower()] = event.value

    unresolved = []

    def substitute(match):
        name = match.group(1)
        if name.lower() not in visible:
            unresolved.append(name)
            return match.group(0)
        return visible[name.lower()]

    resolved = _REF_RE.sub(substitute, text)
    if return_unresolved:
        return resolved, unresolved
    return resolved


def _runtime_creation_finding(kind, statement, message):
    return {
        "id": f"finding:macro_state:{statement.statement_order:04d}:{kind}",
        # Sections 12.8 / 14.7 name an expected, documented pattern, not a
        # problem to fix -- NOT_EXECUTED (section 6), not an error severity.
        "status": "NOT_EXECUTED",
        "type": "runtime_macro_variable_creation",
        "severity": "INFORMATION",
        "object": statement.file,
        "message": message,
        "suggested_action": (
            "This value is created at SAS runtime and cannot be resolved "
            "statically. It is not used to resolve later macro references."
        ),
        "affected_nodes": [],
        "affected_edges": [],
        "source": statement.as_source(kind),
    }


def walk_let_statements(statements, findings_only=False, initial_events=()):
    """Walk `statements` in order, collecting `%let` bindings (section 11.3).

    `%put` is ignored entirely (section 11.7): no binding, no finding, not even
    for an undefined reference. `CALL SYMPUTX` and `PROC SQL INTO:` are
    recorded as evidence only (12.8, 14.7): no binding is created, and a prior
    `%let` for a different name keeps resolving normally afterward.

    Returns the `LetEvent` list, or the findings list when `findings_only`.
    """
    events = list(initial_events)
    local_events = []
    findings = []

    for statement in statements:
        text = statement.text

        if _PUT_RE.match(text):
            continue

        match = _LET_RE.match(text)
        if match:
            name, raw_value = match.group(1), match.group(2)
            value = resolve_text(raw_value, statement.statement_order, events)
            event = LetEvent(
                name, value, statement.statement_order, statement.as_source("let_statement")
            )
            events.append(event)
            local_events.append(event)
            continue

        if _SYMPUTX_RE.search(text):
            findings.append(
                _runtime_creation_finding(
                    "call_symputx",
                    statement,
                    "`CALL SYMPUTX` creates a macro variable at SAS runtime.",
                )
            )
            continue

        if _SQL_INTO_RE.search(text):
            findings.append(
                _runtime_creation_finding(
                    "proc_sql_into",
                    statement,
                    "`PROC SQL ... INTO :` creates a macro variable at SAS runtime.",
                )
            )
            continue

    return findings if findings_only else local_events


def _include_finding(kind, statement, message, raw_path):
    """Build a finding tagged to `statement`, deferring `source`/`id` to
    `_renumber_pieces`: at this point `statement.statement_order` is still the
    pre-splice, per-file number, and section 7 requires the finding to point
    at the statement's *final* spliced position, not its local one.
    """
    return {
        "_pending_statement": statement,
        "_pending_rule": kind,
        # Section 5.3 does not list a rejected %include among the FAILED
        # conditions, and the statements around it are still true, so this is
        # NOT_EXECUTED (section 6): the include did not run, nothing else broke.
        "status": "NOT_EXECUTED",
        "type": kind,
        "severity": "WARNING",
        "object": statement.file,
        "message": message,
        "suggested_action": (
            f'Fix `%include "{raw_path}"`, or add its directory to allowed_roots.'
        ),
        "affected_nodes": [],
        "affected_edges": [],
    }


@dataclass(frozen=True)
class _Piece:
    """One statement or comment, tagged with where it falls in the final flow.

    `expand_includes` builds a flat, correctly-interleaved list of these while
    walking (and recursing into) the statement stream, then a single final
    pass in `_renumber_pieces` assigns gapless `statement_order` /
    `after_statement_order` from position alone.
    """

    kind: str  # "statement" or "comment"
    value: object


def _interleave(result):
    """Flatten one `SplitResult` back into position-ordered `_Piece`s.

    `result.statements` and `result.comments` are two separate lists; a
    comment's place in the flow is only recorded by
    `after_statement_order`, so re-merging by that key is required to keep a
    mid-file comment between the two statements it actually sits between --
    appending `result.comments` wholesale after `result.statements` would
    silently push every comment to the end.
    """
    by_slot = {}
    for comment in result.comments:
        by_slot.setdefault(comment.after_statement_order, []).append(comment)

    pieces = [_Piece("comment", c) for c in by_slot.get(0, [])]
    for statement in result.statements:
        pieces.append(_Piece("statement", statement))
        for comment in by_slot.get(statement.statement_order, []):
            pieces.append(_Piece("comment", comment))
    return pieces


def expand_includes(
    statements, comments, allowed_roots, base_dir=".", findings=(), _visited=None,
    source_paths=None,
):
    """Splice `%include "path";` statements in place (section 11.2).

    `base_dir` is the directory a *relative* `%include` path resolves against
    -- the directory the current file was actually read from. `Statement.file`
    is a display name for section 7 traceability, not a real filesystem path
    (a fixture may pass a bare name with no directory), so it is never used to
    derive a base; the caller states it explicitly, the same way `config.py`
    resolves `main_program` against `project.yaml`'s directory rather than
    guessing from the declared string.

    Renumbers `statement_order` across the whole result so the single linear
    macro-state walk in `walk_let_statements` sees one gapless spine whether or
    not any `%include` fired. A spliced statement keeps its own `file` (the
    included file's name) and its own line range -- only `statement_order` is
    rewritten, so a finding from inside the child still points at the child's
    real source line. Findings (both a child's own parse-error findings from
    `split_statements`, and this function's own include-rejection findings)
    are threaded through the same `_Piece` list so `_renumber_pieces` can give
    them the statement's *final* spliced `statement_order` too -- building
    their `source` block any earlier would freeze in the pre-splice number.

    An include outside `allowed_roots`, a prohibited artifact, a missing or
    unreadable file, or a cycle is reported and the `%include` statement is
    left unexpanded -- never BLOCKED. Section 5.3 does not list any of these
    as a FAILED condition, and the surrounding statements are still true.

    `findings` are the top-level file's *own* parse-error findings from
    `split_statements` (e.g. an unterminated comment), not a child's -- those
    already flow through `_build_pieces`. Like a child's own scanner findings,
    the scanner aborts the scan right after stamping one, so it belongs after
    every statement the top-level file did produce, not before -- the same
    `_pending_after` placement `_build_pieces` uses for a child's findings.
    """
    roots = [Path(r).resolve() for r in allowed_roots]
    pieces = _build_pieces(
        statements, comments, roots, Path(base_dir), _visited or frozenset(), source_paths
    )
    for finding in findings:
        finding = dict(finding)
        finding["_pending_after"] = True
        finding["_pending_rule"] = finding.get("source", {}).get("rule")
        pieces.append(_Piece("finding", finding))
    return _renumber_pieces(pieces)


def _build_pieces(statements, comments, roots, base_dir, visited, source_paths):
    """Recursive worker behind `expand_includes`.

    Returns a flat, un-renumbered `_Piece` list -- every finding still carries
    its `_pending_statement` marker rather than a finished `source`/`id`, at
    every recursion depth. `_renumber_pieces` is called exactly once, by the
    public `expand_includes`, after the *whole* tree has been flattened: if a
    nested call finalized its own findings' `source.statement_order` before
    returning, that number would be local to the nested splice, not the
    outermost one, and would need a second remap on the way back up. Deferring
    finalization to a single top-level pass avoids that entirely.
    """
    pieces = []
    for piece in _interleave(SplitResult(statements=statements, comments=comments)):
        if piece.kind != "statement":
            pieces.append(piece)
            continue

        statement = piece.value
        include_match = _INCLUDE_RE.match(statement.text)
        if include_match is None:
            pieces.append(piece)
            continue

        raw_path = include_match.group(1)
        candidate = Path(raw_path)
        resolved = (
            candidate if candidate.is_absolute() else base_dir / candidate
        ).resolve()

        problem = None
        if resolved in visited:
            problem = (
                "include_cycle_detected",
                f'`%include "{raw_path}"` creates a cycle back to a file '
                "already being expanded.",
            )
        elif (reason := _prohibited_reason(resolved)) is not None:
            problem = (
                "include_prohibited_artifact",
                f'`%include "{raw_path}"` points at a prohibited artifact '
                f"({reason}).",
            )
        elif not _is_inside(resolved, roots):
            problem = (
                "include_outside_allowed_roots",
                f'`%include "{raw_path}"` resolves outside allowed_roots: '
                f"{resolved}.",
            )
        elif not resolved.exists():
            problem = (
                "include_file_missing",
                f'`%include "{raw_path}"` does not exist: {resolved}.',
            )

        if problem is None:
            try:
                child_text = read_text(resolved, "utf-8-sig", source_paths)
            except OSError as exc:
                problem = (
                    "include_file_unreadable",
                    f'`%include "{raw_path}"` cannot be read: {resolved} '
                    f"({exc.strerror or exc}).",
                )

        if problem is not None:
            kind, message = problem
            pieces.append(
                _Piece(
                    "finding",
                    _include_finding(kind, statement, message, raw_path),
                )
            )
            pieces.append(piece)
            continue

        child_split = split_statements(child_text, resolved.name)
        pieces.extend(
            _build_pieces(
                child_split.statements,
                child_split.comments,
                roots,
                resolved.parent,
                visited | {resolved},
                source_paths,
            )
        )
        # `child_split.findings` (e.g. an unterminated comment/string) is the
        # scanner's own evidence and must not be lost just because this file
        # also has an %include in it. `_Scanner._finding()` stamps
        # `source["statement_order"] = len(self.statements) + 1` -- a forward
        # pointer to a statement number that is never reached, because every
        # call site aborts the scan right after (statements.py's
        # `_skip_block_comment` / `_skip_star_comment` / `_skip_string`). So
        # there is no child statement to key a `_pending_statement` lookup
        # against; the only correct position is "immediately after every
        # statement this child did produce," which is exactly where these
        # findings are placed here -- after the child's own `_build_pieces`
        # result, not before it as an earlier revision did (which left them
        # permanently un-renumbered, since no statement ever matched the
        # lookup key).
        for finding in child_split.findings:
            finding = dict(finding)
            finding["_pending_after"] = True
            finding["_pending_rule"] = finding.get("source", {}).get("rule")
            pieces.append(_Piece("finding", finding))

    return pieces


def _renumber_pieces(pieces):
    """Assign final gapless order purely from position in `pieces`.

    A comment's new `after_statement_order` is just "however many statements
    have been emitted so far" -- correct because `_interleave` (used both for
    the top-level input and for every spliced child) guarantees a comment
    always sits immediately next to the piece list position it belongs at.

    A finding built by `_include_finding` carries a `_pending_statement`
    instead of a `source`: it is rendered here, once, against the *already
    renumbered* statement (the very next piece), so `source["statement_order"]`
    reports the statement's final spliced position rather than its pre-splice
    local one.

    A child's own scanner finding (an unterminated comment/string) carries
    `_pending_after` instead: `_Scanner._finding()` stamps a statement number
    that is never reached (the scan aborts right after), so there is no
    statement piece to key off. Its final position is just `emitted` as of
    where it sits in `pieces` -- placed right after the child's own spliced
    statements by `_build_pieces`, so no `+1` here. Only `statement_order` and
    `id` are rewritten; `file`/`line_start`/`line_end`/`original_text` already
    point at the real offset inside the child file and must survive untouched.

    A finding with neither marker (`walk_let_statements` findings are never
    routed through this function) passes through as-is.
    """
    new_statements = []
    new_comments = []
    new_findings = []
    emitted = 0
    # A tiebreaker for `_pending_after` findings, which key off `emitted`
    # alone: two sibling includes that each break before producing a single
    # statement share the same `emitted` value (e.g. both 0), which would
    # otherwise give them the identical id despite being distinct findings
    # from distinct files.
    finding_index = 0

    for piece in pieces:
        if piece.kind == "statement":
            statement = piece.value
            emitted += 1
            new_statements.append(
                statement.__class__(
                    text=statement.text,
                    original_text=statement.original_text,
                    file=statement.file,
                    line_start=statement.line_start,
                    line_end=statement.line_end,
                    statement_order=emitted,
                    terminated=statement.terminated,
                )
            )
        elif piece.kind == "comment":
            comment = piece.value
            new_comments.append(
                Comment(
                    text=comment.text,
                    original_text=comment.original_text,
                    kind=comment.kind,
                    file=comment.file,
                    line_start=comment.line_start,
                    line_end=comment.line_end,
                    after_statement_order=emitted,
                )
            )
        else:
            finding = dict(piece.value)
            pending = finding.pop("_pending_statement", None)
            pending_after = finding.pop("_pending_after", False)
            rule = finding.pop("_pending_rule", None)
            if pending is not None:
                # `emitted` is this pending statement's own final order: it is
                # always appended to `pieces` immediately after its finding,
                # so the count is correct at this point, before it increments.
                final_order = emitted + 1
                finding_index += 1
                finding["source"] = {
                    "file": pending.file,
                    "line_start": pending.line_start,
                    "line_end": pending.line_end,
                    "statement_order": final_order,
                    "original_text": pending.original_text,
                    "rule": rule,
                }
                finding["id"] = f"finding:include:{final_order:04d}:{finding_index:03d}:{rule}"
            elif pending_after:
                # No statement follows -- the scanner aborted the scan right
                # after stamping this finding, so `emitted` (not `emitted + 1`)
                # is the finding's final position: "right after the last
                # statement this child actually produced." Two sibling
                # includes that each break before any statement share the same
                # `emitted`, so `finding_index` (unique per finding regardless
                # of position) breaks the id tie between them.
                final_order = emitted
                finding_index += 1
                source = dict(finding.get("source") or {})
                source["statement_order"] = final_order
                finding["source"] = source
                finding["id"] = (
                    f"finding:statements:{final_order:04d}:{finding_index:03d}:{rule}"
                )
            new_findings.append(finding)

    return SplitResult(
        statements=new_statements, comments=new_comments, findings=new_findings
    )
