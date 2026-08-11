// @ts-check
"use strict";

// Pure hop-distance module (ticket 02). No vscode import, no DOM, no
// vis-network import — dual-environment pattern copied exactly from
// src/libraryFilter.js / src/searchPredicate.js.
//
// Distance is computed pairwise between "hit" nodes (a search/filter
// result set), walking ONLY the five lineage-carrying edge types
// (reads_dataset, writes_dataset, reads_external_file,
// writes_external_file, implemented_by) as undirected edges.
// depends_on and every containment/structural edge (contains_step,
// contains_sql_statement, calls_macro, defined_in, has_control_flow,
// conditional_candidate, passes_parameter) are never walked — see
// wayfinder/spec-traversal-legible-filter-results.md's "Traversal edge set"
// decision: those edges either duplicate the lineage path at a different
// hop count (depends_on) or collapse the whole program to ~4 hops via hub
// nodes (containment). template_derived-flagged mirror edges of the five
// propagating types ARE walked like any other edge of that type — the flag
// is not a branch condition here, only in ticket 01's click-info panel.

const PROPAGATING_TYPES = new Set([
  "reads_dataset",
  "writes_dataset",
  "reads_external_file",
  "writes_external_file",
  "implemented_by",
]);

/**
 * @param {any[]} edges - render-model edges (ticket 01 convertGraph shape)
 * @returns {Map<string, Set<string>>} undirected adjacency over the
 *   restricted lineage edge set only
 */
function buildAdjacency(edges) {
  /** @type {Map<string, Set<string>>} */
  const adjacency = new Map();
  const link = (a, b) => {
    if (!adjacency.has(a)) adjacency.set(a, new Set());
    adjacency.get(a).add(b);
  };
  for (const e of edges) {
    if (!PROPAGATING_TYPES.has(e.type)) continue;
    link(e.from, e.to);
    link(e.to, e.from);
  }
  return adjacency;
}

/**
 * Single-source BFS bounded by `ceiling`, over the restricted adjacency.
 * @param {Map<string, Set<string>>} adjacency
 * @param {string} sourceId
 * @param {number} ceiling
 * @returns {Map<string, number>} nodeId -> distance from sourceId, for
 *   every node reachable within ceiling (sourceId itself maps to 0)
 */
function boundedBfs(adjacency, sourceId, ceiling) {
  const dist = new Map([[sourceId, 0]]);
  let frontier = [sourceId];
  for (let hop = 1; hop <= ceiling && frontier.length; hop++) {
    const next = [];
    for (const nodeId of frontier) {
      const neighbors = adjacency.get(nodeId);
      if (!neighbors) continue;
      for (const neighbor of neighbors) {
        if (dist.has(neighbor)) continue;
        dist.set(neighbor, hop);
        next.push(neighbor);
      }
    }
    frontier = next;
  }
  return dist;
}

function pairKey(a, b) {
  return [a, b].sort().join("|");
}

/**
 * Compute pairwise hop distance between every pair of `hitIds`, walking only
 * the five lineage-carrying edge types (see module doc comment above),
 * bounded by `ceiling`.
 *
 * Return shape (fixed — tickets 03 and 04 both consume this as-is):
 *   {
 *     distances:    Map<nodeId, number>   — shortest distance from the
 *                   nearest hit, for every node reachable within `ceiling`
 *                   from ANY hit (hits themselves map to 0).
 *     bridgeNodeIds: Set<nodeId>          — non-hit nodes lying on a
 *                   shortest path between two hits, within `ceiling`.
 *     pairStatus:   Map<string, "reachable"|"disconnected"> — one entry per
 *                   distinct pair of hits, keyed by `${a}|${b}` with a/b
 *                   sorted so the key is order-independent. Every pair is
 *                   present, never omitted, even when disconnected.
 *   }
 *
 * Single-hit input degenerates to full bounded lineage in both directions
 * from that one hit: `distances` is that hit's bounded BFS, `bridgeNodeIds`
 * is empty (no pair to bridge between), `pairStatus` is empty (no pair
 * exists).
 *
 * @param {any[]} nodes - render-model nodes (ticket 01 convertGraph shape)
 * @param {any[]} edges - render-model edges (ticket 01 convertGraph shape)
 * @param {Iterable<string>} hitIds - hit node ids (array or Set)
 * @param {number} ceiling - max hop distance to search
 * @returns {{ distances: Map<string, number>, bridgeNodeIds: Set<string>, pairStatus: Map<string, "reachable"|"disconnected"> }}
 */
function computeHopDistances(nodes, edges, hitIds, ceiling) {
  const hits = [...new Set(hitIds)];
  const adjacency = buildAdjacency(edges);

  // One bounded BFS per hit — a single multi-source BFS would give
  // distance-to-nearest-hit but cannot recover per-hit distances, which
  // bridge-node detection and pairStatus both need.
  const bfsByHit = new Map(hits.map((hitId) => [hitId, boundedBfs(adjacency, hitId, ceiling)]));

  const distances = new Map();
  for (const hitId of hits) distances.set(hitId, 0);
  for (const bfsResult of bfsByHit.values()) {
    for (const [nodeId, dist] of bfsResult) {
      const current = distances.get(nodeId);
      if (current === undefined || dist < current) distances.set(nodeId, dist);
    }
  }

  const bridgeNodeIds = new Set();
  const pairStatus = new Map();

  for (let i = 0; i < hits.length; i++) {
    for (let j = i + 1; j < hits.length; j++) {
      const a = hits[i];
      const b = hits[j];
      const key = pairKey(a, b);
      const distA = bfsByHit.get(a);
      const distB = bfsByHit.get(b);
      const abDist = distA.get(b);

      if (abDist === undefined) {
        pairStatus.set(key, "disconnected");
        continue;
      }
      pairStatus.set(key, "reachable");

      // A non-hit node v is a bridge between a and b iff it sits on a
      // shortest a<->b path: distA(v) + distB(v) === abDist.
      for (const [nodeId, dv] of distA) {
        if (hits.includes(nodeId)) continue;
        const dvb = distB.get(nodeId);
        if (dvb !== undefined && dv + dvb === abDist) {
          bridgeNodeIds.add(nodeId);
        }
      }
    }
  }

  return { distances, bridgeNodeIds, pairStatus };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { computeHopDistances, PROPAGATING_TYPES };
}
if (typeof window !== "undefined") {
  window.computeHopDistances = computeHopDistances;
  window.PROPAGATING_TYPES = PROPAGATING_TYPES;
}
