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
    "writes_variable": frozenset(
        {
            ("Step", "Variable"),
            ("Step", "UnknownVariable"),
            ("MacroCall", "Variable"),
            ("MacroCall", "UnknownVariable"),
            ("SqlStatement", "Variable"),
        }
    ),
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
    """Up-convert one loaded 0.2.0 graph to the 0.3.0 envelope."""
    if not isinstance(graph, dict):
        raise TypeError("graph must be a dictionary")
    if graph.get("schema_version") != "0.2.0":
        raise ValueError("normalizer expects schema_version '0.2.0'")

    normalized = dict(graph)
    normalized["schema_version"] = "0.3.0"
    normalized["schema"] = {
        "producer": "sas-graph",
        "producer_version": "0.2.0",
        "capabilities": ["dataset_lineage", "variable_lineage"],
    }
    # PR 3 will attach UNKNOWN/NOT_ATTEMPTED evidence to pre-evidence edges.
    return normalized
