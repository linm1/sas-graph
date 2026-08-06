"""End-to-end run orchestration (dev plan section 9, 21 Phase 4).

The only thing this module does that a rule module cannot: sequence section
9's steps in order and own the one `GraphContext` every rule module mutates.
Kept separate from `cli.py` so it can be tested without going through
argparse, and separate from any single rule module because no rule module
should know about the other four.

Setup `%let` events are passed to the main program as prior context, while
each file retains its own local statement-order evidence. This is not a final
global macro table: each use resolves against only the prior setup context and
the events that precede it in its own file.
"""

import re
from pathlib import Path

from ._paths import prohibited_reason as _prohibited_reason
from .blocks import group_blocks
from .graph_model import GraphContext
from .macro_contracts import load_macro_contracts
from .macro_index import build_inline_macro_index, build_macro_index, merge_inline_sites
from .macro_state import LetEvent, expand_includes, resolve_text, walk_let_statements
from .rules_data_step import apply as apply_data_step
from .rules_inactive import apply as apply_inactive
from .rules_libname import apply as apply_libname
from .rules_libname import is_libname
from .rules_macro_call import apply as apply_macro_call
from .rules_macro_call import is_conditional, is_macro_call
from .rules_proc_export import apply as apply_proc_export
from .rules_proc_import import apply as apply_proc_import
from .rules_proc_sort import apply as apply_proc_sort
from .rules_proc_sql import apply as apply_proc_sql
from .statements import split_statements
from .source_snapshot import read_text


_MACRO_DEFINITION_START_RE = re.compile(r"^%macro\b", re.IGNORECASE)
_MACRO_DEFINITION_END_RE = re.compile(r"^%mend\b", re.IGNORECASE)
_GM_CALL_NAME_RE = re.compile(r"%(gm\w+)", re.IGNORECASE)


def _called_gm_macro_names(config_result, source_paths=None):
    """Pre-pass for lazy contract loading: every distinct `%gm...` name
    appearing anywhere in the main program, setup file, or `.sas` files
    under `macro_roots` -- no `%include` expansion, no macro-variable
    resolution, comments and inactive branches included (over-fetch is fine,
    a false positive here just loads one extra contract that goes unused).

    File selection under `macro_roots` mirrors `build_macro_index`: `.sas`
    files found via `rglob`, prohibited paths skipped before reading.
    """
    names = set()

    def _scan(path):
        try:
            text = read_text(path, "utf-8-sig", source_paths)
        except (OSError, UnicodeDecodeError):
            return
        names.update(match.group(1) for match in _GM_CALL_NAME_RE.finditer(text))

    _scan(config_result.main_program)
    if config_result.setup_file:
        _scan(config_result.setup_file)
    for root in config_result.macro_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.sas")):
            if _prohibited_reason(path) is not None:
                continue
            _scan(path)

    return names


def _read_and_split(path, allowed_roots, source_paths=None):
    """Section 9 steps 4-6 / 11-12: split, then expand %include in place."""
    text = read_text(path, "utf-8-sig", source_paths)
    split = split_statements(text, path.name)
    return expand_includes(
        split.statements,
        split.comments,
        allowed_roots,
        base_dir=path.parent,
        findings=split.findings,
        source_paths=source_paths,
    )


def _prior_context(events):
    """Make completed setup events visible before every main statement."""
    return [
        LetEvent(event.name, event.value, index - len(events), event.source)
        for index, event in enumerate(events)
    ]


def _prior_sort_context(ctx):
    """Place setup sort evidence before every main-program statement."""
    for history in ctx.sort_by_at.values():
        history[:] = [
            (index - len(history), by_vars)
            for index, (_order, by_vars) in enumerate(history)
        ]


def _macro_definition_spans(statements):
    """Statement orders and source spans occupied by macro definitions."""
    orders = set()
    spans = []
    depth = 0
    opener = None
    for statement in statements:
        if _MACRO_DEFINITION_START_RE.match(statement.text):
            if depth == 0:
                opener = statement
            depth += 1
        if depth:
            orders.add(statement.statement_order)
        if depth and _MACRO_DEFINITION_END_RE.match(statement.text):
            depth -= 1
            if depth == 0:
                spans.append((
                    opener.line_start,
                    statement.line_end,
                    opener.statement_order,
                    statement.statement_order,
                ))
                opener = None
    if depth and opener is not None:
        spans.append((
            opener.line_start,
            statements[-1].line_end,
            opener.statement_order,
            statements[-1].statement_order,
        ))
    return orders, spans


def _comment_in_macro_definition(comment, spans):
    return any(
        line_start <= comment.line_start <= line_end
        and opener_order <= comment.after_statement_order < mend_order
        for line_start, line_end, opener_order, mend_order in spans
    )


def _process_file(
    path, ctx, allowed_roots, macro_index, macro_contracts, container_node_id,
    prior_events=(), source_paths=None,
):
    """Section 9 steps 8-15 applied to one file's already-spliced statements.

    `container_node_id` is the node this file's own Steps/MacroCalls are
    contained by -- the SetupFile node for setup.sas, the Program node for
    the main program (section 10.1 models them as distinct node types, so
    containment edges must originate from whichever one actually parsed the
    statement, not always the Program node).

    Returns the file's own `%let` events and the Step ids it created (for the
    caller to wire `contains_step`). `prior_events` are already-completed
    setup bindings, visible before every statement in this file.
    """
    expanded = _read_and_split(path, allowed_roots, source_paths)
    macro_definition_orders, macro_definition_spans = _macro_definition_spans(
        expanded.statements
    )
    file_macro_index = merge_inline_sites(
        macro_index, build_inline_macro_index(expanded.statements, path)
    )
    runtime_statements = [
        statement for statement in expanded.statements
        if statement.statement_order not in macro_definition_orders
    ]
    runtime_comments = [
        comment for comment in expanded.comments
        if not _comment_in_macro_definition(comment, macro_definition_spans)
    ]
    ctx.add_existing_findings([
        finding for finding in expanded.findings
        if finding["source"]["statement_order"] not in macro_definition_orders
    ])

    local_events = walk_let_statements(
        runtime_statements, initial_events=prior_events
    )
    events = [*prior_events, *local_events]
    ctx.add_existing_findings(
        walk_let_statements(runtime_statements, findings_only=True)
    )

    blocks, unattached = group_blocks(runtime_statements)
    blocks_by_order = {
        block.statements[-1].statement_order: block for block in blocks
    }
    unattached_orders = {statement.statement_order for statement in unattached}
    steps_before = {n["id"] for n in ctx.nodes if n["type"] == "Step"}

    def apply_block(block):
        if block.kind == "DATA":
            apply_data_step(block, ctx, events)
        elif block.proc_name == "sort":
            apply_proc_sort(block, ctx, events)
        elif block.proc_name == "sql":
            apply_proc_sql(block, ctx, events)
        elif block.proc_name == "import":
            apply_proc_import(block, ctx, events)
        elif block.proc_name == "export":
            apply_proc_export(block, ctx, events)

        if not block.terminated and block.proc_name != "sql":
            # Section 14.6's recovery is specific to PROC SQL's own missing-QUIT
            # case, which rules_proc_sql.py already reports; every other block
            # kind reaching here unterminated (DATA missing RUN, PROC SORT
            # missing RUN) gets the same "recovered, not fatal" treatment.
            ctx.add_finding(
                "block_not_explicitly_closed",
                "NOT_EXECUTED",
                "WARNING",
                block.opener.text,
                "This block has no closing RUN statement; parsing recovered "
                "at the next DATA/PROC boundary or end of file.",
                "Add the missing RUN statement.",
                block.as_source("block_not_explicitly_closed"),
            )

    for statement in runtime_statements:
        block = blocks_by_order.get(statement.statement_order)
        if block is not None:
            apply_block(block)

        if statement.statement_order in unattached_orders and is_libname(statement.text):
            apply_libname(statement, ctx, events)
        elif (
            statement.statement_order in unattached_orders and is_conditional(statement.text)
        ) or is_macro_call(statement.text):
            apply_macro_call(
                statement, ctx, events, file_macro_index, macro_contracts, container_node_id,
                program_path=path,
            )
        elif (
            statement.statement_order in unattached_orders
            and statement.text.startswith("&")
        ):
            # A bare macro-variable-reference statement (e.g. `&&mapcode_&domain.;`):
            # its full expansion is genuinely unknowable statically (section 3.2's
            # `&&` exclusion is deliberate), but its *existence* must not vanish
            # silently. `resolve_text` only improves the finding's own message --
            # the (partially) resolved text is never fed back into dispatch, so
            # this never executes macro-generated code, only reports it accurately.
            # codex #7: when it fully resolves -- no unresolved names, and no
            # `&` reference syntax (e.g. the unhandled `&&` form) left over --
            # it is not genuinely unknowable, so no finding is emitted at all.
            resolved_text, unresolved = resolve_text(
                statement.text, statement.statement_order, events, return_unresolved=True
            )
            if unresolved or "&" in resolved_text:
                if resolved_text != statement.text:
                    detail = (
                        f"After static macro-variable substitution it reads "
                        f"`{resolved_text}`; it was not re-interpreted as a new statement."
                    )
                else:
                    detail = "Its expansion is not known statically."
                ctx.add_finding(
                    "unresolved_macro_generated_statement",
                    "NOT_EXECUTED",
                    "INFORMATION",
                    statement.text,
                    f"This statement is a bare macro-variable reference. {detail}",
                    "Review the statement manually to confirm what it expands to.",
                    statement.as_source("unresolved_macro_generated_statement"),
                )

    apply_inactive(runtime_comments, ctx)

    new_step_ids = [
        n["id"] for n in ctx.nodes if n["type"] == "Step" and n["id"] not in steps_before
    ]
    for step_id in new_step_ids:
        ctx.add_edge("contains_step", container_node_id, step_id, source=None)

    return local_events


def run(config_result, run_id, source_paths=None):
    """Execute section 9 end to end and return the assembled graph dict.

    Caller (cli.py) owns save -> reload -> render per section 4.2; this
    function only builds the in-memory graph and hands it back.
    """
    main_name = config_result.main_program.name
    setup_name = config_result.setup_file.name

    ctx = GraphContext(main_program=main_name, setup_file=setup_name, run_id=run_id)
    ctx.add_existing_findings(config_result.findings)
    program_node_id = f"program:{main_name}"
    ctx.add_node(
        program_node_id, "Program", main_name,
        source={
            "file": main_name, "line_start": 1, "line_end": 1,
            "statement_order": None, "original_text": None, "rule": "main_program",
        },
    )
    setup_node_id = f"setup:{setup_name}"
    ctx.add_node(
        setup_node_id, "SetupFile", setup_name,
        source={
            "file": setup_name, "line_start": 1, "line_end": 1,
            "statement_order": None, "original_text": None, "rule": "declared_setup_file",
        },
    )

    macro_index = build_macro_index(config_result.macro_roots, source_paths)
    called_macro_names = _called_gm_macro_names(config_result, source_paths)
    macro_contracts = load_macro_contracts(
        config_result.macro_contracts, source_paths, called_macro_names,
    )

    # Section 9 step 4: setup.sas first, so LIBNAME/macro-variable evidence it
    # declares exists before the main program is parsed. Its own Steps/calls
    # are contained by the SetupFile node, not the Program node.
    setup_events = _process_file(
        config_result.setup_file, ctx, config_result.allowed_roots,
        macro_index, macro_contracts, setup_node_id,
        source_paths=source_paths,
    )
    for event in setup_events:
        var_id = f"macrovar:{event.name}@{event.statement_order}"
        ctx.add_node(
            var_id, "MacroVariable", event.name, value=event.value,
            source=event.source,
        )
        ctx.add_edge("defines_macro_variable", setup_node_id, var_id, source=None)

    _prior_sort_context(ctx)

    # Section 9 step 11: main program parses next. Its own Steps/calls are
    # contained by the Program node.
    _process_file(
        config_result.main_program, ctx, config_result.allowed_roots,
        macro_index, macro_contracts, program_node_id, _prior_context(setup_events), source_paths,
    )

    return ctx.to_graph()
