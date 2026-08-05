"""Visible macro call rule (dev plan section 15.1-15.6, 21 Phase 4).

`calls_macro` from the program and `passes_parameter` to each
`MacroParameter`. A unique source definition is represented by
`MacroDefinition`/`MacroSourceFile` evidence and its simple structural body is
bound without executing SAS. A missing source may instead match a
md-parsed `MacroContract` (wayfinder: bind-macro-calls-to-md-contracts.md --
purpose/parameter-match evidence only, no dataset edges); otherwise it
reaches an `UnknownMacro` node with no invented dependency. A parameter
whose value fails to resolve reaches an
`UnknownDataset` through `resolves_to`, hopping over the parameter node the
same way the fixture and `renderer_mermaid._flow_edges` already do -- this
module is what actually draws that edge, not just what the renderer expects.

Section 15.8-15.10 permits only simple resolved `%if` branches and bounded
structural loops. All other macro control flow remains evidence-only and is
marked `NOT_EXECUTED`; a bare `%if`/`%do` statement reaching this module does
not create dependencies.
"""

import re
from dataclasses import replace

from .blocks import group_blocks
from .macro_state import resolve_text
from .rules_data_step import apply as apply_data_step
from .rules_proc_import import apply as apply_proc_import
from .rules_proc_sort import apply as apply_proc_sort
from .rules_proc_sql import apply as apply_proc_sql

_MACRO_CALL_RE = re.compile(r"^%(\w+)\s*(?:\((.*)\))?\s*;?$", re.DOTALL)
_RESERVED_NAMES = {
    "let", "put", "include", "macro", "mend", "if", "then", "else", "do", "end",
    "return", "abort",
}
_CONDITIONAL_RE = re.compile(r"^%(if|do)\b", re.IGNORECASE)
_PARAM_SPLIT_RE = re.compile(r",(?![^(]*\))")  # split on top-level commas only
_PARAM_REF_RE = re.compile(r"(?<!&)&(?!&)([A-Za-z_][A-Za-z0-9_]*)\.?")
_IMPORT_DATAFILE_RE = re.compile(r'\bdatafile\s*=\s*(["\'])(.*?)\1', re.IGNORECASE)
_UNSUPPORTED_TEMPLATE_RE = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE)
_IF_RE = re.compile(
    r"^%if\s+(.+?)\s*(=|!=|~=|\beq\b|\bne\b)\s*(.*?)\s+%then\s+%do\s*;?$",
    re.IGNORECASE,
)
_INTEGER_LOOP_RE = re.compile(
    r"^%do\s+([A-Za-z_]\w*)\s*=\s*(\d+)\s+%to\s+(\d+)\s*;?$", re.IGNORECASE
)
_LIST_LOOP_RE = re.compile(
    r"^%do\s+([A-Za-z_]\w*)\s*=\s*1\s+%to\s+%sysfunc\s*\(\s*countw\s*\(\s*&([A-Za-z_]\w*)\.?\s*\)\s*\)\s*;?$",
    re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(
    r"^%let\s+([A-Za-z_]\w*)\s*=\s*%scan\s*\(\s*&([A-Za-z_]\w*)\.?\s*,\s*&([A-Za-z_]\w*)\.?\s*\)\s*;?$",
    re.IGNORECASE,
)
_MACRO_OPERAND_RE = re.compile(
    r"%(?:[A-Za-z_]\w*\s*\(|if\b|then\b|else\b|do\b|end\b)", re.IGNORECASE
)
_COMPOUND_OPERAND_RE = re.compile(r"\b(?:and|or|eq|ne)\b|[()=~!]", re.IGNORECASE)
_MAX_STATIC_LOOP_ITERATIONS = 20


def is_macro_call(statement_text):
    match = _MACRO_CALL_RE.match(statement_text)
    return match is not None and match.group(1).lower() not in _RESERVED_NAMES


def is_conditional(statement_text):
    return _CONDITIONAL_RE.match(statement_text) is not None


def _external_references_outside_import_datafiles(blocks):
    """Keep template binding strict except for PROC IMPORT's own DATAFILE=."""
    references = set()
    for block in blocks:
        text = "\n".join(statement.text for statement in block.statements)
        if block.proc_name == "import":
            text = _IMPORT_DATAFILE_RE.sub(lambda match: " " * len(match.group()), text)
        references.update(match.group(1).lower() for match in _PARAM_REF_RE.finditer(text))
    return references


def _split_params(raw):
    if not raw.strip():
        return []
    return [p.strip() for p in _PARAM_SPLIT_RE.split(raw) if p.strip()]


def apply(
    statement, ctx, let_events, macro_index, macro_contracts, program_node_id,
    program_path=None,
):
    """Bind one `%macro_name(...)` call. Mutates ctx.

    `program_node_id` is the calling Program node's id -- section 10.2's
    `calls_macro` edge always originates there in the section 22 fixture,
    never from a Step (a macro call is a top-level statement in this phase's
    scope, not nested inside a DATA step body).

    `program_path` is the resolved path of the file currently being
    processed (main Program or SetupFile), passed by
    `run_pipeline._process_file`. When a matched definition's own site path
    equals it, the macro is defined inline in the very file already modeled
    by `program_node_id` -- a second `MacroSourceFile` node for that same
    file would only duplicate it, so `defined_in` points at `program_node_id`
    instead. `None` (e.g. tests that call this rule directly) keeps the
    original always-create behavior.
    """
    if is_conditional(statement.text):
        ctx.add_finding(
            "macro_conditional_not_executed",
            "NOT_EXECUTED",
            "INFORMATION",
            statement.text,
            "Macro conditional (%IF/%DO) branches were not executed statically.",
            "Review branches manually; static resolution is out of scope.",
            statement.as_source("macro_conditional_not_executed"),
        )
        return

    match = _MACRO_CALL_RE.match(statement.text)
    if not match or match.group(1).lower() in _RESERVED_NAMES:
        return

    macro_name = match.group(1)
    call_number = sum(node["type"] == "MacroCall" for node in ctx.nodes) + 1
    call_id = f"macrocall:{call_number:03d}"
    ctx.add_node(
        call_id, "MacroCall", f"%{macro_name}", macro_name=macro_name,
        source=statement.as_source("macro_call"),
    )
    ctx.add_edge("calls_macro", program_node_id, call_id, statement.as_source("macro_call"))

    # Section 15.7's forward-resolution gate: real SAS compiles a %macro when
    # its %macro statement is reached, so a call written textually before its
    # own same-file (inline) definition must not resolve against it -- that
    # definition hasn't been "compiled" yet at the point of the call. A
    # macro_roots-sourced definition has no ordering relative to the call
    # site at all (`definition_order is None`), so it is never gated.
    sites = [
        site for site in macro_index.sites_for(macro_name)
        if site.definition_order is None or site.definition_order < statement.statement_order
    ]
    definition = sites[0] if len(sites) == 1 else None
    contract = macro_contracts.get(macro_name)
    param_ids = {}
    for raw_param in _split_params(match.group(2) or ""):
        if "=" not in raw_param:
            continue
        name, raw_value = (p.strip() for p in raw_param.split("=", 1))
        resolved_value, unresolved = resolve_text(
            raw_value, statement.statement_order, let_events, return_unresolved=True
        )
        param_name = name.lower()
        param_id = f"macroparam:{call_number:03d}:{param_name}"
        ctx.add_node(
            param_id, "MacroParameter", name,
            raw_value=raw_value,
            resolved_value=None if unresolved else resolved_value,
            source=statement.as_source("macro_parameter"),
        )
        ctx.add_edge(
            "passes_parameter", call_id, param_id, statement.as_source("macro_parameter")
        )
        param_ids[param_name] = (param_id, resolved_value, unresolved)

        if unresolved:
            unknown_id = ctx.add_unknown_dataset(raw_value, statement.as_source(
                "unresolved_macro_variable_in_parameter"
            ))
            ctx.add_edge(
                "resolves_to", param_id, unknown_id,
                statement.as_source("unresolved_macro_variable_in_parameter"),
            )
            ctx.add_finding(
                "unresolved_macro_variable",
                "UNRESOLVED_MACRO_VARIABLE",
                "WARNING",
                raw_value,
                f"Macro variable {raw_value} has no value at this statement. "
                "The parameter value could not be resolved.",
                "Define the macro variable earlier, or confirm it is created "
                "at runtime and therefore out of static scope.",
                statement.as_source("unresolved_macro_variable_in_parameter"),
                affected_nodes=[param_id, unknown_id],
                affected_edges=[],
            )

    if len(sites) > 1:
        conflict_id = f"macroconflict:{macro_name}"
        ctx.add_node(
            conflict_id, "MacroConflict", macro_name,
            source=statement.as_source("macro_conflict"),
        )
        ctx.add_edge(
            "implemented_by", call_id, conflict_id, statement.as_source("macro_conflict")
        )
        site_files = ", ".join(site.file for site in sites)
        ctx.add_finding(
            "macro_conflict",
            "REQUIRES_DECISION",
            "ERROR",
            macro_name,
            f"Macro %{macro_name} is defined in more than one declared macro "
            f"source file: {site_files}. The macro body was not expanded.",
            "Resolve the duplicate definition, keeping exactly one source "
            "for this macro name.",
            statement.as_source("macro_conflict"),
            affected_nodes=[call_id, conflict_id],
        )
        return

    if definition is not None:
        definition_id = f"macrodefinition:{definition.name}"
        definition_source = definition.source("macro_definition")
        ctx.add_node(
            definition_id,
            "MacroDefinition",
            definition.authored_name,
            parameters=[parameter.name for parameter in definition.parameters],
            source=definition_source,
        )
        ctx.add_edge(
            "implemented_by", call_id, definition_id,
            statement.as_source("macro_source_definition"),
        )
        if program_path is not None and definition.path == program_path:
            # Inline definition in the file already modeled by program_node_id
            # (e.g. qc_adae.sas defining and calling its own macro) -- a
            # MacroSourceFile node here would just duplicate that Program/
            # SetupFile node, so `defined_in` points at it directly instead.
            ctx.add_edge("defined_in", definition_id, program_node_id, definition_source)
        else:
            source_file_id = f"macrosource:{definition.path.as_posix()}"
            ctx.add_node(
                source_file_id,
                "MacroSourceFile",
                definition.file,
                path=str(definition.path.as_posix()),
                source=definition_source,
            )
            ctx.add_edge("defined_in", definition_id, source_file_id, definition_source)
        _bind_source_template(
            statement, ctx, call_id, definition_id, definition, param_ids, let_events
        )
        return

    if contract is not None and not contract.errors:
        _bind_contract(statement, ctx, call_id, contract, param_ids, let_events)
        return

    unknown_id = f"unknownmacro:{macro_name}"
    ctx.add_node(
        unknown_id, "UnknownMacro", macro_name, source=statement.as_source("macro_source_not_found")
    )
    ctx.add_edge(
        "implemented_by", call_id, unknown_id, statement.as_source("macro_source_not_found")
    )
    ctx.add_finding(
        "macro_source_not_found",
        "UNRESOLVED_MACRO_SOURCE",
        "WARNING",
        macro_name,
        f"Macro source for %{macro_name} was not found in the declared "
        "macro_roots, and no macro contract exists. Internal reads and "
        "writes were not inferred.",
        "Add the macro source directory to macro_roots, or supply a "
        f"macro contract for {macro_name}.",
        statement.as_source("macro_source_not_found"),
        affected_nodes=[call_id, unknown_id],
    )


def _bind_contract(statement, ctx, call_id, contract, param_ids, let_events):
    """A matched md-parsed contract has no role classification (wayfinder:
    bind-macro-calls-to-md-contracts.md) -- a doc's parameters carry no
    input/output signal, so this never infers a dataset edge. It only
    records that the call matched a documented macro: the contract's
    purpose, and which passed parameters lined up with the doc versus
    which didn't (missing required, or unknown to the doc)."""
    contract_id = f"macrocontract:{contract.macro}"
    contract_source = {
        "file": str(contract.path.as_posix()),
        "line_start": 1,
        "line_end": None,
        "statement_order": None,
        "original_text": None,
        "rule": "macro_contract_matched",
    }
    ctx.add_node(
        contract_id, "MacroContract", contract.macro,
        purpose=contract.purpose, examples=contract.examples,
        source=contract_source,
    )
    ctx.add_edge("implemented_by", call_id, contract_id, contract_source)

    missing_required = [
        name for name, meta in contract.parameters.items()
        if meta.required and contract.parameter(name) and param_ids.get(name.lower()) is None
    ]
    unknown_to_doc = [
        name for name in param_ids
        if contract.parameter(name) is None
    ]
    matched = [name for name in param_ids if contract.parameter(name) is not None]

    detail_parts = []
    if matched:
        detail_parts.append(f"matched parameters: {', '.join(sorted(matched))}")
    if missing_required:
        detail_parts.append(f"missing required parameters: {', '.join(sorted(missing_required))}")
    if unknown_to_doc:
        detail_parts.append(f"parameters not in the doc: {', '.join(sorted(unknown_to_doc))}")
    detail = "; ".join(detail_parts) if detail_parts else "all passed parameters match the doc"

    ctx.add_finding(
        "macro_contract_matched",
        "SUPPORTED",
        "INFORMATION",
        contract.macro,
        f"Macro call matched documented macro %{contract.macro}: {contract.purpose or 'no purpose documented'}. "
        f"{detail}.",
        None,
        statement.as_source("macro_contract_matched"),
        affected_nodes=[call_id, contract_id],
    )


def _bind_source_template(
    statement, ctx, call_id, definition_id, definition, param_ids, let_events
):
    """Apply one source body as a structural template, never as SAS code."""
    bindings = {}
    for parameter in definition.parameters:
        key = parameter.name.lower()
        if key in param_ids:
            _param_id, value, unresolved = param_ids[key]
            bindings[key] = None if unresolved or not value.strip() else value
        elif parameter.default and parameter.default.strip():
            value, unresolved = resolve_text(
                parameter.default,
                statement.statement_order,
                let_events,
                return_unresolved=True,
            )
            bindings[key] = None if unresolved or not value.strip() else value

    formal_names = {parameter.name.lower() for parameter in definition.parameters}
    unresolved_parameters = []
    unresolved_external = []

    def bind_text(text, local_bindings=None):
        local_bindings = {k.lower(): str(v) for k, v in (local_bindings or {}).items()}

        def substitute(match):
            key = match.group(1).lower()
            if key in local_bindings:
                return local_bindings[key]
            if key in bindings and bindings[key] is not None:
                return bindings[key]
            if key in formal_names:
                unresolved_parameters.append(match.group(1))
            return match.group(0)

        rendered = _PARAM_REF_RE.sub(substitute, text)
        if unresolved_parameters:
            return rendered
        rendered, unresolved = resolve_text(
            rendered, statement.statement_order, let_events, return_unresolved=True
        )
        unresolved_external.extend(unresolved)
        return rendered

    controls = [s for s in definition.template_statements if _is_control(s.text)]
    if controls:
        selected, control_status, control = _select_control_flow(
            definition.template_statements, bind_text
        )
        _add_control_evidence(
            ctx, call_id, definition_id, definition, statement, control, control_status
        )
        if selected is None or unresolved_parameters or unresolved_external:
            _add_control_not_executed(
                ctx, statement, call_id, definition_id, definition, control, control_status
            )
            return
        blocks, unattached = group_blocks(selected)
    else:
        blocks = []
        for template_block in definition.template_blocks:
            statements = [
                replace(template_statement, text=bind_text(template_statement.text))
                for template_statement in template_block.statements
            ]
            blocks.append(replace(template_block, opener=statements[0], statements=statements))
        unattached = ()

    rendered = "\n".join(
        template_statement.text
        for block in blocks
        for template_statement in block.statements
    )
    external_references = _external_references_outside_import_datafiles(blocks)
    unresolved_external = [
        name for name in unresolved_external if name.lower() in external_references
    ]
    unterminated = any(not block.terminated for block in blocks)
    unsupported = (
        "&&" in rendered
        or _UNSUPPORTED_TEMPLATE_RE.search(rendered)
        or bool(definition.template_findings)
        or (definition.template_has_unattached if not controls else bool(unattached))
        or unterminated
        or any(
            block.kind != "DATA" and block.proc_name not in {"import", "sort", "sql"}
            for block in blocks
        )
    )
    if unresolved_parameters or unresolved_external or unsupported:
        reasons = []
        if unresolved_parameters or unresolved_external:
            reasons.append("one or more template parameters could not be resolved")
        if unsupported:
            reasons.append(
                "the body has an unterminated block"
                if unterminated
                else "the body contains unsupported generated or control-flow syntax"
            )
        ctx.add_finding(
            "macro_source_template_not_executed",
            "NOT_EXECUTED",
            "INFORMATION",
            definition.authored_name,
            f"Source template for %{definition.authored_name} was not bound: "
            f"{' and '.join(reasons)}.",
            "Use a purely structural DATA/PROC IMPORT/PROC SORT/PROC SQL macro body, or review it manually.",
            statement.as_source("macro_source_template_not_executed"),
            affected_nodes=[call_id, definition_id],
        )
        return

    nodes_before = len(ctx.nodes)
    edges_before = len(ctx.edges)
    findings_before = len(ctx.findings)
    template_kinds = set()
    outer_sort_by_at, outer_sort_by_of = ctx.sort_by_at, ctx.sort_by_of
    template_sort_by_at = {}
    ctx.sort_by_at, ctx.sort_by_of = {}, dict(outer_sort_by_of)
    try:
        for block in blocks:
            if block.kind == "DATA":
                apply_data_step(block, ctx, (), sort_by_fallback=outer_sort_by_of)
                template_kinds.add("DATA")
            elif block.proc_name == "sort":
                apply_proc_sort(block, ctx, ())
                template_kinds.add("PROC_SORT")
            elif block.proc_name == "sql":
                apply_proc_sql(block, ctx, ())
                template_kinds.add("PROC_SQL")
            else:
                apply_proc_import(block, ctx, ())
                template_kinds.add("PROC_IMPORT")
        template_sort_by_at = ctx.sort_by_at
    finally:
        ctx.sort_by_at, ctx.sort_by_of = outer_sort_by_at, outer_sort_by_of

    for dataset_id, history in template_sort_by_at.items():
        sort_by = history[-1][1]
        ctx.sort_by_at.setdefault(dataset_id, []).append((statement.statement_order, sort_by))
        ctx.sort_by_of[dataset_id] = sort_by

    call_source = statement.as_source("macro_source_template_call")
    definition_source = definition.source("macro_source_template")
    for item in [
        *ctx.nodes[nodes_before:],
        *ctx.edges[edges_before:],
        *ctx.findings[findings_before:],
    ]:
        item["template_derived"] = True
        item["call_source"] = call_source
        item["definition_source"] = definition_source

    for edge in ctx.edges[edges_before:]:
        if edge["type"] not in {"reads_dataset", "writes_dataset"}:
            continue
        ctx.add_edge(
            edge["type"],
            call_id,
            edge["to"],
            call_source,
            template_derived=True,
            definition_source=definition_source,
            call_source=call_source,
        )

    definition_node = next(node for node in ctx.nodes if node["id"] == definition_id)
    definition_node["template_status"] = control_status if controls else "SUPPORTED"
    definition_node["template_kinds"] = sorted(template_kinds)

    if controls:
        ctx.add_finding(
            "macro_static_control_flow",
            control_status,
            "INFORMATION",
            definition.authored_name,
            f"Source template for %{definition.authored_name} used bounded static control flow.",
            None,
            statement.as_source("macro_static_control_flow"),
            affected_nodes=[call_id, definition_id],
        )


def _is_control(text):
    return re.match(r"^%(if|do|else|end)\b", text, re.IGNORECASE) is not None


def _matching_end(statements, start):
    depth = 1
    for index in range(start + 1, len(statements)):
        text = statements[index].text
        if re.match(r"^%(if|do)\b", text, re.IGNORECASE):
            depth += 1
        elif re.match(r"^%end\b", text, re.IGNORECASE):
            depth -= 1
            if not depth:
                return index
    return None


def _purely_structural(statements, bind_text, local_bindings=None):
    if any(_is_control(item.text) for item in statements):
        return None
    rendered = [replace(item, text=bind_text(item.text, local_bindings)) for item in statements]
    blocks, unattached = group_blocks(rendered)
    if unattached or any(not block.terminated for block in blocks):
        return None
    if any(block.kind != "DATA" and block.proc_name not in {"import", "sort", "sql"} for block in blocks):
        return None
    return rendered


def _scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _select_control_flow(statements, bind_text):
    """Return only a strictly recognized static control-flow body.

    This intentionally recognizes one top-level `%if` or `%do`; it never runs
    macro functions or guesses a nested/generated branch.
    """
    if not statements:
        return [], "SUPPORTED", None
    control = statements[0]

    if re.match(r"^%if\b", control.text, re.IGNORECASE):
        true_end = _matching_end(statements, 0)
        if true_end is None:
            return None, "CONDITIONAL_BRANCH_UNRESOLVED", {
                "kind": "MacroConditional", "statement": control, "branches": []
            }
        true_branch = statements[1:true_end]
        false_branch = []
        if true_end + 1 < len(statements) and re.match(r"^%else\b", statements[true_end + 1].text, re.IGNORECASE):
            if re.match(r"^%else\s+%if\b", statements[true_end + 1].text, re.IGNORECASE):
                return None, "CONDITIONAL_BRANCH_UNRESOLVED", {
                    "kind": "MacroConditional", "statement": control,
                    "branches": [true_branch, statements[true_end + 1:]],
                }
            false_end = _matching_end(
                [statements[true_end + 1], *statements[true_end + 2:]], 0
            )
            if false_end is None or true_end + 1 + false_end != len(statements) - 1:
                return None, "CONDITIONAL_BRANCH_UNRESOLVED", {
                    "kind": "MacroConditional", "statement": control,
                    "branches": [true_branch, statements[true_end + 2:]],
                }
            false_branch = statements[true_end + 2:true_end + 1 + false_end]
        elif true_end != len(statements) - 1:
            return None, "CONDITIONAL_BRANCH_UNRESOLVED", {
                "kind": "MacroConditional", "statement": control,
                "branches": [true_branch],
            }
        rendered_control = bind_text(control.text)
        match = _IF_RE.match(rendered_control)
        evidence = {
            "kind": "MacroConditional",
            "statement": control,
            "branches": [true_branch, false_branch],
        }
        if match is None or _PARAM_REF_RE.search(rendered_control):
            return None, "CONDITIONAL_BRANCH_UNRESOLVED", evidence
        left, operator, right = match.groups()
        authored = _IF_RE.match(control.text)
        if authored is None or any(
            "&&" in operand
            or _MACRO_OPERAND_RE.search(operand)
            or _COMPOUND_OPERAND_RE.search(operand)
            for operand in (*authored.groups()[::2], left, right)
        ):
            return None, "CONDITIONAL_BRANCH_UNRESOLVED", evidence
        equal = _scalar(left) == _scalar(right)
        selected = true_branch if operator.lower() in {"=", "eq"} and equal or operator.lower() in {"!=", "~=", "ne"} and not equal else false_branch
        selected = _purely_structural(selected, bind_text)
        return (selected, "STATIC_CONDITION_SUPPORTED", evidence) if selected is not None else (None, "CONDITIONAL_BRANCH_UNRESOLVED", evidence)

    end = _matching_end(statements, 0)
    if end is None or end != len(statements) - 1:
        return None, "NOT_EXECUTED", {"kind": "MacroLoop", "statement": control, "branches": []}
    numeric = _INTEGER_LOOP_RE.match(control.text)
    body = statements[1:end]
    evidence = {"kind": "MacroLoop", "statement": control, "branches": [body]}
    if numeric:
        variable, start, stop = numeric.groups()
        if int(start) > int(stop):
            return None, "NOT_EXECUTED", evidence
        values = range(int(start), int(stop) + 1)
        if len(values) > _MAX_STATIC_LOOP_ITERATIONS:
            return None, "NOT_EXECUTED", evidence
        rendered = []
        for value in values:
            item = _purely_structural(body, bind_text, {variable: value})
            if item is None:
                return None, "NOT_EXECUTED", evidence
            rendered.extend(item)
        return rendered, "STATIC_LOOP_SUPPORTED", evidence

    list_loop = _LIST_LOOP_RE.match(control.text)
    if list_loop and body:
        index_name, list_name = list_loop.groups()
        item_match = _LIST_ITEM_RE.match(body[0].text)
        if item_match and item_match.group(2).lower() == list_name.lower() and item_match.group(3).lower() == index_name.lower():
            item_name = item_match.group(1)
            raw_list = bind_text(f"&{list_name}.")
            values = [part for part in re.split(r"[\s,]+", raw_list.strip()) if part]
            if values and len(values) <= _MAX_STATIC_LOOP_ITERATIONS and all(re.fullmatch(r"[A-Za-z_]\w*", value) for value in values):
                rendered = []
                for position, value in enumerate(values, 1):
                    item = _purely_structural(body[1:], bind_text, {index_name: position, item_name: value})
                    if item is None:
                        return None, "NOT_EXECUTED", evidence
                    rendered.extend(item)
                return rendered, "STATIC_LIST_LOOP_SUPPORTED", evidence
    return None, "NOT_EXECUTED", evidence


def _add_control_evidence(ctx, call_id, definition_id, definition, call_statement, control, status):
    if control is None:
        return
    count = sum(node["type"] == control["kind"] for node in ctx.nodes) + 1
    control_id = f"{control['kind'].lower()}:{count:03d}"
    control_source = control["statement"].as_source("macro_control_flow")
    ctx.add_node(
        control_id, control["kind"], control["statement"].text, status=status,
        branches=["\n".join(item.original_text for item in branch) for branch in control["branches"]],
        source=control_source,
        call_source=call_statement.as_source("macro_source_template_call"),
        definition_source=definition.source("macro_source_template"),
    )
    ctx.add_edge("has_control_flow", definition_id, control_id, control_source)
    for index, branch in enumerate(control["branches"], 1):
        if not branch:
            continue
        branch_id = f"conditionalbranch:{count:03d}:{index:03d}"
        ctx.add_node(
            branch_id, "ConditionalBranch", f"{control['kind']} branch {index}",
            candidate_text="\n".join(item.original_text for item in branch),
            source=branch[0].as_source("conditional_branch_candidate"),
        )
        ctx.add_edge("conditional_candidate", control_id, branch_id, control_source)


def _add_control_not_executed(ctx, statement, call_id, definition_id, definition, control, status):
    if status == "CONDITIONAL_BRANCH_UNRESOLVED":
        ctx.add_finding(
            "macro_conditional_branch_unresolved", status, "INFORMATION", definition.authored_name,
            f"Source template for %{definition.authored_name} has an unresolved or unsupported conditional branch.",
            "Review all conditional candidates manually.",
            statement.as_source("macro_conditional_branch_unresolved"),
            affected_nodes=[call_id, definition_id],
        )
    ctx.add_finding(
        "macro_control_flow_not_executed", "NOT_EXECUTED", "INFORMATION", definition.authored_name,
        f"Source template for %{definition.authored_name} contains control flow that was not statically bound.",
        "Use a simple scalar condition or bounded structural loop, or review it manually.",
        statement.as_source("macro_control_flow_not_executed"),
        affected_nodes=[call_id, definition_id],
    )
    definition_node = next(node for node in ctx.nodes if node["id"] == definition_id)
    definition_node["template_status"] = status
