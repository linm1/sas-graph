#!/usr/bin/env python3
"""Compare the legacy SAS scanner with an isolated tree-sitter-sas parser.

The comparison is intentionally a standalone spike.  The parent process creates
and removes a temporary virtual environment, installs only the pinned parser
packages there, then re-executes this file in that environment.  Nothing in the
project environment or in ``src/sas_graph`` imports the optional parser.

Usage::

    python tools/parser_compare.py --format markdown
    python tools/parser_compare.py --format json

The command exits successfully with a ``SKIP`` message when Python, virtualenv
creation, package installation, or the parser smoke import is unavailable.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


TREE_SITTER_SAS_VERSION = "0.4.2"
TREE_SITTER_VERSION = "0.24.0"
PARSER_REQUIREMENTS = (
    "tree-sitter-sas=={}".format(TREE_SITTER_SAS_VERSION),
    "tree-sitter=={}".format(TREE_SITTER_VERSION),
)
MACRO_REF_RE = re.compile(r"&[A-Za-z_]\w*\.?", re.IGNORECASE)
MACRO_CALL_RE = re.compile(r"%[A-Za-z_]\w*\(", re.IGNORECASE)
BOUNDARY_SUFFIXES = ("_statement", "_header")
BOUNDARY_EXTRA_TYPES = {
    "macro_definition",
    "macro_end",
    "macro_do_statement",
    "macro_if_statement",
    "macro_variable_assignment",
    "null_statement",
}
MACRO_NODE_TYPES = (
    "macro_definition",
    "macro_end",
    "macro_if_statement",
    "macro_do_statement",
    "macro_call_statement",
    "macro_variable_assignment",
    "macro_variable_ref",
    "include_statement",
)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="output format (default: markdown)",
    )
    parser.add_argument(
        "--child",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def _run(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _skip(message: str, details: str = "") -> int:
    print("[SKIP] " + message)
    if details:
        print(details.rstrip())
    return 0


def _walk(node: Any) -> Iterable[Any]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _is_boundary_type(node_type: str) -> bool:
    return node_type.endswith(BOUNDARY_SUFFIXES) or node_type in BOUNDARY_EXTRA_TYPES


def _line_starts(source: str) -> List[int]:
    starts = [0]
    for index, char in enumerate(source):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _char_to_byte(source: str, index: int) -> int:
    return len(source[:index].encode("utf-8"))


def _legacy_spans(source: str, statements: Sequence[Any]) -> List[Dict[str, Any]]:
    """Recover byte offsets from the scanner's exact ``original_text`` fields."""

    spans = []
    cursor = 0
    for statement in statements:
        start_char = source.find(statement.original_text, cursor)
        if start_char < 0:
            start_char = cursor
        end_char = start_char + len(statement.original_text)
        leading_match = re.match(r"[ \t\f\v]*", statement.original_text)
        token_start_char = start_char + len(leading_match.group(0))
        spans.append(
            {
                "start_byte": _char_to_byte(source, start_char),
                "end_byte": _char_to_byte(source, end_char),
                "token_start_byte": _char_to_byte(source, token_start_char),
                "token_end_byte": _char_to_byte(source, end_char),
                "line_start": statement.line_start,
                "line_end": statement.line_end,
                "statement_order": statement.statement_order,
                "text": statement.text,
                "original_text": statement.original_text,
                "terminated": statement.terminated,
                "offset_recovered": start_char >= cursor,
            }
        )
        cursor = end_char
    return spans


def _node_record(node: Any, source_bytes: bytes) -> Dict[str, Any]:
    raw = source_bytes[node.start_byte : node.end_byte]
    return {
        "type": node.type,
        "start_byte": node.start_byte,
        "end_byte": node.end_byte,
        "start_point": [node.start_point[0] + 1, node.start_point[1] + 1],
        "end_point": [node.end_point[0] + 1, node.end_point[1] + 1],
        "text": raw.decode("utf-8", errors="replace"),
    }


def _legacy_ir_counts(statements: Sequence[Any], source_name: str) -> Dict[str, int]:
    """Exercise the current IR entry points without changing graph state."""

    from sas_graph.blocks import group_blocks
    from sas_graph.data_step_ir import parse_assignment
    from sas_graph.ir import IRUnknownExpression
    from sas_graph.rules_data_step import _literal_value, _rhs_identifiers
    from sas_graph.sql_ir import parse_sql

    assignment_candidates = 0
    assignment_parsed = 0
    assignment_unknown = 0
    sql_candidates = 0
    sql_parsed = 0
    sql_errors = 0

    blocks, _ = group_blocks(statements)
    for block in blocks:
        body = block.statements[1:]
        if block.kind == "DATA":
            for statement in body:
                text = statement.text.strip()
                if not re.match(r"^&?[A-Za-z_]\w*\.?\s*=", text):
                    continue
                assignment_candidates += 1
                assignment = parse_assignment(
                    text,
                    statement.as_source("parser_comparison"),
                    (),
                    _literal_value,
                    _rhs_identifiers,
                )
                if assignment is not None:
                    assignment_parsed += 1
                    if isinstance(assignment.value, IRUnknownExpression):
                        assignment_unknown += 1

        if block.kind != "PROC" or block.proc_name != "sql":
            continue
        for statement in body:
            text = statement.text.strip()
            sql_match = re.match(
                r"^(create\s+(?:table|view)|insert\s+into)\b",
                text,
                re.IGNORECASE,
            )
            if not sql_match:
                continue
            sql_candidates += 1
            subtype = "CREATE_TABLE"
            if text.lower().startswith("create view"):
                subtype = "CREATE_VIEW"
            elif text.lower().startswith("insert into"):
                subtype = "INSERT_INTO"
            try:
                parse_sql(text, subtype, "", statement.as_source("parser_comparison"))
            except Exception:
                # The comparison records the existing parser's reachable
                # constructs; an exception is a real unsupported boundary.
                sql_errors += 1
            else:
                sql_parsed += 1

    return {
        "assignment_candidates": assignment_candidates,
        "assignment_parsed": assignment_parsed,
        "assignment_unknown_expression": assignment_unknown,
        "sql_candidates": sql_candidates,
        "sql_parsed": sql_parsed,
        "sql_errors": sql_errors,
        "source_file": source_name,
    }


def _fixture_result(path: Path, repo_root: Path) -> Dict[str, Any]:
    from tree_sitter import Language, Parser
    import tree_sitter_sas

    from sas_graph.blocks import group_blocks
    from sas_graph.statements import split_statements

    source = path.read_text(encoding="utf-8")
    source_bytes = source.encode("utf-8")
    relative = path.relative_to(repo_root / "tests" / "fixtures").as_posix()
    legacy = split_statements(source, relative)
    legacy_spans = _legacy_spans(source, legacy.statements)

    parser = Parser(Language(tree_sitter_sas.language()))
    tree = parser.parse(source_bytes)
    nodes = list(_walk(tree.root_node))
    node_types = collections.Counter(node.type for node in nodes if node.is_named)

    named_spans: Dict[Tuple[int, int], List[str]] = collections.defaultdict(list)
    boundary_spans: Dict[Tuple[int, int], List[str]] = collections.defaultdict(list)
    for node in nodes:
        if not node.is_named:
            continue
        span = (node.start_byte, node.end_byte)
        named_spans[span].append(node.type)
        if _is_boundary_type(node.type):
            boundary_spans[span].append(node.type)

    exact_matches = []
    unmatched_legacy = []
    for span in legacy_spans:
        key = (span["token_start_byte"], span["token_end_byte"])
        types = sorted(set(named_spans.get(key, [])))
        match = dict(span)
        match["tree_types"] = types
        if types:
            exact_matches.append(match)
        else:
            unmatched_legacy.append(match)

    tree_boundary_records = []
    for (start_byte, end_byte), types in sorted(boundary_spans.items()):
        tree_boundary_records.append(
            {
                "start_byte": start_byte,
                "end_byte": end_byte,
                "types": sorted(set(types)),
                "text": source_bytes[start_byte:end_byte].decode(
                    "utf-8", errors="replace"
                ),
            }
        )

    legacy_span_keys = {
        (item["token_start_byte"], item["token_end_byte"]) for item in legacy_spans
    }
    extra_tree_boundaries = [
        item
        for item in tree_boundary_records
        if (item["start_byte"], item["end_byte"]) not in legacy_span_keys
    ]
    error_nodes = [_node_record(node, source_bytes) for node in nodes if node.is_error]
    missing_nodes = [
        _node_record(node, source_bytes) for node in nodes if node.is_missing
    ]

    macro_legacy = {
        "definitions": sum(
            item["text"].lower().startswith("%macro ") for item in legacy_spans
        ),
        "ends": sum(item["text"].lower().startswith("%mend") for item in legacy_spans),
        "ifs": sum(item["text"].lower().startswith("%if ") for item in legacy_spans),
        "includes": sum(
            item["text"].lower().startswith("%include") for item in legacy_spans
        ),
        "calls": sum(bool(MACRO_CALL_RE.search(item["text"])) for item in legacy_spans),
        "variable_refs": sum(
            len(MACRO_REF_RE.findall(item["text"])) for item in legacy_spans
        ),
    }
    macro_tree = {
        node_type: node_types.get(node_type, 0) for node_type in MACRO_NODE_TYPES
    }

    blocks, unattached = group_blocks(legacy.statements)
    ir_counts = _legacy_ir_counts(legacy.statements, relative)
    ir_counts.update(
        {
            "legacy_blocks": len(blocks),
            "legacy_unattached": len(unattached),
        }
    )
    tree_coverage = {
        key: node_types.get(key, 0)
        for key in (
            "data_step",
            "data_step_header",
            "set_statement",
            "merge_statement",
            "sql_create_statement",
            "sql_select_statement",
            "sql_join_clause",
            "table_reference",
            "generic_statement",
        )
    }

    return {
        "fixture": relative,
        "bytes": len(source_bytes),
        "lines": source.count("\n")
        + (1 if source and not source.endswith("\n") else 0),
        "legacy": {
            "statement_count": len(legacy_spans),
            "comment_count": len(legacy.comments),
            "finding_count": len(legacy.findings),
            "finding_types": dict(
                collections.Counter(f["type"] for f in legacy.findings)
            ),
            "statements": legacy_spans,
            "ir": ir_counts,
        },
        "tree_sitter": {
            "boundary_span_count": len(tree_boundary_records),
            "boundary_types": dict(
                collections.Counter(
                    type_name
                    for item in tree_boundary_records
                    for type_name in item["types"]
                )
            ),
            "exact_span_matches": len(exact_matches),
            "unmatched_legacy": unmatched_legacy,
            "extra_boundaries": extra_tree_boundaries,
            "error_count": len(error_nodes),
            "missing_count": len(missing_nodes),
            "errors": error_nodes,
            "missing": missing_nodes,
            "node_types": dict(node_types),
            "macro": macro_tree,
            "coverage": tree_coverage,
        },
        "macro": {"legacy": macro_legacy, "tree_sitter": macro_tree},
    }


def _child_payload(repo_root: Path) -> Dict[str, Any]:
    # Importing here keeps the parent process free of optional parser imports.
    fixtures = sorted(
        path
        for path in (repo_root / "tests" / "fixtures").rglob("*.sas")
        if path.is_file()
    )
    results = [_fixture_result(path, repo_root) for path in fixtures]
    aggregate_types = collections.Counter()
    aggregate_coverage = collections.Counter()
    aggregate_errors = collections.Counter()
    aggregate_findings = collections.Counter()
    aggregate_macro_legacy = collections.Counter()
    aggregate_macro_tree = collections.Counter()
    for result in results:
        aggregate_types.update(result["tree_sitter"]["node_types"])
        aggregate_coverage.update(result["tree_sitter"]["coverage"])
        aggregate_errors.update(
            {
                "tree_error_nodes": result["tree_sitter"]["error_count"],
                "tree_missing_nodes": result["tree_sitter"]["missing_count"],
            }
        )
        aggregate_findings.update(result["legacy"]["finding_types"])
        aggregate_macro_legacy.update(result["macro"]["legacy"])
        aggregate_macro_tree.update(result["macro"]["tree_sitter"])

    includes = [
        result for result in results if result["fixture"].startswith("includes/")
    ]
    return {
        "python": sys.version.split()[0],
        "fixtures": results,
        "aggregate": {
            "fixture_count": len(results),
            "include_fixture_count": len(includes),
            "bytes": sum(result["bytes"] for result in results),
            "legacy_statements": sum(
                result["legacy"]["statement_count"] for result in results
            ),
            "legacy_comments": sum(
                result["legacy"]["comment_count"] for result in results
            ),
            "legacy_findings": sum(
                result["legacy"]["finding_count"] for result in results
            ),
            "legacy_finding_types": dict(aggregate_findings),
            "tree_boundary_spans": sum(
                result["tree_sitter"]["boundary_span_count"] for result in results
            ),
            "tree_exact_span_matches": sum(
                result["tree_sitter"]["exact_span_matches"] for result in results
            ),
            "tree_error_nodes": aggregate_errors["tree_error_nodes"],
            "tree_missing_nodes": aggregate_errors["tree_missing_nodes"],
            "tree_node_types": dict(aggregate_types),
            "tree_coverage": dict(aggregate_coverage),
            "macro_legacy": dict(aggregate_macro_legacy),
            "macro_tree_sitter": dict(aggregate_macro_tree),
            "includes_legacy_findings": sum(
                result["legacy"]["finding_count"] for result in includes
            ),
            "includes_tree_errors": sum(
                result["tree_sitter"]["error_count"] for result in includes
            ),
            "includes_tree_missing": sum(
                result["tree_sitter"]["missing_count"] for result in includes
            ),
        },
        "package_versions": {
            "tree_sitter_sas": TREE_SITTER_SAS_VERSION,
            "tree_sitter": TREE_SITTER_VERSION,
        },
    }


def _format_macro_counts(counts: Mapping[str, int]) -> str:
    return " / ".join(
        "{}={}".format(key, counts.get(key, 0))
        for key in ("definitions", "ends", "ifs", "includes", "calls", "variable_refs")
    )


def _markdown(payload: Mapping[str, Any]) -> str:
    aggregate = payload["aggregate"]
    lines = [
        "# Parser comparison harness output",
        "",
        "This output was generated by `tools/parser_compare.py` in a temporary "
        "virtualenv with `tree-sitter-sas=={}` and `tree-sitter=={}` on Python {}.".format(
            TREE_SITTER_SAS_VERSION,
            TREE_SITTER_VERSION,
            payload["python"],
        ),
        "",
        "## Corpus totals",
        "",
        "- Fixtures: **{}** (`includes/`: **{}**).".format(
            aggregate["fixture_count"], aggregate["include_fixture_count"]
        ),
        "- UTF-8 bytes: **{}**.".format(aggregate["bytes"]),
        "- Legacy statements/comments/findings: **{}/{}/{}**.".format(
            aggregate["legacy_statements"],
            aggregate["legacy_comments"],
            aggregate["legacy_findings"],
        ),
        "- Tree-sitter boundary spans / exact legacy token-byte-span matches: **{}/{}**.".format(
            aggregate["tree_boundary_spans"], aggregate["tree_exact_span_matches"]
        ),
        "- Tree-sitter error/missing nodes: **{}/{}**.".format(
            aggregate["tree_error_nodes"], aggregate["tree_missing_nodes"]
        ),
        "",
        "## Per-fixture boundaries, spans, and recovery",
        "",
        "`TS boundaries` counts deduplicated named grammar nodes whose type ends "
        "in `_statement` or `_header`, plus explicit macro/NULL nodes; it is "
        "not asserted to be the same abstraction as a legacy statement. "
        "`Exact token bytes` trims only leading horizontal indentation from the "
        "legacy raw span, then counts spans for which Tree-sitter has a named "
        "node with exactly the same UTF-8 byte range.",
        "",
        "| Fixture | Bytes | Legacy statements | TS boundaries | Exact token bytes | Legacy findings | TS error/missing |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in payload["fixtures"]:
        tree = result["tree_sitter"]
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {}/{} |".format(
                result["fixture"],
                result["bytes"],
                result["legacy"]["statement_count"],
                tree["boundary_span_count"],
                tree["exact_span_matches"],
                result["legacy"]["finding_count"],
                tree["error_count"],
                tree["missing_count"],
            )
        )

    lines.extend(
        [
            "",
            "### Unmatched legacy spans",
            "",
            "The following legacy statement spans had no exact named Tree-sitter "
            "node; offsets are UTF-8 byte offsets and are recorded by the harness.",
            "",
        ]
    )
    for result in payload["fixtures"]:
        unmatched = result["tree_sitter"]["unmatched_legacy"]
        if not unmatched:
            continue
        preview = "; ".join(
            "{}-{} `{}`".format(
                item["token_start_byte"],
                item["token_end_byte"],
                item["text"].replace("\n", "\\n")[:80],
            )
            for item in unmatched[:5]
        )
        suffix = "" if len(unmatched) <= 5 else " (+{} more)".format(len(unmatched) - 5)
        lines.append("- `{}`: {}{}".format(result["fixture"], preview, suffix))

    lines.extend(
        [
            "",
            "## Includes recovery",
            "",
            "| Include fixture | Legacy statements | Legacy finding types | TS errors | TS missing |",
            "|---|---:|---|---:|---:|",
        ]
    )
    for result in payload["fixtures"]:
        if not result["fixture"].startswith("includes/"):
            continue
        lines.append(
            "| `{}` | {} | `{}` | {} | {} |".format(
                result["fixture"],
                result["legacy"]["statement_count"],
                json.dumps(result["legacy"]["finding_types"], sort_keys=True),
                result["tree_sitter"]["error_count"],
                result["tree_sitter"]["missing_count"],
            )
        )
    lines.extend(
        [
            "",
            "Include totals: legacy findings **{}**, Tree-sitter error nodes **{}**, "
            "missing nodes **{}**.".format(
                aggregate["includes_legacy_findings"],
                aggregate["includes_tree_errors"],
                aggregate["includes_tree_missing"],
            ),
            "",
            "## Macro behavior",
            "",
            "| Fixture | Legacy scanner markers | Tree-sitter named macro nodes |",
            "|---|---|---|",
        ]
    )
    for result in payload["fixtures"]:
        legacy = result["macro"]["legacy"]
        tree = result["macro"]["tree_sitter"]
        if any(legacy.values()) or any(tree.values()):
            tree_text = (
                ", ".join(
                    "{}={}".format(key, value) for key, value in tree.items() if value
                )
                or "none"
            )
            lines.append(
                "| `{}` | `{}` | `{}` |".format(
                    result["fixture"], _format_macro_counts(legacy), tree_text
                )
            )
    lines.extend(
        [
            "",
            "Corpus macro totals: legacy `{}`; Tree-sitter `{}`.".format(
                _format_macro_counts(aggregate["macro_legacy"]),
                ", ".join(
                    "{}={}".format(key, value)
                    for key, value in aggregate["macro_tree_sitter"].items()
                    if value
                )
                or "none",
            ),
            "",
            "## Existing IR reachability and typed syntax",
            "",
            "| Fixture | Current IR assignment candidates/parsed/unknown | Current IR SQL candidates/parsed/errors | TS typed coverage |",
            "|---|---:|---:|---|",
        ]
    )
    for result in payload["fixtures"]:
        ir = result["legacy"]["ir"]
        coverage = result["tree_sitter"]["coverage"]
        typed = ", ".join(
            "{}={}".format(key, value) for key, value in coverage.items() if value
        )
        lines.append(
            "| `{}` | {}/{}/{} | {}/{}/{} | `{}` |".format(
                result["fixture"],
                ir["assignment_candidates"],
                ir["assignment_parsed"],
                ir["assignment_unknown_expression"],
                ir["sql_candidates"],
                ir["sql_parsed"],
                ir["sql_errors"],
                typed or "none",
            )
        )
    lines.extend(
        [
            "",
            "Tree-sitter typed coverage totals: `{}`.".format(
                ", ".join(
                    "{}={}".format(key, value)
                    for key, value in aggregate["tree_coverage"].items()
                    if value
                )
                or "none"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _parent_run(args: argparse.Namespace, repo_root: Path) -> int:
    if sys.version_info < (3, 10):
        return _skip(
            "the optional parser requires Python >=3.10; this harness is running "
            "under {}.".format(sys.version.split()[0])
        )

    try:
        with tempfile.TemporaryDirectory(prefix="sas-graph-parser-") as temp_dir:
            env_dir = Path(temp_dir) / "venv"
            venv.EnvBuilder(with_pip=True, clear=True).create(str(env_dir))
            python = env_dir / (
                "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            )
            install = _run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--only-binary=:all:",
                    *PARSER_REQUIREMENTS,
                ]
            )
            if install.returncode != 0:
                return _skip(
                    "tree-sitter-sas installation failed in the throwaway virtualenv; "
                    "the comparison was not run. Exact pip output:",
                    install.stdout,
                )
            child = _run(
                [
                    str(python),
                    str(Path(__file__).resolve()),
                    "--child",
                    "--repo-root",
                    str(repo_root),
                    "--format",
                    "json",
                ]
            )
            if child.returncode != 0:
                return _skip(
                    "tree-sitter-sas installed, but the isolated comparison failed; "
                    "exact child output:",
                    child.stdout,
                )
            try:
                payload = json.loads(child.stdout)
            except json.JSONDecodeError:
                return _skip(
                    "the isolated comparison returned invalid JSON; exact child output:",
                    child.stdout,
                )
            payload["host"] = {
                "platform": sys.platform,
                "python": sys.version.split()[0],
                "installation": "success",
                "wheel_only": True,
            }
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_markdown(payload))
            return 0
    except Exception as error:  # pragma: no cover - platform/bootstrap guard
        return _skip(
            "could not build the throwaway virtualenv; the comparison was not run. "
            "Exact error: {}: {}".format(type(error).__name__, error)
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    repo_root = (args.repo_root or Path(__file__).resolve().parents[1]).resolve()
    if args.child:
        sys.path.insert(0, str(repo_root / "src"))
        payload = _child_payload(repo_root)
        print(json.dumps(payload, sort_keys=True))
        return 0
    return _parent_run(args, repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
