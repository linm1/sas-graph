"""Split SAS source into ordered statements (dev plan sections 7, 11.6, 21 Phase 2).

Named `statements.py` rather than the plan's `tokenizer.py`: the local agent
config denies reads on `**/*token*`, which a file called `tokenizer.py` matches.
The module's job is exactly what section 21 Phase 2 specifies.

One scanner, one pass, character by character. The alternative — strip comments
with a regex, then split the remainder on `;` — is shorter but destroys the thing
section 7 requires: once text is deleted, the line numbers of everything after it
are wrong, and every finding downstream points at the wrong source line. So this
scanner never removes text. It walks offsets, records where comments *were*, and
lets active statements keep their real line ranges.

Three SAS-specific traps shape the code:

- **Block comments do not nest.** `/* a /* b */` closes at the first `*/`. A depth
  counter would swallow the rest of the file.
- **`*` is a comment only where a statement could start.** `x = a * b;` is
  multiplication; `* note;` is a comment. The scanner tracks whether anything
  active has been seen since the last `;`.
- **Quotes escape by doubling.** `"it""s"` is one string. Treating the second `"`
  as a close would put the tail of the file into string state.

Section 11.6: commented code is inactive evidence. It is returned separately and
never becomes a statement, so no later phase can accidentally read a dependency
out of disabled code.
"""

from dataclasses import dataclass, field

# `%*` before `*` matters: a macro comment must not be read as a bare star
# comment that happens to follow a `%`.
_MACRO_STAR = "%*"


@dataclass(frozen=True)
class Statement:
    """One active SAS statement, with the source location section 7 requires.

    `text` is whitespace-normalised for rule matching; `original_text` is verbatim
    for audit. Both are kept because collapsing them loses one or the other.
    """

    text: str
    original_text: str
    file: str
    line_start: int
    line_end: int
    statement_order: int
    terminated: bool = True

    def as_source(self, rule):
        """Render the section 7 source block, tagged with the rule that fired."""
        return {
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "statement_order": self.statement_order,
            "original_text": self.original_text,
            "rule": rule,
        }


@dataclass(frozen=True)
class Comment:
    """A comment span, kept as section 11.6 inactive evidence.

    `after_statement_order` places the comment in the statement flow, which is what
    makes "an earlier draft read from sdtm.suppae" reviewable rather than floating.
    `kind` is one of `block`, `star`, `macro_star`.
    """

    text: str
    original_text: str
    kind: str
    file: str
    line_start: int
    line_end: int
    after_statement_order: int


@dataclass(frozen=True)
class SplitResult:
    statements: list = field(default_factory=list)
    comments: list = field(default_factory=list)
    findings: list = field(default_factory=list)


class _Scanner:
    def __init__(self, text, file_name):
        self.text = text
        self.file = file_name
        self.pos = 0
        self.statements = []
        self.comments = []
        self.findings = []
        self.comment_spans = []

        # Offset where the statement currently being collected began. Advanced
        # past comments while the statement is still empty, so a comment before
        # a statement is not glued onto its front.
        self.start = 0
        # True once a non-space, non-comment character has been seen since the
        # last `;`. This is what distinguishes `* note;` from `x = a * b;`.
        self.has_active = False

        # Line starts, so an offset can be turned into a line number without
        # rescanning. `\r\n` is handled by counting only `\n`: the `\r` stays in
        # the offset arithmetic but never starts a line.
        self._line_starts = [0]
        for index, char in enumerate(text):
            if char == "\n":
                self._line_starts.append(index + 1)

    # --- position helpers --------------------------------------------------

    def line_of(self, offset):
        """1-based line number for a character offset (binary search)."""
        low, high = 0, len(self._line_starts) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if self._line_starts[mid] <= offset:
                low = mid
            else:
                high = mid - 1
        return low + 1

    def at(self, literal):
        return self.text.startswith(literal, self.pos)

    # --- emitting ----------------------------------------------------------

    def _finding(self, kind, offset, message):
        self.findings.append(
            {
                "id": f"finding:statements:{len(self.findings) + 1:03d}",
                # Deliberately not BLOCKED. `config.py` established that any
                # BLOCKED finding makes the run FAILED, and section 5.3 reserves
                # FAILED for a parser that "cannot tokenize the SAS file enough
                # to identify statements". An unbalanced delimiter late in a file
                # does not meet that bar: the statements already emitted are
                # still true, so section 5.2 makes this PARTIAL and section 26
                # wants the truncation exposed rather than the run discarded.
                "status": "REQUIRES_DECISION",
                "type": kind,
                "severity": "ERROR",
                "object": self.file,
                "message": message,
                "suggested_action": "Fix the unbalanced delimiter in the SAS source.",
                "affected_nodes": [],
                "affected_edges": [],
                "source": {
                    "file": self.file,
                    "line_start": self.line_of(offset),
                    "line_end": self.line_of(offset),
                    "statement_order": len(self.statements) + 1,
                    "original_text": None,
                    "rule": kind,
                },
            }
        )

    def emit_statement(self, end, terminated):
        """Close the statement running from `self.start` to `end` (exclusive).

        Comment spans inside the range stay in `original_text`; only the
        normalised `text` has them cut out, which is why comment offsets are
        recorded as the scan goes rather than reconstructed here.
        """
        start = self.start
        raw = self.text[start:end]
        self.start = end
        self.has_active = False

        if not raw.strip():
            return

        text = " ".join(self._without_comments(raw, start).split())
        if not text:
            return

        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        self.statements.append(
            Statement(
                text=text,
                # Only newlines are trimmed, so a statement's own indentation
                # survives for audit — section 7 wants it verbatim.
                original_text=raw.strip("\r\n"),
                file=self.file,
                line_start=self.line_of(start + leading),
                line_end=self.line_of(max(end - 1 - trailing, start)),
                statement_order=len(self.statements) + 1,
                terminated=terminated,
            )
        )

    def _without_comments(self, raw, offset):
        """Cut comment ranges out of `raw`, right to left so offsets stay valid."""
        for start, end in sorted(self.comment_spans, reverse=True):
            if offset <= start and end <= offset + len(raw):
                raw = raw[: start - offset] + raw[end - offset :]
        return raw

    def add_comment(self, kind, start, end):
        self.comments.append(
            Comment(
                text=self.text[start:end].strip(),
                original_text=self.text[start:end],
                kind=kind,
                file=self.file,
                line_start=self.line_of(start),
                line_end=self.line_of(max(end - 1, start)),
                after_statement_order=len(self.statements),
            )
        )

    # --- the scan ----------------------------------------------------------

    def run(self):
        text = self.text
        length = len(text)

        while self.pos < length:
            char = text[self.pos]

            if self.at("/*"):
                if not self._skip_block_comment():
                    return self.result()
                continue

            if char in "\"'":
                if not self._skip_string(char):
                    return self.result()
                continue

            # A comment can only open where a statement could start: nothing
            # active collected yet since the last `;`.
            if not self.has_active and (char == "*" or self.at(_MACRO_STAR)):
                if not self._skip_star_comment():
                    return self.result()
                continue

            if char == ";":
                self.pos += 1
                self.emit_statement(self.pos, terminated=True)
                continue

            if not char.isspace():
                self.has_active = True
            self.pos += 1

        # Section 26: a file ending mid-statement exposes the truncation rather
        # than dropping the text.
        self.emit_statement(length, terminated=False)
        return self.result()

    def _skip_block_comment(self):
        start = self.pos
        # Searching from `start + 2` is what makes comments non-nesting: the
        # first `*/` closes, whatever `/*` sequences appear in between.
        end = self.text.find("*/", start + 2)
        if end == -1:
            self._finding(
                "unterminated_comment",
                start,
                "block comment opened with `/*` is never closed.",
            )
            self.pos = len(self.text)
            return False

        end += 2
        self.add_comment("block", start, end)
        self.comment_spans.append((start, end))
        self.pos = end
        if not self.has_active:
            # Nothing active yet, so the comment belongs to no statement. Move
            # the statement start past it to keep `line_start` honest.
            self.start = end
        return True

    def _skip_star_comment(self):
        start = self.pos
        kind = "macro_star" if self.at(_MACRO_STAR) else "star"
        end = self.text.find(";", start)
        if end == -1:
            opener = "%*" if kind == "macro_star" else "*"
            self._finding(
                "unterminated_comment",
                start,
                f"`{opener}` comment is never closed by a `;`.",
            )
            self.pos = len(self.text)
            return False

        end += 1
        self.add_comment(kind, start, end)
        self.comment_spans.append((start, end))
        self.pos = end
        self.start = end
        return True

    def _skip_string(self, quote):
        start = self.pos
        self.pos += 1
        while self.pos < len(self.text):
            if self.text[self.pos] != quote:
                self.pos += 1
                continue
            # A doubled quote is an escaped quote, not a close.
            if self.text.startswith(quote * 2, self.pos):
                self.pos += 2
                continue
            self.pos += 1
            self.has_active = True
            return True

        self._finding(
            "unterminated_string",
            start,
            f"string opened with {quote} is never closed.",
        )
        self.pos = len(self.text)
        return False

    def result(self):
        return SplitResult(
            statements=self.statements,
            comments=self.comments,
            findings=self.findings,
        )


def split_statements(text, file_name):
    """Split SAS `text` into active statements plus inactive comment evidence."""
    return _Scanner(text, file_name).run()
