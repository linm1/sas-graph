"""The emitted graph vocabulary and the 0.2.0 to 0.3.0 normalizer."""


NODE_TYPES = frozenset(
    {
        "Dataset",
        "UnknownDataset",
        "Variable",
        "UnknownVariable",
        "Step",
        "ExternalFile",
        "InactiveEvidence",
        "CommentBlock",
        "CommentedStatement",
        "Library",
        "MacroCall",
        "MacroParameter",
        "MacroConflict",
        "MacroDefinition",
        "MacroSourceFile",
        "UnknownMacro",
        "MacroContract",
        "MacroConditional",
        "MacroLoop",
        "ConditionalBranch",
        "SqlBlock",
        "SqlStatement",
        "SetupFile",
        "MacroVariable",
        "Program",
    }
)


_VARIABLE_WRITE_ENDPOINTS = frozenset(
    {
        ("Step", "Variable"),
        ("Step", "UnknownVariable"),
        ("MacroCall", "Variable"),
        ("MacroCall", "UnknownVariable"),
        ("SqlStatement", "Variable"),
    }
)

# SQL selection-reference edges point from the exact source Variable (or an
# explicitly represented UnknownVariable for future conservative emitters) to
# the consuming SqlStatement.  The direction mirrors ``reads_variable`` and
# ``conditioned_by``: a source column is the dependency, the SQL operation is
# the consumer.  B2's emitter only creates these edges for exact Variable
# bindings; the UnknownVariable pairs keep the registry honest for graph
# fragments that preserve unresolved IR explicitly.
_SQL_REFERENCE_ENDPOINTS = frozenset(
    {
        ("Variable", "SqlStatement"),
        ("UnknownVariable", "SqlStatement"),
    }
)


EDGE_ENDPOINTS = {
    "reads_variable": frozenset(
        {
            ("Variable", "Step"),
            ("UnknownVariable", "Step"),
            ("Variable", "MacroCall"),
            ("UnknownVariable", "MacroCall"),
            ("Variable", "SqlStatement"),
            ("UnknownVariable", "SqlStatement"),
        }
    ),
    "writes_variable": _VARIABLE_WRITE_ENDPOINTS,
    "derives": _VARIABLE_WRITE_ENDPOINTS,
    "conditioned_by": frozenset(
        (target, source) for source, target in _VARIABLE_WRITE_ENDPOINTS
    ),
    # Variable -> SqlStatement: the column participates in an explicit JOIN
    # predicate; ``join_kind`` is an additive edge attribute when known.
    "joins_on": _SQL_REFERENCE_ENDPOINTS,
    # Variable -> SqlStatement: the column is an exact WHERE/HAVING filter
    # reference for a source dataset in the SQL statement.
    "filters_dataset": _SQL_REFERENCE_ENDPOINTS,
    # Variable -> SqlStatement: the column is an exact GROUP BY key.
    "groups_by": _SQL_REFERENCE_ENDPOINTS,
    # Variable -> SqlStatement: the column is an exact ORDER BY key.
    "sorts_by": _SQL_REFERENCE_ENDPOINTS,
    "reads_dataset": frozenset(
        {
            (source, target)
            for source in ("Dataset", "UnknownDataset")
            for target in ("Step", "MacroCall", "SqlStatement")
        }
    ),
    "writes_dataset": frozenset(
        {
            (source, target)
            for source in ("Step", "MacroCall", "SqlStatement")
            for target in ("Dataset", "UnknownDataset")
        }
    ),
    "depends_on": frozenset(
        {
            (source, target)
            for source in ("Dataset", "UnknownDataset")
            for target in ("Dataset", "UnknownDataset")
        }
    ),
    "comment_mentions_macro": frozenset({("CommentBlock", "InactiveEvidence")}),
    "comment_mentions_dataset": frozenset({("CommentBlock", "InactiveEvidence")}),
    "inactive_candidate_writes": frozenset(
        {("CommentedStatement", "InactiveEvidence")}
    ),
    "inactive_candidate_reads": frozenset(
        {("CommentedStatement", "InactiveEvidence")}
    ),
    "inactive_candidate_depends_on": frozenset(
        {("InactiveEvidence", "InactiveEvidence")}
    ),
    "calls_macro": frozenset(
        {("Program", "MacroCall"), ("SetupFile", "MacroCall")}
    ),
    "passes_parameter": frozenset({("MacroCall", "MacroParameter")}),
    "resolves_to": frozenset({("MacroParameter", "UnknownDataset")}),
    "implemented_by": frozenset(
        {
            ("MacroCall", "MacroConflict"),
            ("MacroCall", "MacroDefinition"),
            ("MacroCall", "UnknownMacro"),
            ("MacroCall", "MacroContract"),
        }
    ),
    "defined_in": frozenset(
        {
            ("MacroDefinition", "Program"),
            ("MacroDefinition", "SetupFile"),
            ("MacroDefinition", "MacroSourceFile"),
        }
    ),
    "has_control_flow": frozenset(
        {
            ("MacroDefinition", "MacroConditional"),
            ("MacroDefinition", "MacroLoop"),
        }
    ),
    "conditional_candidate": frozenset(
        {
            ("MacroConditional", "ConditionalBranch"),
            ("MacroLoop", "ConditionalBranch"),
        }
    ),
    "writes_external_file": frozenset(
        {
            ("Dataset", "ExternalFile"),
            ("UnknownDataset", "ExternalFile"),
        }
    ),
    "reads_external_file": frozenset(
        {
            ("ExternalFile", "Dataset"),
            ("ExternalFile", "UnknownDataset"),
        }
    ),
    "contains_sql_statement": frozenset({("SqlBlock", "SqlStatement")}),
    "contains_step": frozenset(
        {
            ("Program", "Step"),
            ("Program", "SqlBlock"),
            ("SetupFile", "Step"),
            ("SetupFile", "SqlBlock"),
        }
    ),
    "defines_macro_variable": frozenset(
        {("SetupFile", "MacroVariable"), ("Program", "MacroVariable")}
    ),
}


EDGE_TYPES = frozenset(EDGE_ENDPOINTS)


def normalize_graph(graph):
    """Up-convert one loaded 0.2.0 graph to the 0.3.0 envelope.

    This function does not mutate its input, but the returned graph shares
    nested sub-objects (including ``evidence`` and ``source``) with that
    input. Callers must not mutate nested dictionaries in either graph in
    place afterwards.
    """
    from .evidence import EvidenceKind, ResolutionStatus
    from .graph_model import GraphContext

    if not isinstance(graph, dict):
        raise TypeError("graph must be a dictionary")
    if graph.get("schema_version") != "0.2.0":
        raise ValueError("normalizer expects schema_version '0.2.0'")

    normalized = dict(graph)
    normalized["schema_version"] = "0.3.0"
    schema = dict(graph.get("schema", {}))
    schema["producer"] = "sas-graph"
    schema["producer_version"] = "0.2.0"
    capabilities = list(
        schema.get("capabilities", ["dataset_lineage", "variable_lineage"])
    )
    if "evidence_v1" not in capabilities:
        capabilities.append("evidence_v1")
    schema["capabilities"] = capabilities
    normalized["schema"] = schema
    normalized["edges"] = []
    for edge in graph.get("edges", []):
        normalized_edge = dict(edge)
        # Existing evidence intentionally keeps identity; see the docstring.
        if "evidence" not in normalized_edge:
            normalized_edge["evidence"] = GraphContext.make_evidence(
                EvidenceKind.UNKNOWN,
                ResolutionStatus.NOT_ATTEMPTED,
                "normalize_graph",
                normalized_edge.get("source"),
            )
        normalized["edges"].append(normalized_edge)
    return normalized
