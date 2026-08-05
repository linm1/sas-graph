// @ts-check
"use strict";

// Pure port of .scratch/prototype-vis-network/convert.py to JS. Takes a
// parsed graph.json object, returns a vis-network render model. No vscode
// import, no DOM, no file I/O — caller decides where the JSON came from.

// Per surface-findings-in-webview: UnknownMacro/UnknownDataset get their own
// color distinct from MacroCall/Dataset, and MacroConflict is added (orange)
// even though no fixture currently contains one — unverified against real
// data, added for completeness only (see ticket 02 acceptance note).
const TYPE_COLOR = {
  Program: "#4E79A7",
  SetupFile: "#4E79A7",
  Step: "#F28E2B",
  SqlBlock: "#F28E2B",
  SqlStatement: "#F28E2B",
  Dataset: "#59A14F",
  ExternalFile: "#76B7B2",
  MacroCall: "#E15759",
  MacroDefinition: "#E15759",
  MacroParameter: "#EDC949",
  MacroLoop: "#E15759",
  MacroConditional: "#E15759",
  MacroSourceFile: "#B07AA1",
  ConditionalBranch: "#EDC949",
  CommentBlock: "#BAB0AC",
  UnknownMacro: "#FF9D9A", // distinct from MacroCall's #E15759
  UnknownDataset: "#8CD17D", // distinct from Dataset's #59A14F
  MacroConflict: "#F1A340", // orange; no fixture exercises this yet
};

const DEFAULT_COLOR = "#9C755F";

// Node types excluded at conversion time: unlinked, clutter the layout.
// MacroParameter joins CommentBlock here (ticket 01 / spec story 17): all 41
// in the reference fixture are dead-end leaves with no lineage edge of their
// own. This reuses the existing keptIds/keptEdges filtering below — once a
// type is in this set, its nodes AND any edge touching them drop out
// automatically. Not a new exclusion mechanism, just one more type in it.
// Their raw_value text is not lost: convertGraph copies each MacroParameter's
// name/raw_value onto the owning MacroCall/MacroDefinition node's `raw`
// payload (via the existing passes_parameter edges) so the click-info panel
// can still show them as part of the macro's call signature.
const EXCLUDED_TYPES = new Set(["CommentBlock", "MacroParameter"]);

// Edge types that carry lineage direction get an arrowhead shape at
// conversion time (spec: direction is intrinsic to edge.type, fixed once,
// never recomputed on search/selection). Every other edge type (calls_macro,
// contains_step, passes_parameter, defines_macro_variable, depends_on,
// resolves_to, ...) intentionally gets no arrowhead override here — they
// keep vis-network's default rendering. This is a deliberate scope
// narrowing to the five lineage-carrying types the spec calls out, not an
// oversight.
const ARROWHEAD_BY_TYPE = {
  reads_dataset: { to: { enabled: true, type: "vee" } },
  writes_dataset: { to: { enabled: true, type: "triangle" } },
  reads_external_file: { to: { enabled: true, type: "vee" } },
  writes_external_file: { to: { enabled: true, type: "triangle" } },
  implemented_by: { to: { enabled: true, type: "circle" } },
};

/**
 * Pure function of edge.type -> vis-network arrowhead config. Computed once
 * at render-model build time (ticket 01) and never recomputed relative to
 * search/selection state.
 * @param {string} edgeType
 * @returns {{ to: { enabled: true, type: "vee"|"triangle"|"circle" } } | undefined}
 */
function assignArrowhead(edgeType) {
  return ARROWHEAD_BY_TYPE[edgeType];
}

/**
 * Human-readable edge tooltip phrase, e.g. "reads raw.ae", "writes
 * ae_suppae", "implemented by <label>". Falls back to the raw type string
 * for edge types with no readable phrasing defined (e.g. depends_on,
 * calls_macro) so no edge is left without a title.
 * @param {string} edgeType
 * @param {string} targetLabel - label of the edge's `to` node
 * @returns {string}
 */
function edgeTitle(edgeType, targetLabel) {
  if (edgeType === "reads_dataset") return `reads ${targetLabel}`;
  if (edgeType === "writes_dataset") return `writes ${targetLabel}`;
  if (edgeType === "implemented_by") return `implemented by ${targetLabel}`;
  return edgeType;
}

/**
 * Convert a parsed graph.json object into a vis-network render model.
 * @param {any} graph - parsed graph.json (nodes, edges, findings arrays)
 * @returns {{ nodes: any[], edges: any[] }}
 */
function convertGraph(graph) {
  const keptNodes = (graph.nodes || []).filter((n) => !EXCLUDED_TYPES.has(n.type));
  const keptIds = new Set(keptNodes.map((n) => n.id));
  const keptEdges = (graph.edges || []).filter((e) => keptIds.has(e.from) && keptIds.has(e.to));

  const degree = {};
  for (const e of keptEdges) {
    degree[e.from] = (degree[e.from] || 0) + 1;
    degree[e.to] = (degree[e.to] || 0) + 1;
  }

  // Attach each finding to every node id in its affected_nodes. New behavior
  // relative to convert.py: convert.py never reads top-level `findings`, so
  // without this the detail panel (ticket 04/07) has nothing to render for
  // finding-affected nodes. A node referenced by multiple findings collects
  // all of them.
  const findingsByNode = {};
  for (const finding of graph.findings || []) {
    for (const nodeId of finding.affected_nodes || []) {
      if (!findingsByNode[nodeId]) findingsByNode[nodeId] = [];
      findingsByNode[nodeId].push(finding);
    }
  }

  // MacroParameter nodes are excluded from the canvas (EXCLUDED_TYPES
  // above), but their raw_value text must stay reachable from the owning
  // macro's click-info panel (spec story 17). graph.json links a parameter
  // to its owning MacroCall/MacroDefinition via a `passes_parameter` edge
  // (macro -> parameter), so reuse that link rather than inventing a second
  // index. Built from the FULL (pre-exclusion) node/edge lists, since the
  // MacroParameter nodes themselves are gone from keptNodes/keptEdges by
  // this point.
  const allNodesById = new Map((graph.nodes || []).map((n) => [n.id, n]));
  const parametersByMacroId = {};
  for (const e of graph.edges || []) {
    if (e.type !== "passes_parameter") continue;
    const paramNode = allNodesById.get(e.to);
    if (!paramNode || paramNode.type !== "MacroParameter") continue;
    if (!parametersByMacroId[e.from]) parametersByMacroId[e.from] = [];
    parametersByMacroId[e.from].push({ name: paramNode.label, raw_value: paramNode.raw_value });
  }

  const nodes = keptNodes.map((n) => {
    const src = n.source || {};
    const parameters = parametersByMacroId[n.id];
    return {
      id: n.id,
      label: n.label !== undefined ? n.label : n.id,
      group: n.type,
      color: TYPE_COLOR[n.type] || DEFAULT_COLOR,
      value: degree[n.id] || 1,
      file: src.file ?? null,
      line_start: src.line_start ?? null,
      line_end: src.line_end ?? null,
      // Full original graph.json node, for the click info panel. Macro
      // nodes get a shallow copy with `parameters` added (immutability:
      // never mutate the caller's parsed graph.json) — every other node's
      // `raw` stays the exact original object.
      raw: parameters ? { ...n, parameters } : n,
      findings: findingsByNode[n.id] || [],
    };
  });

  const nodeLabelById = new Map(keptNodes.map((n) => [n.id, n.label !== undefined ? n.label : n.id]));

  const edges = keptEdges.map((e) => ({
    id: e.id,
    from: e.from,
    to: e.to,
    type: e.type,
    template_derived: e.template_derived,
    arrows: assignArrowhead(e.type),
    title: edgeTitle(e.type, nodeLabelById.get(e.to)),
  }));

  return { nodes, edges };
}

module.exports = { convertGraph, TYPE_COLOR, DEFAULT_COLOR, EXCLUDED_TYPES, assignArrowhead };
