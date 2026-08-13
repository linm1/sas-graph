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
from .macro_state import LetEvent, expand_includes, resolve_text, walk_runtime
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
_SETUP_MACRO_HEADER_RE = re.compile(
    r"^%macro\s+(?P<name>[A-Za-z_]\w*)\s*"
    r"(?:(?P<parameters>\([^;]*\))|/(?P<options>[^;]*))?\s*;$",
    re.IGNORECASE | re.DOTALL,
)
_BARE_MACRO_CALL_RE = re.compile(
    r"^%(?P<name>[A-Za-z_]\w*)\s*;$", re.IGNORECASE
)
_GM_CALL_NAME_RE = re.compile(r"%(gm\w+)", re.IGNORECASE)


def _called_gm_macro_names(config_result, source_paths=None):
    """Pre-pass for lazy contract loading: every distinct `%gm...` name
    appearing anywhere in any declared main program, setup file, or `.sas`
    files under `macro_roots` -- no `%include` expansion, no macro-variable
    resolution, comments and inactive branches included (over-fetch is fine,
    a false positive here just loads one extra contract that goes unused).

    Scans *every* declared program, not just the first: a `%gm...` macro
    called only by the second or third declared program must still resolve
    through its contract in the merged graph.

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

    for main_program in config_result.main_programs:
        _scan(main_program)
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
    prior_events=(), source_paths=None, is_setup=False, os_fvars_base=None,
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

    Ticket 04: a lineage walk that reaches a Step or a MacroCall can already
    name the program that owns it in one hop -- `contains_step` for Steps,
    `calls_macro` for MacroCalls (both edges originate at `container_node_id`
    above) -- with no new graph structure or helper needed.
    """
    expanded = _read_and_split(path, allowed_roots, source_paths)
    macro_definition_orders, macro_definition_spans = _macro_definition_spans(
        expanded.statements
    )
    file_macro_index = merge_inline_sites(
        macro_index, build_inline_macro_index(expanded.statements, path)
    )
    runtime_excluded_orders = set(macro_definition_orders)
    dropped_call_orders = set()
    if is_setup:
        statements_by_order = {
            statement.statement_order: statement for statement in expanded.statements
        }
        for line_start, line_end, opener_order, mend_order in macro_definition_spans:
            opener = statements_by_order.get(opener_order)
            header = opener and _SETUP_MACRO_HEADER_RE.match(opener.text)
            if header is None or header.group("parameters") is not None:
                continue

            nested_orders = set()
            depth = 1
            for statement in expanded.statements:
                order = statement.statement_order
                if not opener_order < order < mend_order:
                    continue
                if _MACRO_DEFINITION_START_RE.match(statement.text):
                    depth += 1
                    if depth > 1:
                        nested_orders.add(order)
                elif depth > 1:
                    nested_orders.add(order)
                if depth > 1 and _MACRO_DEFINITION_END_RE.match(statement.text):
                    depth -= 1

            call_orders = {
                statement.statement_order
                for statement in expanded.statements
                if statement.statement_order not in macro_definition_orders
                and (call := _BARE_MACRO_CALL_RE.match(statement.text)) is not None
                and call.group("name").lower() == header.group("name").lower()
            }
            if not call_orders:
                continue

            runtime_excluded_orders.difference_update(
                statement.statement_order
                for statement in expanded.statements
                if opener_order < statement.statement_order < mend_order
                and statement.statement_order not in nested_orders
            )
            dropped_call_orders.update(call_orders)
            ctx.add_finding(
                "setup_macro_wrapper_unwrapped",
                "SUPPORTED",
                "INFORMATION",
                header.group("name"),
                f"Setup macro wrapper `%{header.group('name')}` was unwrapped; "
                "its body was executed inline.",
                None,
                opener.as_source("setup_macro_wrapper_unwrapped"),
            )
            break

    runtime_statements = [
        statement for statement in expanded.statements
        if statement.statement_order not in runtime_excluded_orders
        and statement.statement_order not in dropped_call_orders
    ]
    ctx.add_existing_findings([
        finding for finding in expanded.findings
        if finding["source"]["statement_order"] not in runtime_excluded_orders
    ])

    surviving_statements, events, runtime_findings, skipped_orders = walk_runtime(
        runtime_statements,
        initial_events=prior_events,
        os_fvars_base=os_fvars_base,
    )
    local_events = events[len(prior_events):]
    ctx.add_existing_findings(runtime_findings)

    runtime_comments = [
        comment for comment in expanded.comments
        if not _comment_in_macro_definition(comment, macro_definition_spans)
        and comment.after_statement_order not in skipped_orders
    ]
    blocks, unattached = group_blocks(surviving_statements)
    blocks_by_order = {
        block.statements[-1].statement_order: block for block in blocks
    }
    unattached_orders = {statement.statement_order for statement in unattached}
    steps_before = {n["id"] for n in ctx.nodes if n["type"] in ("Step", "SqlBlock")}

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

    for statement in surviving_statements:
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

    # codex-review-class fix (found while building the variable-lineage
    # acceptance fixture): `SqlBlock` never got a `contains_step` edge here,
    # so `variable_lineage_walk._owning_program`'s
    # `SqlStatement -> contains_sql_statement -> SqlBlock -> contains_step ->
    # Program` reverse hop (wayfinder:
    # variable-impact-reference-walk-algorithm) always resolved `program:
    # null` for any reached `SqlStatement` -- not a walk bug, a missing edge.
    # `SqlBlock` is not literally a `Step`, but it reuses the same edge type
    # deliberately: it is a direct child of `container_node_id` exactly like
    # a `Step` is, and the walk's reverse-hop lookup already treats
    # `contains_step` as "whatever this Program/SetupFile directly parsed",
    # not "specifically a Step" -- confirmed by reading
    # `variable_lineage_walk.py` before this fix, not assumed.
    new_step_ids = [
        n["id"] for n in ctx.nodes
        if n["type"] in ("Step", "SqlBlock") and n["id"] not in steps_before
    ]
    for step_id in new_step_ids:
        ctx.add_edge("contains_step", container_node_id, step_id, source=None)

    return local_events


def _report_setup_rebind(ctx, main_name, event, setup_events, var_id):
    """Section 9 step 11 gap (wayfinder: per-program-macro-state-isolation):
    a program rebinding a value setup.sas already declared is not an error --
    real SAS allows shadowing freely -- but is exactly the kind of divergence
    a reader comparing programs side by side benefits from seeing named.
    Fires only when the value actually diverges from setup's own *last*
    binding for that name (a same-value rebind is a no-op, not a finding).
    """
    setup_value = None
    for setup_event in setup_events:
        if setup_event.name.lower() == event.name.lower():
            setup_value = setup_event.value
    if setup_value is None or setup_value == event.value:
        return
    ctx.add_finding(
        "main_program_rebinds_setup_macro_variable",
        "SUPPORTED",
        "INFORMATION",
        event.name,
        f"`{main_name}` rebinds macro variable `{event.name}` to "
        f"`{event.value}`; setup.sas declared it as `{setup_value}`.",
        None,
        event.source,
        affected_nodes=[var_id],
    )


def _report_read_but_never_written(ctx):
    """Ticket 03: a dataset read anywhere in the finished graph but written by
    no parsed program is expected, correct behavior -- a QC compare target,
    an SDTM raw source, anything of the kind -- not a defect, so it gets a
    plain, non-degrading finding instead of dangling with no explanation for
    why lineage stops there. Fires uniformly for every such dataset: no
    scoping by library name or reader count, since guessing which datasets
    are "supposed to" be external is exactly what this parser must never do.
    Runs once over the fully merged node/edge set (after every declared
    program has parsed), not per rule module -- it needs the complete
    read/write picture, which no single `rules_*.py` ever has on its own.
    """
    written = {
        edge["to"]
        for edge in ctx.edges
        if edge["type"] in ("writes_dataset", "reads_external_file")
    }
    first_read_edge = {}
    for edge in ctx.edges:
        if edge["type"] == "reads_dataset":
            first_read_edge.setdefault(edge["from"], edge)
    dataset_labels = {
        node["id"]: node["label"] for node in ctx.nodes if node["type"] == "Dataset"
    }
    for dataset_node_id, edge in first_read_edge.items():
        if dataset_node_id in written or dataset_node_id not in dataset_labels:
            continue
        label = dataset_labels[dataset_node_id]
        ctx.add_finding(
            "dataset_read_never_written",
            "SUPPORTED",
            "INFORMATION",
            label,
            f"`{label}` is read but never written by any parsed program; "
            "lineage stops here because it is an external input to this "
            "parsed scope.",
            None,
            {**edge["source"], "rule": "dataset_read_never_written"},
            affected_nodes=[dataset_node_id],
        )


def run(config_result, run_id, source_paths=None):
    """Execute section 9 end to end and return the assembled graph dict.

    Caller (cli.py) owns save -> reload -> render per section 4.2; this
    function only builds the in-memory graph and hands it back.

    N declared programs parse into one shared `GraphContext` (wayfinder:
    per-program-macro-state-isolation, merged-and-per-program-run-layout) --
    no separate merge step: node/edge/finding id counters already live on
    the one shared instance, so a loop over `_process_file` calls produces a
    correctly merged graph for free, the same way the single-program path
    always has.
    """
    setup_name = config_result.setup_file.name

    ctx = GraphContext(
        main_programs=[p.name for p in config_result.main_programs],
        setup_file=setup_name, run_id=run_id,
    )
    ctx.add_existing_findings(config_result.findings)
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
    # declares exists before any main program is parsed. Its own Steps/calls
    # are contained by the SetupFile node, not a Program node.
    setup_events = _process_file(
        config_result.setup_file, ctx, config_result.allowed_roots,
        macro_index, macro_contracts, setup_node_id,
        source_paths=source_paths, is_setup=True,
        os_fvars_base=config_result.os_fvars_base,
    )
    for event in setup_events:
        var_id = f"macrovar:{event.name}@{event.statement_order}"
        ctx.add_node(
            var_id, "MacroVariable", event.name, value=event.value,
            source=event.source,
        )
        ctx.add_edge("defines_macro_variable", setup_node_id, var_id, source=None)

    _prior_sort_context(ctx)
    # Snapshot setup's own (now prior-timestamped) sort evidence once. Each
    # program below gets a fresh copy of exactly this snapshot rather than
    # the live, accumulating dicts -- sort_by_at/sort_by_of are structural
    # evidence about *this parse's* datasets, not a final global table any
    # more than %let state is (section 11.3); without this reset, program 2
    # would see program 1's own PROC SORT evidence as if it preceded its own
    # statements, misfiring (or missing) the merge-by-prefix check purely
    # because both programs' statement_order numbering restarts at 1.
    setup_sort_by_at = {key: list(value) for key, value in ctx.sort_by_at.items()}
    setup_sort_by_of = dict(ctx.sort_by_of)

    # Section 9 step 11, looped: each declared program parses next, seeded
    # with setup's already-completed %let bindings and sort evidence, with
    # its own body fully isolated from every other program's. Its own
    # Steps/calls are contained by its own Program node.
    for index, main_program in enumerate(config_result.main_programs):
        main_name = main_program.name
        # Declaration-index-qualified id (wayfinder: per-program-macro-state-
        # isolation, same convention as cli.py's debug-directory naming):
        # two declared programs can share a basename from different
        # directories, and a bare `main_name` id would collide via
        # add_node's generic id-based dedup, silently discarding the
        # second program's Program node and macro-variable state.
        program_key = f"{index:03d}_{main_name}"
        program_node_id = f"program:{program_key}"
        ctx.add_node(
            program_node_id, "Program", main_name,
            source={
                "file": main_name, "line_start": 1, "line_end": 1,
                "statement_order": None, "original_text": None, "rule": "main_program",
            },
        )

        ctx.sort_by_at = {key: list(value) for key, value in setup_sort_by_at.items()}
        ctx.sort_by_of = dict(setup_sort_by_of)

        local_events = _process_file(
            main_program, ctx, config_result.allowed_roots,
            macro_index, macro_contracts, program_node_id,
            _prior_context(setup_events), source_paths,
            os_fvars_base=config_result.os_fvars_base,
        )
        for event in local_events:
            # Program-qualified id (wayfinder: per-program-macro-state-
            # isolation): two programs each declaring `%let domain = ...` at
            # the same local statement_order would otherwise collide into
            # one node via add_node's generic id-based dedup.
            var_id = f"macrovar:{program_key}:{event.name}@{event.statement_order}"
            ctx.add_node(
                var_id, "MacroVariable", event.name, value=event.value,
                source=event.source,
            )
            ctx.add_edge("defines_macro_variable", program_node_id, var_id, source=None)
            _report_setup_rebind(ctx, main_name, event, setup_events, var_id)

    _report_read_but_never_written(ctx)

    return ctx.to_graph()
