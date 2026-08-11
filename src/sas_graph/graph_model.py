"""Assemble nodes/edges/findings into the frozen graph.json contract.

wayfinder/tickets/freeze-minimal-graph-contract.md is the frozen schema;
examples/hand_written_graph.json demonstrates it. This module is the only
thing that constructs node/edge ids and appends to the three lists, so two
rule modules can never collide on an id or disagree on the envelope shape.

Dataset nodes are deduplicated by id and carry `source: null` (the contract
ticket's own finding: a dataset is read and written by multiple steps, so it
has no single line range -- locations live on the edges instead). Every other
node type is appended once per call site, since a Step, SqlStatement or
MacroCall is inherently single-sourced.

Section 11.3 still applies here even though this module never resolves macro
text itself: `GraphContext` carries no "current value of X" field for
anything a rule computes from `resolve_text` -- the libref map and sort-by
map below are the two exceptions the phase 4 handoff calls out explicitly,
because LIBNAME and PROC SORT are structural evidence, not macro
substitution.
"""

from dataclasses import dataclass, field

from . import SCHEMA_VERSION


def dataset_id(name):
    return f"dataset:{name}"


def normalize_dataset(raw, libref_map):
    """Normalize a `libref.member` or bare `member` name (section 11.4).

    A bare name (no dot) defaults to `work`, matching SAS's own default
    libref -- section 22's fixture never exercises this, but every DATA/PROC
    output in the dev plan's own examples uses `work.x`, never bare `x`.
    `libref_map` is consulted for nothing yet (v0 does not resolve a libref to
    a physical path for graph purposes, section 11.5), but is threaded through
    so a future rule can flag an undeclared libref without a second lookup
    mechanism.
    """
    if "." in raw:
        libref, member = raw.split(".", 1)
    else:
        libref, member = "work", raw
    return libref.lower(), member.lower()


@dataclass
class GraphContext:
    """Accumulates one run's nodes/edges/findings and owns every id.

    `sort_by_of` maps a normalized dataset name to its last-known PROC SORT
    `by_vars`. Every sort also records its effective statement order in
    `sort_by_at`, so a completed DATA block can ignore later sort evidence.

    For N declared programs (wayfinder: per-program-macro-state-isolation),
    `run_pipeline.run` resets `sort_by_of`/`sort_by_at` to a setup-only
    snapshot before each program's parse -- structural sort evidence from one
    program's own body must not leak into another's merge-by-prefix check,
    the same isolation already applied to `%let` state. `libref_map` is the
    opposite call: it stays one cross-program-shared dict, never reset,
    because nothing reads it yet (v0 does not resolve a libref to a physical
    path for graph purposes, section 11.5) -- isolating write-only state that
    has no observable behavior would be speculative. Revisit when a consumer
    appears.
    """

    main_programs: tuple
    setup_file: str
    run_id: str
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    findings: list = field(default_factory=list)

    _node_ids: set = field(default_factory=set)
    _step_counter: int = 0
    _edge_counter: int = 0
    _finding_counter: int = 0
    _inactive_counter: int = 0
    libref_map: dict = field(default_factory=dict)  # libref -> resolved_path
    sort_by_of: dict = field(default_factory=dict)  # "libref.member" -> by_vars list
    sort_by_at: dict = field(default_factory=dict)  # dataset id -> [(statement_order, by_vars)]

    # --- ids -----------------------------------------------------------

    def next_step_id(self):
        self._step_counter += 1
        return f"step:{self._step_counter:03d}"

    def next_edge_id(self):
        self._edge_counter += 1
        return f"edge:{self._edge_counter:03d}"

    def next_finding_id(self, rule):
        self._finding_counter += 1
        return f"finding:{self._finding_counter:03d}:{rule}"

    def next_inactive_id(self, prefix):
        self._inactive_counter += 1
        return f"{prefix}:{self._inactive_counter:03d}"

    # --- nodes -----------------------------------------------------------

    def add_node(self, node_id, node_type, label, source=None, **extra):
        """Append a node, deduplicating by id.

        Only `Dataset`/`UnknownDataset` are ever re-added for the same id
        (every step that reads/writes `work.a` calls this again) -- everything
        else is single-sourced by construction, so the dedup check is cheap
        insurance, not a hot path.
        """
        if node_id in self._node_ids:
            return node_id
        self._node_ids.add(node_id)
        node = {"id": node_id, "type": node_type, "label": label, **extra, "source": source}
        self.nodes.append(node)
        return node_id

    def add_dataset(self, raw_name):
        """Register a Dataset node from a raw `libref.member` (or bare) name.

        Returns the node id. Section 10.1: Dataset nodes carry `source: null`.
        """
        libref, member = normalize_dataset(raw_name, self.libref_map)
        node_id = dataset_id(f"{libref}.{member}")
        self.add_node(node_id, "Dataset", f"{libref}.{member}", libref=libref, member=member)
        return node_id

    def add_unknown_dataset(self, unresolved_expression, source):
        node_id = f"unknowndataset:{unresolved_expression}"
        self.add_node(
            node_id,
            "UnknownDataset",
            unresolved_expression,
            unresolved_expression=unresolved_expression,
            source=source,
        )
        return node_id

    # --- edges -----------------------------------------------------------

    def add_edge(self, edge_type, from_id, to_id, source, **extra):
        edge_id = self.next_edge_id()
        self.edges.append(
            {"id": edge_id, "type": edge_type, "from": from_id, "to": to_id, **extra, "source": source}
        )
        return edge_id

    # --- findings -----------------------------------------------------------

    def add_finding(
        self,
        rule,
        status,
        severity,
        obj,
        message,
        suggested_action,
        source,
        affected_nodes=(),
        affected_edges=(),
    ):
        finding_id = self.next_finding_id(rule)
        self.findings.append(
            {
                "id": finding_id,
                "status": status,
                "type": rule,
                "severity": severity,
                "object": obj,
                "message": message,
                "suggested_action": suggested_action,
                "affected_nodes": list(affected_nodes),
                "affected_edges": list(affected_edges),
                "source": source,
            }
        )
        return finding_id

    def add_existing_findings(self, findings):
        """Append parser findings through the same run-wide id sequence."""
        for finding in findings:
            finding = dict(finding)
            finding["id"] = self.next_finding_id(finding["type"])
            self.findings.append(finding)

    # --- assembly -----------------------------------------------------------

    def run_status(self):
        """Derive the section 5 run-level status from findings collected so far.

        A BLOCKED finding never reaches this module in Phase 4 (config/parse
        failures are decided before the parser runs at all -- section 9 steps
        1-3 gate everything after them), so this only ever distinguishes
        COMPLETE from PARTIAL, matching section 5.1/5.2's own wording: a graph
        with no unresolved required elements is COMPLETE, anything else that
        still emitted a graph is PARTIAL.
        """
        if any(f["status"] == "BLOCKED" for f in self.findings):
            return "FAILED"
        partial_statuses = {
            "REQUIRES_DECISION",
            "NOT_EXECUTED",
            "UNRESOLVED_MACRO_VARIABLE",
            "UNRESOLVED_MACRO_SOURCE",
            "MACRO_CONFLICT",
            "CONDITIONAL_BRANCH_UNRESOLVED",
        }
        if any(f["status"] in partial_statuses for f in self.findings):
            return "PARTIAL"
        return "COMPLETE"

    def to_graph(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "run_status": self.run_status(),
            "main_programs": list(self.main_programs),
            "setup_file": self.setup_file,
            "nodes": self.nodes,
            "edges": self.edges,
            "findings": self.findings,
        }
