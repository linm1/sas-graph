"""Parse `%gm` macro markdown intro docs directly (wayfinder: Macro contracts
from markdown, no YAML -- design-md-contract-shape.md).

Section 15.5's "narrative documentation ... cannot drive graph inference" is
deliberately reversed here: a doc's own `## Purpose`/`## Parameters`/example
call lines are now the only source, no human-curated YAML, no approval gate.
A doc that fails to parse (no `# ` heading, no parseable Parameters table)
stays out of the index via `errors` -- excluded from driving inference for
that macro, not raised.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from ._paths import prohibited_reason as _prohibited_reason
from .source_snapshot import read_text

_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|\s*$")
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$")
_PURPOSE_HEADING_RE = re.compile(r"^##\s+Purpose\s*$", re.IGNORECASE)
_HEADING_ANY_RE = re.compile(r"^#{1,6}\s+\S")
_EXAMPLE_CALL_RE = re.compile(r"%([A-Za-z_]\w*)\s*\(")


@dataclass(frozen=True)
class DocParameter:
    required: bool
    default: str | None = None


@dataclass(frozen=True)
class MacroContract:
    macro: str  # from the # heading, case preserved as written
    purpose: str
    parameters: dict  # name -> DocParameter
    examples: tuple  # raw %macroName(...) call text, as found in the doc
    path: Path
    errors: tuple = ()

    def parameter(self, param_name):
        return next(
            (param for name, param in self.parameters.items()
             if name.lower() == param_name.lower()),
            None,
        )


@dataclass(frozen=True)
class MacroContractIndex:
    contracts: dict = field(default_factory=dict)  # lowercase macro -> MacroContract

    def get(self, macro_name):
        return self.contracts.get(macro_name.lower())


def _clean_cell(cell):
    text = cell.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    text = html.unescape(text)
    return " ".join(text.split()).strip()


def _split_row(line):
    match = _ROW_RE.match(line.strip())
    if match is None:
        return None
    body = match.group(1)
    # Markdown table cells may contain an escaped pipe (\|) as literal text,
    # e.g. "E\|ERROR\|ABORT" -- must not be treated as a column delimiter.
    placeholder = "\x00"
    protected = body.replace("\\|", placeholder)
    cells = protected.split("|")
    return [_clean_cell(cell.replace(placeholder, "|")) for cell in cells]


def _find_parameter_table(lines):
    header_index = None
    for index, line in enumerate(lines):
        cells = _split_row(line)
        if cells is None:
            continue
        lowered = [c.lower() for c in cells]
        if "parameter" in lowered and "default value" in lowered:
            header_index = index
            break
    if header_index is None:
        return None, None
    header = [c.lower() for c in _split_row(lines[header_index])]
    return header, header_index


def _macro_name_from_heading(lines):
    for line in lines:
        match = _HEADING_RE.match(line)
        if match is not None:
            return match.group(1).strip()
    return None


def _parse_parameter_table(lines):
    header, header_index = _find_parameter_table(lines)
    if header is None:
        return None, ["no Parameters table found"]

    name_col = header.index("parameter")
    default_col = header.index("default value")

    row_index = header_index + 1
    if row_index < len(lines) and _SEPARATOR_RE.match(lines[row_index].strip()):
        row_index += 1

    parameters = {}
    errors = []
    while row_index < len(lines):
        raw_line = lines[row_index]
        if not raw_line.strip():
            break  # blank line ends the table

        cells = _split_row(raw_line)
        if cells is None or len(cells) != len(header):
            errors.append(f"skipped malformed parameter row: {raw_line!r}")
            row_index += 1
            continue

        name = cells[name_col]
        default_cell = cells[default_col]
        if not name:
            row_index += 1
            continue

        is_required = default_cell.strip().upper() == "REQUIRED"
        default_value = None if is_required or not default_cell.strip() else default_cell
        parameters[name] = DocParameter(required=is_required, default=default_value)
        row_index += 1

    return parameters, errors


def _extract_purpose(lines):
    for index, line in enumerate(lines):
        if _PURPOSE_HEADING_RE.match(line):
            body = []
            for later in lines[index + 1 :]:
                if _HEADING_ANY_RE.match(later):
                    break
                if later.strip():
                    body.append(later.strip())
            return html.unescape(" ".join(body))
    return ""


def _extract_examples(text, macro_name):
    """Raw `%macroName(...)` call text found anywhere in the doc body, for
    the doc's own macro only (case-insensitive) -- prose elsewhere in the
    Discussion section can reference other macros (e.g. `%nrstr(%%)gmOther(`)
    and must not be mistaken for this doc's own example call.

    Examples live in unstructured prose (the Discussion `<pre><code>` blob),
    not their own heading, and can wrap across lines -- so this scans the
    whole doc rather than line-by-line. Parens are depth-counted from each
    `%name(` to its match, ignoring parens inside a quoted (`'`/`"`) span so
    a literal `)` in a string doesn't look like a close. A call with no
    matching close (e.g. embedded regex text that unbalances parens) falls
    back to the next top-level `;` (back at the opening paren depth) instead
    of the next line break, since a real fixture's whole Discussion section
    can be a single giant line -- cutting at the next newline would swallow
    unrelated prose and later calls too.
    """
    text = html.unescape(text)
    macro_lower = macro_name.lower()
    seen = set()
    examples = []
    for match in _EXAMPLE_CALL_RE.finditer(text):
        if match.group(1).lower() != macro_lower:
            continue
        start = match.start()
        depth = 0
        end = None
        semicolon_end = None
        quote = None
        for index in range(match.end() - 1, len(text)):
            char = text[index]
            if quote is not None:
                if char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
            elif char == ";" and depth == 1 and semicolon_end is None:
                semicolon_end = index + 1
        if end is None:
            end = semicolon_end if semicolon_end is not None else len(text)
        call_text = " ".join(text[start:end].split())
        if call_text not in seen:
            seen.add(call_text)
            examples.append(call_text)
    return tuple(examples)


def parse_macro_doc(path, text=None):
    """Parse one `%gm` markdown macro doc into a `MacroContract`.

    Returns a contract with a non-empty `errors` tuple (and empty
    parameters/purpose/examples) when no `# ` heading is found -- there is
    no filename fallback, since an unnamed doc cannot be indexed by macro.

    `text` lets a caller that already read the file (e.g. `load_macro_contracts`,
    via the source-snapshot-tracked `read_text`) pass those exact bytes through
    instead of this function re-reading `path` itself -- a second, untracked
    read here would break the run-wide read-once/snapshot-consistency
    guarantee (`source_snapshot.SourceChangedDuringRunError`).
    """
    path = Path(path)
    if text is None:
        text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    macro = _macro_name_from_heading(lines)
    if macro is None:
        return MacroContract(
            macro="", purpose="", parameters={}, examples=(), path=path,
            errors=("no macro heading found",),
        )

    parameters, table_errors = _parse_parameter_table(lines)
    purpose = _extract_purpose(lines)
    examples = _extract_examples(text, macro)

    return MacroContract(
        macro=macro,
        purpose=purpose,
        parameters=parameters or {},
        examples=examples,
        path=path,
        errors=tuple(table_errors),
    )


def _record_contract(contracts, contract):
    """Insert `contract` into `contracts`, applying the same duplicate-name
    collision handling regardless of which scan mode found it."""
    macro_name = contract.macro.lower()
    if macro_name in contracts:
        existing = contracts[macro_name]
        contracts[macro_name] = MacroContract(
            macro=existing.macro,
            purpose=existing.purpose,
            parameters=existing.parameters,
            examples=existing.examples,
            path=existing.path,
            errors=(
                *existing.errors,
                "duplicate contracts for macro name: "
                f"{existing.path.as_posix()}, {contract.path.as_posix()}",
            ),
        )
    else:
        contracts[macro_name] = contract


def _load_one(path, source_paths):
    """Read and parse one `.md` doc, or None for a prohibited/unreadable/
    unheaded file -- shared by both the full-scan and filtered paths."""
    if _prohibited_reason(path) is not None:
        return None
    try:
        text = read_text(path, "utf-8", source_paths)
    except OSError:
        return None
    if _macro_name_from_heading(text.splitlines()) is None:
        return None
    return parse_macro_doc(path, text=text)


def _load_full_scan(macro_contract_roots, source_paths):
    contracts = {}
    for root in macro_contract_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            contract = _load_one(path, source_paths)
            if contract is not None:
                _record_contract(contracts, contract)
    return contracts


def _load_filtered(macro_contract_roots, source_paths, macro_names):
    """One flat directory listing per root, matched against wanted names
    case-insensitively. A doc in a subdirectory of a root is not found here
    -- unlike the full scan's `rglob` -- since the filter is only meant to
    replace the expected "every doc directly under the root" shape.
    """
    wanted = {name.lower() for name in macro_names}
    contracts = {}
    for root in macro_contract_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        by_stem = {
            path.stem.lower(): path
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() == ".md"
        }
        for name in sorted(wanted):
            path = by_stem.get(name)
            if path is None:
                continue
            contract = _load_one(path, source_paths)
            if contract is not None:
                _record_contract(contracts, contract)
    return contracts


def load_macro_contracts(macro_contract_roots, source_paths=None, macro_names=None):
    """Scan for `.md` macro docs under each declared root.

    With `macro_names` omitted or empty, every `.md` file anywhere under each
    root is parsed (recursive `rglob`) -- unchanged full-scan behavior. With
    `macro_names` non-empty, each root gets one flat directory listing
    instead (docs in subdirectories are not found in this mode) and only
    filenames matching a wanted name (case-insensitive) are parsed.

    A doc that fails to parse stays out of the index (see `parse_macro_doc`).
    Two docs naming the same macro both get an errors entry noting the
    duplicate paths; neither is usable -- same collision handling as the
    prior YAML loader, in either scan mode.
    """
    if macro_names:
        contracts = _load_filtered(macro_contract_roots, source_paths, macro_names)
    else:
        contracts = _load_full_scan(macro_contract_roots, source_paths)
    return MacroContractIndex(contracts=contracts)
