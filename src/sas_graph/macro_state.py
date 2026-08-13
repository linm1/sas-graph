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

# --- walk_runtime: %os_fvars binding + %IF/%symexist branch selection -------

_OS_FVARS_RE = re.compile(r"^%os_fvars\s*\(\s*(.*?)\s*\)\s*;?$", re.IGNORECASE)
_IF_THEN_DO_RE = re.compile(r"^%if\s+(.+?)\s*%then\s+%do\s*;?$", re.IGNORECASE)
_ELSE_DO_RE = re.compile(r"^%else\s+%do\b", re.IGNORECASE)
_DO_OPEN_RE = re.compile(r"^%do\b", re.IGNORECASE)
# Depth counter for `%end;` matching -- `else` counts as an opener too, since
# a nested `%if ... %do; ... %end; %else %do; ... %end;` needs its `%else`
# half to open its own depth level, or the first `%end;` after it would look
# like the outer block's close.
_BLOCK_OPENER_RE = re.compile(r"^%(if|do|else)\b", re.IGNORECASE)
_BLOCK_END_RE = re.compile(r"^%end\b", re.IGNORECASE)
_SYMEXIST_RE = re.compile(r"^%symexist\s*\(\s*([A-Za-z_]\w*)\s*\)$", re.IGNORECASE)
_IN_CONDITION_RE = re.compile(r"^(.+?)\s+in\s*\(\s*(.+?)\s*\)$", re.IGNORECASE)
_COMPARISON_RE = re.compile(
    r"^(?P<lhs>.+?)\s+(?P<op>\^=|~=|!=|=|eq|ne)\s+(?P<rhs>.+)$", re.IGNORECASE
)


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


def _bind_statement(statement, events):
    """One statement's `%let` / `CALL SYMPUTX` / `PROC SQL INTO:` handling.

    Shared by `walk_let_statements` and `walk_runtime` so the two never drift
    on this logic. Returns `(event_or_None, finding_or_None)`; `%put` and any
    statement matching neither pattern return `(None, None)`.
    """
    text = statement.text

    if _PUT_RE.match(text):
        return None, None

    match = _LET_RE.match(text)
    if match:
        name, raw_value = match.group(1), match.group(2)
        value = resolve_text(raw_value.strip(), statement.statement_order, events)
        event = LetEvent(
            name, value, statement.statement_order, statement.as_source("let_statement")
        )
        return event, None

    if _SYMPUTX_RE.search(text):
        return None, _runtime_creation_finding(
            "call_symputx",
            statement,
            "`CALL SYMPUTX` creates a macro variable at SAS runtime.",
        )

    if _SQL_INTO_RE.search(text):
        return None, _runtime_creation_finding(
            "proc_sql_into",
            statement,
            "`PROC SQL ... INTO :` creates a macro variable at SAS runtime.",
        )

    return None, None


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
        event, finding = _bind_statement(statement, events)
        if event is not None:
            events.append(event)
            local_events.append(event)
        if finding is not None:
            findings.append(finding)

    return findings if findings_only else local_events


def _parse_os_fvars_params(raw):
    """`mvar=X, projpath=Y` -> `{"mvar": "X", "projpath": "Y"}`, lower-keyed."""
    params = {}
    for part in raw.split(","):
        key, sep, value = part.partition("=")
        if sep:
            params[key.strip().lower()] = value.strip()
    return params


def _os_fvars_finding(statement, message):
    return {
        "id": f"finding:macro_state:{statement.statement_order:04d}:os_fvars_unbound",
        "status": "NOT_EXECUTED",
        "type": "os_fvars_unbound",
        "severity": "INFORMATION",
        "object": statement.file,
        "message": message,
        "suggested_action": (
            "This value cannot be resolved statically and is not used to "
            "resolve later macro references."
        ),
        "affected_nodes": [],
        "affected_edges": [],
        "source": statement.as_source("os_fvars_unbound"),
    }


def _bind_os_fvars(statement, events, os_fvars_base):
    """`%os_fvars(mvar=X, projpath=P)` -> a `LetEvent` for `X` (map decision 4).

    Composition, in order: resolve `projpath` against `events`; `:` -> `/`;
    concatenate onto `os_fvars_base` (a declared prefix, not path-joined --
    the declared base already ends in `/`); collapse to exactly one trailing
    `/`. `os_fvars_base=None` means `%os_fvars` binds nothing anywhere, with
    no finding either -- identical to today's behavior (spec decision 9).
    """
    match = _OS_FVARS_RE.match(statement.text)
    if match is None:
        return None, None
    if os_fvars_base is None:
        return None, None

    params = _parse_os_fvars_params(match.group(1))
    missing = [key for key in ("mvar", "projpath") if not params.get(key)]
    if missing:
        names = ", ".join(f"`{key}=`" for key in missing)
        return None, _os_fvars_finding(
            statement, f"`%os_fvars` call is missing {names} and cannot be bound."
        )

    resolved, unresolved = resolve_text(
        params["projpath"], statement.statement_order, events, return_unresolved=True
    )
    if unresolved:
        return None, _os_fvars_finding(
            statement,
            "`%os_fvars` projpath references unresolved macro variable(s): "
            + ", ".join(unresolved) + ".",
        )

    value = (os_fvars_base + resolved.replace(":", "/")).rstrip("/") + "/"
    event = LetEvent(
        params["mvar"], value, statement.statement_order,
        statement.as_source("os_fvars_statement"),
    )
    return event, None


def _is_bound(name, statement_order, events):
    """`%symexist(name)` -- true iff visible as of `statement_order` (< only)."""
    return any(
        event.statement_order < statement_order and event.name.lower() == name.lower()
        for event in events
    )


def _evaluate_simple_condition(text, statement_order, events):
    """One operand: `%symexist(x)`, `lhs in (v1 v2)`, or `lhs op rhs`.

    Returns True/False, or None when the operand cannot be decided (an
    unsupported form, or an operand that resolve_text leaves unresolved) --
    None always means "never guess", per the dev plan's own MVP principle.
    """
    match = _SYMEXIST_RE.match(text)
    if match:
        return _is_bound(match.group(1), statement_order, events)

    match = _IN_CONDITION_RE.match(text)
    if match:
        lhs, unresolved = resolve_text(
            match.group(1).strip(), statement_order, events, return_unresolved=True
        )
        if unresolved:
            return None
        members = {member.strip().lower() for member in match.group(2).split()}
        return lhs.strip().lower() in members

    match = _COMPARISON_RE.match(text)
    if match:
        lhs, lhs_unresolved = resolve_text(
            match.group("lhs").strip(), statement_order, events, return_unresolved=True
        )
        rhs, rhs_unresolved = resolve_text(
            match.group("rhs").strip(), statement_order, events, return_unresolved=True
        )
        if lhs_unresolved or rhs_unresolved:
            return None
        equal = lhs.strip().lower() == rhs.strip().lower()
        return equal if match.group("op").lower() in ("=", "eq") else not equal

    return None


def _evaluate_condition(condition_text, statement_order, events):
    """Full `%IF` condition: a top-level `or` chain of simple operands.

    A top-level `and`, or anything else unsupported, is unresolvable (never
    guessed). For `or`: any true operand wins regardless of the rest; with no
    true operand, the result is False only if every operand resolved (to
    False) -- one unresolved operand among all-False siblings still makes the
    whole chain unresolvable, since SAS itself would still need that operand
    to decide.
    """
    text = condition_text.strip()
    if re.search(r"\band\b", text, re.IGNORECASE):
        return None

    operands = re.split(r"\s+or\s+", text, flags=re.IGNORECASE)
    results = [
        _evaluate_simple_condition(operand.strip(), statement_order, events)
        for operand in operands
    ]
    if any(result is True for result in results):
        return True
    if all(result is False for result in results):
        return False
    return None


def _classify_opener(text):
    """`%IF ... %THEN %DO;` / `%ELSE %DO;` / any other `%DO` -- or neither."""
    match = _IF_THEN_DO_RE.match(text)
    if match:
        return "if", match.group(1)
    if _ELSE_DO_RE.match(text):
        return "else", None
    if _DO_OPEN_RE.match(text):
        return "do", None
    return None, None


def _find_block_end(statements, start_index):
    """Index of the `%end;` matching the opener at `start_index`, depth-counted."""
    depth = 1
    for index in range(start_index + 1, len(statements)):
        text = statements[index].text
        if _BLOCK_OPENER_RE.match(text):
            depth += 1
        elif _BLOCK_END_RE.match(text):
            depth -= 1
            if depth == 0:
                return index
    return None


def _span_finding(kind, status, first, last, message, suggested_action):
    return {
        "id": f"finding:macro_state:{first.statement_order:04d}:{kind}",
        "status": status,
        "type": kind,
        "severity": "INFORMATION",
        "object": first.file,
        "message": message,
        "suggested_action": suggested_action,
        "affected_nodes": [],
        "affected_edges": [],
        "source": {
            "file": first.file,
            "line_start": first.line_start,
            "line_end": last.line_end,
            "statement_order": first.statement_order,
            "original_text": first.original_text,
            "rule": kind,
        },
    }


def _skipped_branch_finding(first, last, description):
    return _span_finding(
        "macro_branch_not_taken",
        "NOT_EXECUTED",
        first,
        last,
        f"Branch not taken: `{description}` was not selected for the "
        "declared context; its statements were not processed.",
        "Review the branch manually if it was expected to run.",
    )


def _unresolved_condition_finding(statement, description):
    return _span_finding(
        "macro_condition_unresolved",
        "NOT_EXECUTED",
        statement,
        statement,
        f"Macro condition could not be resolved statically: `{description}`. "
        "Its body was processed as evidence, not as a confirmed branch.",
        "Review branches manually; static resolution is out of scope.",
    )


def _walk(statements, events, findings, surviving, skipped_orders, os_fvars_base):
    """Interleaved branch selection + binding over one flat statement list.

    Recurses into a taken (or unresolvable-and-therefore-kept) body so nested
    `%IF`s see the events bound by their own enclosing branch; an untaken
    body is never recursed into -- `_find_block_end`'s depth counter already
    resolved its full span, so every order in it is skipped in one pass,
    with no chance of its own `%os_fvars`/`%let` calls leaking a binding.
    """
    index = 0
    length = len(statements)
    while index < length:
        statement = statements[index]
        kind, condition_text = _classify_opener(statement.text)

        if kind is None:
            event, finding = _bind_os_fvars(statement, events, os_fvars_base)
            if event is None and finding is None:
                event, finding = _bind_statement(statement, events)
            if event is not None:
                events.append(event)
            if finding is not None:
                findings.append(finding)
            surviving.append(statement)
            index += 1
            continue

        end_index = _find_block_end(statements, index)
        if end_index is None:
            # No matching %end in this stream -- keep the opener as evidence
            # rather than guess a boundary; never seen in practice.
            surviving.append(statement)
            index += 1
            continue

        body = statements[index + 1:end_index]
        decision = (
            _evaluate_condition(condition_text, statement.statement_order, events)
            if kind == "if" else None
        )

        else_start = end_index + 1
        else_end = None
        if kind == "if" and else_start < length and _ELSE_DO_RE.match(
            statements[else_start].text
        ):
            else_end = _find_block_end(statements, else_start)

        if decision is True:
            _walk(body, events, findings, surviving, skipped_orders, os_fvars_base)
            if else_end is not None:
                else_span = statements[else_start:else_end + 1]
                skipped_orders.update(s.statement_order for s in else_span)
                findings.append(_skipped_branch_finding(
                    else_span[0], else_span[-1], f"%else (complement of `{condition_text}`)"
                ))
                index = else_end + 1
            else:
                index = end_index + 1
        elif decision is False:
            span = statements[index:end_index + 1]
            skipped_orders.update(s.statement_order for s in span)
            findings.append(_skipped_branch_finding(span[0], span[-1], condition_text))
            if else_end is not None:
                _walk(
                    statements[else_start + 1:else_end], events, findings, surviving,
                    skipped_orders, os_fvars_base,
                )
                index = else_end + 1
            else:
                index = end_index + 1
        else:
            description = condition_text if condition_text is not None else statement.text
            findings.append(_unresolved_condition_finding(statement, description))
            _walk(body, events, findings, surviving, skipped_orders, os_fvars_base)
            index = end_index + 1


def walk_runtime(statements, initial_events=(), os_fvars_base=None):
    """Interleaved `%os_fvars` binding + `%IF`/`%symexist` branch selection.

    Unlike `walk_let_statements`, this also decides which statements survive:
    an untaken `%IF`/`%ELSE` branch's statements never reach the returned
    `surviving_statements`, so a `LIBNAME` or macro call inside it never
    fires downstream. `events` is the full visible table (`initial_events`
    plus everything bound while walking), ready to hand to `resolve_text`
    elsewhere. `skipped_orders` is every `statement_order` dropped by branch
    selection -- the caller needs the set itself (not spans) to filter
    comments, since a comment's `after_statement_order` is a flat position
    marker, not a range (map decision 6).
    """
    events = list(initial_events)
    findings = []
    surviving = []
    skipped_orders = set()
    _walk(list(statements), events, findings, surviving, skipped_orders, os_fvars_base)
    return surviving, events, findings, skipped_orders


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
