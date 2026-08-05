"""Group statements into DATA/PROC blocks (dev plan sections 12-14, 21 Phase 4).

No rule in sections 12-14 fires against a single `Statement`: `data work.a; set
sdtm.ae; run;` is one `Step` node whose `original_text` spans all three
statements, and PROC SORT's `by usubjid;` is a *sibling* statement of the
`proc sort ...;` line that opens the block. Every rule module needs the whole
run from an opener (`data`/`proc x`) to its `run;`/`quit;`, so that grouping
happens once, here, before any rule module runs.

Section 14.6: a missing `quit;` is not fatal. A block still closes at the next
opener, and the recovery is a finding, not a parse failure -- so blocks are
delimited by "next opener or terminator, whichever comes first," never by
requiring the terminator to exist.
"""

import re
from dataclasses import dataclass

_DATA_RE = re.compile(r"^data\b", re.IGNORECASE)
_PROC_RE = re.compile(r"^proc\s+(\w+)", re.IGNORECASE)
_RUN_RE = re.compile(r"^run\s*;?$", re.IGNORECASE)
_QUIT_RE = re.compile(r"^quit\s*;?$", re.IGNORECASE)

# DATA closes on `run;` only; PROC SQL and other PROCs close on `quit;` (SQL)
# or `run;` (most procs). Section 13/14 only exercise PROC SORT and PROC SQL,
# so both terminators are accepted for any PROC -- narrowing this to
# proc-specific terminators is not needed by anything in scope.
_TERMINATORS = {"DATA": _RUN_RE, "PROC": (_RUN_RE, _QUIT_RE)}


@dataclass(frozen=True)
class Block:
    """One DATA/PROC unit: its opener, body, and terminator, in one span.

    `statements` includes the opener and terminator (when present) -- the
    section 22 fixture's `step:001.source.original_text` spans through its own
    `run;`, so a block's `as_source` must too, even though no rule reads the
    bare `run;`/`quit;` text for its own content.
    """

    kind: str  # "DATA" or "PROC"
    proc_name: str  # None for DATA; e.g. "sort", "sql" for PROC
    opener: object  # Statement
    statements: list  # Statement list, opener included
    terminated: bool
    file: str
    line_start: int
    line_end: int
    statement_order: int  # opener's statement_order -- the block's own spine position

    def as_source(self, rule):
        return {
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "statement_order": self.statement_order,
            "original_text": "\n".join(s.original_text for s in self.statements),
            "rule": rule,
        }


def _closes(kind, statement):
    matchers = _TERMINATORS[kind]
    if isinstance(matchers, tuple):
        return any(m.match(statement.text) for m in matchers)
    return matchers.match(statement.text)


def group_blocks(statements):
    """Split `statements` into `Block`s and unattached statements, in order.

    Returns `(blocks, unattached)`, both position-ordered. A block missing its
    terminator (section 14.6) still closes -- at the next opener or end of
    input -- and is returned with `terminated=False` so the caller can emit
    `SQL_BLOCK_NOT_EXPLICITLY_CLOSED` without re-scanning.

    `unattached` holds everything outside a DATA/PROC run: `%let`, `libname`,
    macro calls, `%macro`/`%mend`. Rule modules that only care about blocks
    skip these; the LIBNAME and macro-call rules read this list directly.
    """
    blocks = []
    unattached = []
    current = None  # (kind, proc_name, opener, body, terminated)

    def flush():
        nonlocal current
        if current is None:
            return
        kind, proc_name, opener, body, terminated = current
        blocks.append(
            Block(
                kind=kind,
                proc_name=proc_name,
                opener=opener,
                statements=body,
                terminated=terminated,
                file=opener.file,
                line_start=opener.line_start,
                line_end=body[-1].line_end,
                statement_order=opener.statement_order,
            )
        )
        current = None

    for statement in statements:
        text = statement.text

        data_match = _DATA_RE.match(text)
        proc_match = _PROC_RE.match(text)
        if data_match or proc_match:
            flush()
            kind = "DATA" if data_match else "PROC"
            proc_name = proc_match.group(1).lower() if proc_match else None
            current = (kind, proc_name, statement, [statement], False)
            continue

        if current is None:
            unattached.append(statement)
            continue

        kind, proc_name, opener, body, _terminated = current
        body.append(statement)
        if _closes(kind, statement):
            current = (kind, proc_name, opener, body, True)
            flush()

    flush()
    return blocks, unattached
