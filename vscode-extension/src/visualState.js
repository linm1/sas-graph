// @ts-check
"use strict";

// Pure band-table + hover-precedence module (ticket 03). No vscode import,
// no DOM, no vis-network import — dual-environment pattern copied exactly
// from src/hopDistance.js / src/libraryFilter.js. media/webview.js's
// applyVisualState is the ONLY place that calls into this module and writes
// edges.update(...)/nodes.update(...) with the results — this module only
// computes values, it never touches vis-network or the DOM (spec: single
// opacity-mutation site is load-bearing, not a style preference).

// Edge opacity band table (wayfinder/spec-traversal-legible-filter-results.md
// "Edge opacity bands"). Band = max(distA, distB), the farther endpoint, so
// the ramp decays monotonically outward along a chain.
const HOP0_OPACITY = 1.0;
const HOP1_OPACITY = 0.9;
const HOP2_OPACITY = 0.6;
const HOP3_OPACITY = 0.35;
const HOP4_PLUS_OPACITY = 0.15;
const UNREACHABLE_OPACITY = 0.03;
const IDLE_OPACITY = 0.5;
const HOVER_INCIDENT_OPACITY = 0.9;

/**
 * Baseline edge opacity from the hop-distance band table, given both
 * endpoints' distances from computeHopDistances's `distances` map (undefined
 * = unreachable within the ceiling, or disconnected).
 * @param {number|undefined} distA
 * @param {number|undefined} distB
 * @returns {number}
 */
function edgeBandOpacity(distA, distB) {
  if (distA === undefined || distB === undefined) return UNREACHABLE_OPACITY;
  const band = Math.max(distA, distB);
  if (band === 0) return HOP0_OPACITY;
  if (band === 1) return HOP1_OPACITY;
  if (band === 2) return HOP2_OPACITY;
  if (band === 3) return HOP3_OPACITY;
  return HOP4_PLUS_OPACITY; // hop4+, within ceiling (distA/distB both defined)
}

/**
 * Apply hover/select/cursor precedence on top of an edge's band baseline:
 * incident edges raise to 0.9, UNLESS already at hop0 (1.0), which is never
 * demoted by hover. Non-incident edges keep their baseline unchanged (layer
 * on top, never replace — spec "Precedence" section).
 * @param {number} baseline - this edge's band opacity (or flat 0.5 if idle)
 * @param {boolean} isIncident - whether this edge touches the highlighted node
 * @returns {number}
 */
function applyHoverToEdge(baseline, isIncident) {
  if (!isIncident) return baseline;
  return Math.max(baseline, HOVER_INCIDENT_OPACITY);
}

/**
 * Compute the effective hit-id set that computeHopDistances should band
 * against, given the current click/search/filter state (spec "Hit-set
 * formation"; click origin added by unify-cursor-select-into-hit-set).
 *   - click active (clickHitId set, from a node click or editor cursor
 *     sync): hit set is that single node, always — wins over search/filter
 *     per that ticket's resolution ("click always wins... more specific,
 *     more recent user action"), not combined with them.
 *   - search active (searchMatches !== null), no filter active: hit set is
 *     searchMatches.
 *   - filter active, no search: hit set is every node passing
 *     nodeMatchesFilters(n) via `filtersAllow`.
 *   - both search and filter active: intersection (must pass both).
 *   - none active: null — caller must not invoke computeHopDistances at
 *     all in this case (idle path, flat 0.5).
 * @param {any[]} nodes - render-model nodes
 * @param {Set<string>|null} searchMatches
 * @param {{ library: string|null, type: string|null }} filters
 * @param {(node: any, filters: any) => boolean} filtersAllow - injected so
 *   this module doesn't hardcode a dependency on libraryFilter.js's global
 * @param {string|null} [clickHitId] - clicked/cursor-synced node id, or null
 * @returns {Set<string>|null}
 */
function computeHitSet(nodes, searchMatches, filters, filtersAllow, clickHitId) {
  if (clickHitId) return new Set([clickHitId]);

  const hasSearch = searchMatches !== null;
  const hasFilter = Boolean(filters && (filters.library || filters.type));
  if (!hasSearch && !hasFilter) return null;

  if (hasFilter) {
    const filterHits = new Set(nodes.filter((n) => filtersAllow(n, filters)).map((n) => n.id));
    if (!hasSearch) return filterHits;
    // both active: intersection
    return new Set([...searchMatches].filter((id) => filterHits.has(id)));
  }
  return new Set(searchMatches);
}

/**
 * Node opacity for the "lasso" filter/search treatment (spec: reveal hits
 * plus every node with a finite bounded-lineage distance from any hit, dim
 * every other true non-match). `distances` (computeHopDistances's map) is
 * the general reachability signal — hitIds/bridgeNodeIds are a subset of it,
 * but distances alone also covers the single-hit case, where bridgeNodeIds
 * is necessarily empty (a bridge requires two hits) yet the hit's whole
 * bounded lineage chain must still reveal (spec story 8). `distances` is
 * optional so existing hit/bridge-only callers keep working unchanged.
 * Does not account for hover — see applyHoverToNode for that layer.
 * @param {string} nodeId
 * @param {Set<string>} hitIds
 * @param {Set<string>} bridgeNodeIds
 * @param {Map<string, number>} [distances]
 * @returns {number}
 */
function nodeLassoOpacity(nodeId, hitIds, bridgeNodeIds, distances) {
  if (hitIds.has(nodeId) || bridgeNodeIds.has(nodeId)) return 1;
  if (distances && distances.has(nodeId)) return 1;
  return 0.1;
}

/**
 * Apply hover/select/cursor precedence on top of a node's lasso baseline:
 * the highlighted node itself always stays fully visible; every other node
 * (including bridge/hit nodes) keeps its existing baseline.
 * @param {number} baseline
 * @param {boolean} isHighlighted
 * @returns {number}
 */
function applyHoverToNode(baseline, isHighlighted) {
  return isHighlighted ? 1 : baseline;
}

/**
 * A hit node has "no lineage" if it has zero incident edges of the five
 * distance-propagating types (dataset reads/writes, external-file
 * reads/writes, or implemented_by).
 * Pure edge scan — deliberately not a fourth field on computeHopDistances's
 * return shape (ticket 02's shape is fixed at three fields); this stays a
 * narrowly-scoped predicate in visualState.js instead.
 * @param {string} nodeId
 * @param {any[]} edges - render-model edges
 * @param {Set<string>} propagatingTypes - e.g. hopDistance.js's PROPAGATING_TYPES
 * @returns {boolean}
 */
function hasNoLineage(nodeId, edges, propagatingTypes) {
  return !edges.some((e) => propagatingTypes.has(e.type) && (e.from === nodeId || e.to === nodeId));
}

/**
 * Ticket 04's two status messages, as one pure selector so webview.js's
 * statusMessage() is a thin adapter and the (a)/(b)/(c) cases are unit
 * testable without a DOM. Two separate, coexisting conditions — not one
 * gate:
 *   1. ANY hit with zero lineage edges -> named in a "no lineage" note.
 *   2. A hit PAIR with lineage on both ends but no path within the ceiling
 *      -> "disconnected" message. A no-lineage endpoint disqualifies a pair
 *      from (2) — that pair was never part of a lineage graph to begin with,
 *      it's (1)'s territory, not (2)'s.
 * Precedence when a hit set has BOTH a no-lineage hit and an unrelated
 * disconnected pair among its other (lineage-having) hits: the no-lineage
 * note is appended to whichever message the rest of the hit set would
 * otherwise produce (default count, or the disconnected message), rather
 * than replacing it outright — only an ALL-no-lineage hit set replaces the
 * default message entirely. Otherwise a large filter hit set (e.g. a
 * library filter that legitimately pulls in a few isolated-by-design nodes
 * alongside many connected ones, per libraryFilter.js's "no derivable
 * library stays visible" rule) would bury the still-useful default/disconnected
 * message behind a note about a handful of expected outliers.
 * @param {string[]} hitList - state.hitIds as an array
 * @param {Map<string,"reachable"|"disconnected">|null} pairStatus
 * @param {any[]} edges - render-model edges
 * @param {Set<string>} propagatingTypes
 * @param {number} ceiling
 * @param {(id: string) => string} labelOf - node id -> display label
 * @returns {string|null} null means "no special message, default applies"
 */
function deriveStatusMessage(hitList, pairStatus, edges, propagatingTypes, ceiling, labelOf) {
  const noLineage = (id) => hasNoLineage(id, edges, propagatingTypes);
  const noLineageHits = hitList.filter(noLineage);

  const noLineageNote = (() => {
    if (noLineageHits.length === 0) return null;
    const MAX_NAMED = 2;
    const names = noLineageHits.slice(0, MAX_NAMED).map((id) => `"${labelOf(id)}"`).join(", ");
    const rest = noLineageHits.length - MAX_NAMED;
    const who = rest > 0 ? `${names}, and ${rest} more` : names;
    return `No lineage edges (dataset reads/writes, external-file reads/writes, ` +
       `or implemented-by) to trace for: ${who}.`;
  })();

  // All hits lack lineage: the note IS the whole story, replace the default.
  if (noLineageHits.length > 0 && noLineageHits.length === hitList.length) return noLineageNote;

  let disconnectedMsg = null;
  if (pairStatus) {
    for (const [key, status] of pairStatus) {
      if (status !== "disconnected") continue;
      const [a, b] = key.split("|");
      if (noLineage(a) || noLineage(b)) continue; // (1)'s territory, not (2)'s
      disconnectedMsg = `"${labelOf(a)}" and "${labelOf(b)}" are not connected within ${ceiling} hops.`;
      break;
    }
  }

  // Mixed set: append the no-lineage note to whatever else applies (or to
  // nothing, letting the default count message stand alone) instead of
  // silently eating it or burying the other signal.
  if (noLineageNote && disconnectedMsg) return `${disconnectedMsg} ${noLineageNote}`;
  return disconnectedMsg || noLineageNote;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    edgeBandOpacity,
    applyHoverToEdge,
    computeHitSet,
    nodeLassoOpacity,
    applyHoverToNode,
    hasNoLineage,
    deriveStatusMessage,
    IDLE_OPACITY,
    UNREACHABLE_OPACITY,
  };
}
if (typeof window !== "undefined") {
  window.edgeBandOpacity = edgeBandOpacity;
  window.applyHoverToEdge = applyHoverToEdge;
  window.computeHitSet = computeHitSet;
  window.nodeLassoOpacity = nodeLassoOpacity;
  window.applyHoverToNode = applyHoverToNode;
  window.hasNoLineage = hasNoLineage;
  window.deriveStatusMessage = deriveStatusMessage;
  window.IDLE_OPACITY = IDLE_OPACITY;
  window.UNREACHABLE_OPACITY = UNREACHABLE_OPACITY;
}
