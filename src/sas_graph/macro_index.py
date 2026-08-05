"""Index macro definitions from declared `macro_roots` (dev plan section
15.1-15.3, 21 Phase 4).

Reuses `_paths.py`'s prohibited-artifact/containment checks rather than a
third copy -- the phase 4 handoff settles this explicitly: `config.py` and
`macro_state.py` already share them, this module does too.

Indexing happens once, before the main program is parsed (section 9 step 8),
so a macro call site never scans the filesystem itself -- it looks up a name
in the `MacroIndex` this module returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from ._paths import prohibited_reason as _prohibited_reason
from .blocks import group_blocks
from .statements import split_statements
from .source_snapshot import read_text

_MACRO_DEF_RE = re.compile(
    r"%macro\s+(?P<name>[A-Za-z_]\w*)\s*"
    r"(?:\((?P<signature>.*?)\))?\s*;"
    r"(?P<body>.*?)%mend(?:\s+\w+)?\s*;",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class MacroParameterDefinition:
    """One authored `%macro` signature parameter and its explicit default."""

    name: str
    default: str | None


@dataclass(frozen=True)
class MacroDefinitionSite:
    name: str
    file: str
    path: Path
    authored_name: str
    parameters: tuple[MacroParameterDefinition, ...]
    body: str
    line_start: int
    line_end: int
    template_statements: tuple
    template_blocks: tuple
    template_findings: tuple
    template_has_unattached: bool
    # Same-file statement_order of the %macro opener, for the forward-
    # resolution gate in rules_macro_call.apply -- None for a macro_roots-
    # sourced definition (a separate file has no ordering relative to the
    # call site at all, so no gate applies to it).
    definition_order: int | None = None

    def source(self, rule):
        return {
            "file": str(self.path.as_posix()),
            "line_start": self.line_start,
            "line_end": self.line_end,
            "statement_order": None,
            "original_text": None,
            "rule": rule,
        }


@dataclass(frozen=True)
class MacroIndex:
    """Maps a lowercased macro name to every file it was found defined in.

    More than one entry for a name is section 15.3's `MacroConflict` --
    `sites_for(name)` exposes the whole list so the caller can build that
    finding without a second filesystem walk.
    """

    sites: dict = field(default_factory=dict)  # name -> list[MacroDefinitionSite]

    def sites_for(self, name):
        return self.sites.get(name.lower(), [])

    def has_conflict(self, name):
        return len(self.sites_for(name)) > 1

    def is_known(self, name):
        return bool(self.sites_for(name))


def build_macro_index(macro_roots, source_paths=None):
    """Scan every `.sas` file directly under each declared macro root.

    Section 15.1: only declared roots are scanned, never a wider search.
    A prohibited artifact under a macro root is skipped, not read -- the same
    posture `config.py` applies to declared config paths.
    """
    sites = {}
    seen_paths = set()
    for root in macro_roots:
        root = Path(root).resolve()
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.sas")):
            path = path.resolve()
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if _prohibited_reason(path) is not None:
                continue
            try:
                text = read_text(path, "utf-8-sig", source_paths)
            except OSError:
                continue
            masked = _mask_inactive_and_quoted(text)
            for match in _MACRO_DEF_RE.finditer(masked):
                authored_name = match.group("name")
                name = authored_name.lower()
                body_start = match.start("body")
                body_line_offset = text.count("\n", 0, body_start)
                body = text[body_start:match.end("body")]
                source_file = str(path.as_posix())
                parsed = split_statements(body, source_file)
                blocks, unattached = group_blocks(parsed.statements)
                sites.setdefault(name, []).append(
                    MacroDefinitionSite(
                        name=name,
                        file=path.name,
                        path=path,
                        authored_name=authored_name,
                        parameters=_parse_parameters(match.group("signature") or ""),
                        body=body,
                        line_start=text.count("\n", 0, match.start()) + 1,
                        line_end=text.count("\n", 0, match.end()) + 1,
                        template_statements=_offset_statements(
                            parsed.statements, body_line_offset
                        ),
                        template_blocks=_offset_blocks(blocks, body_line_offset),
                        template_findings=tuple(parsed.findings),
                        template_has_unattached=bool(unattached),
                    )
                )
    return MacroIndex(sites=sites)


_INLINE_MACRO_OPEN_RE = re.compile(r"^%macro\b", re.IGNORECASE)
_INLINE_MACRO_END_RE = re.compile(r"^%mend\b", re.IGNORECASE)
_INLINE_MACRO_HEADER_RE = re.compile(
    r"^%macro\s+(?P<name>[A-Za-z_]\w*)\s*"
    r"(?:\((?P<signature>.*)\))?"
    r"(?:\s*/[^;]*)?\s*;$",
    re.IGNORECASE,
)


def build_inline_macro_index(statements, path):
    """Index top-level `%macro...%mend` definitions already split out of one
    file's own statement stream (section 15.7's template-binding path
    extended to same-file definitions).

    `run_pipeline._process_file` strips every statement inside a
    `%macro...%mend` span from its runtime statement stream so an uncalled
    body is never executed inline -- but the definition itself was
    previously discarded rather than indexed, so a call to it later in the
    SAME file fell through to `UnknownMacro` even though the source sits
    right there in the file being parsed. This mirrors `build_macro_index`'s
    site shape so `rules_macro_call.apply` binds it through the identical
    template path, without a second filesystem read.

    Only a completed *top-level* span becomes a site -- a nested `%macro`
    stays part of its outer definition's body, matching
    `run_pipeline._macro_definition_spans`'s own depth tracking.
    """
    sites = {}
    seen_spans = set()
    depth = 0
    opener = None
    body = []
    for statement in statements:
        if _INLINE_MACRO_OPEN_RE.match(statement.text):
            if depth == 0:
                opener = statement
                body = []
            else:
                body.append(statement)
            depth += 1
            continue
        if depth and _INLINE_MACRO_END_RE.match(statement.text):
            depth -= 1
            if depth == 0:
                header = _INLINE_MACRO_HEADER_RE.match(opener.text)
                if header is not None:
                    authored_name = header.group("name")
                    name = authored_name.lower()
                    blocks, unattached = group_blocks(body)
                    # `opener.file` is the statement's own originating file --
                    # for a definition spliced in from a %include'd child, that
                    # is the child's name, not the outer file being processed.
                    # `Statement.file` carries no directory (statements.py:
                    # "a display name... not a real filesystem path"), so a full
                    # resolved path is only available when they match.
                    site_path = path if opener.file == path.name else Path(opener.file)
                    # An %include'd file included more than once splices the
                    # same physical %macro...%mend text into the statement
                    # stream once per inclusion -- same path, same line span.
                    # That is one physical definition, not a MacroConflict;
                    # dedup keeps the first (earliest) occurrence.
                    span_key = (site_path, opener.line_start, statement.line_end)
                    if span_key not in seen_spans:
                        seen_spans.add(span_key)
                        sites.setdefault(name, []).append(
                            MacroDefinitionSite(
                                name=name,
                                file=site_path.name,
                                path=site_path,
                                authored_name=authored_name,
                                parameters=_parse_parameters(header.group("signature") or ""),
                                body="\n".join(s.original_text for s in body),
                                line_start=opener.line_start,
                                line_end=statement.line_end,
                                template_statements=tuple(body),
                                template_blocks=tuple(blocks),
                                template_findings=(),
                                template_has_unattached=bool(unattached),
                                definition_order=opener.statement_order,
                            )
                        )
                opener = None
                continue
        if depth:
            body.append(statement)
    return sites


def merge_inline_sites(macro_index, inline_sites):
    """Layer same-file inline definitions on top of a macro_roots-sourced
    `MacroIndex`, without mutating it -- the caller scopes `inline_sites` to
    one file's own statements, so a definition local to that file never
    becomes visible while parsing any other file.

    codex #2: a `macro_roots` entry that overlaps the file being processed
    (e.g. it points at the main program's own directory) scans that file and
    indexes the same physical %macro...%mend span again -- dedupe by
    (path, line span) so the same definition never becomes a MacroConflict.
    """
    if not inline_sites:
        return macro_index
    combined = {name: list(entries) for name, entries in macro_index.sites.items()}
    for name, entries in inline_sites.items():
        existing_spans = {
            (site.path, site.line_start, site.line_end) for site in combined.get(name, [])
        }
        deduped = [
            site for site in entries
            if (site.path, site.line_start, site.line_end) not in existing_spans
        ]
        if deduped:
            combined.setdefault(name, []).extend(deduped)
    return MacroIndex(sites=combined)


def _mask_inactive_and_quoted(text):
    """Blank comments and quoted strings without changing source offsets."""
    masked = list(text)

    def hide(start, end):
        for index in range(start, end):
            if masked[index] not in "\r\n":
                masked[index] = " "

    pos = 0
    has_active = False
    while pos < len(text):
        if text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            end = len(text) if end == -1 else end + 2
            hide(pos, end)
            pos = end
            continue
        if text[pos] in "\"'":
            quote = text[pos]
            end = pos + 1
            while end < len(text):
                if text[end] != quote:
                    end += 1
                elif text.startswith(quote * 2, end):
                    end += 2
                else:
                    end += 1
                    break
            hide(pos, end)
            has_active = True
            pos = end
            continue
        if not has_active and (text[pos] == "*" or text.startswith("%*", pos)):
            end = text.find(";", pos)
            end = len(text) if end == -1 else end + 1
            hide(pos, end)
            pos = end
            continue
        if text[pos] == ";":
            has_active = False
        elif not text[pos].isspace():
            has_active = True
        pos += 1
    return "".join(masked)


def _offset_blocks(blocks, line_offset):
    prepared = []
    for block in blocks:
        statements = [
            replace(
                statement,
                line_start=statement.line_start + line_offset,
                line_end=statement.line_end + line_offset,
            )
            for statement in block.statements
        ]
        prepared.append(
            replace(
                block,
                opener=statements[0],
                statements=statements,
                line_start=block.line_start + line_offset,
                line_end=block.line_end + line_offset,
            )
        )
    return tuple(prepared)


def _offset_statements(statements, line_offset):
    return tuple(
        replace(
            statement,
            line_start=statement.line_start + line_offset,
            line_end=statement.line_end + line_offset,
        )
        for statement in statements
    )


def _parse_parameters(signature):
    parameters = []
    for raw in signature.split(","):
        raw = raw.strip()
        if not raw:
            continue
        name, marker, default = raw.partition("=")
        name = name.strip()
        if re.fullmatch(r"[A-Za-z_]\w*", name):
            parameters.append(
                MacroParameterDefinition(name=name, default=default.strip() if marker else None)
            )
    return tuple(parameters)
