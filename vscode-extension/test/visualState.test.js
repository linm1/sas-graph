// Ticket 03: band-table + hover-precedence module. Pure functions, no DOM.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  edgeBandOpacity,
  applyHoverToEdge,
  computeHitSet,
  nodeLassoOpacity,
  applyHoverToNode,
  hasNoLineage,
  deriveStatusMessage,
  IDLE_OPACITY,
  UNREACHABLE_OPACITY,
} = require("../src/visualState.js");

test("edgeBandOpacity: both endpoints hits (distance 0) resolves to 1.0", () => {
  assert.equal(edgeBandOpacity(0, 0), 1.0);
});

test("edgeBandOpacity: hop1/hop2/hop3/hop4+ each produce the exact documented value", () => {
  assert.equal(edgeBandOpacity(0, 1), 0.9); // hop1
  assert.equal(edgeBandOpacity(1, 1), 0.9);
  assert.equal(edgeBandOpacity(0, 2), 0.6); // hop2
  assert.equal(edgeBandOpacity(1, 2), 0.6);
  assert.equal(edgeBandOpacity(0, 3), 0.35); // hop3
  assert.equal(edgeBandOpacity(2, 3), 0.35);
  assert.equal(edgeBandOpacity(0, 4), 0.15); // hop4+, within ceiling
  assert.equal(edgeBandOpacity(4, 9), 0.15);
});

test("edgeBandOpacity: band picks max(distA, distB), the farther endpoint", () => {
  assert.equal(edgeBandOpacity(0, 3), edgeBandOpacity(3, 0));
  assert.equal(edgeBandOpacity(1, 3), 0.35);
});

test("edgeBandOpacity: an unreachable/disconnected endpoint (undefined distance) resolves to 0.03, not 0", () => {
  assert.equal(edgeBandOpacity(0, undefined), UNREACHABLE_OPACITY);
  assert.equal(edgeBandOpacity(undefined, undefined), UNREACHABLE_OPACITY);
  assert.notEqual(UNREACHABLE_OPACITY, 0);
});

test("edgeBandOpacity: a hit's own distance of 0 is not mistaken for falsy/missing (regression: must not use `|| 0`)", () => {
  // Both endpoints are hits themselves (distance 0) - must be hop0 (1.0),
  // not accidentally treated as "no distance data" -> unreachable.
  assert.equal(edgeBandOpacity(0, 0), 1.0);
});

test("applyHoverToEdge: non-incident edge keeps its band baseline unchanged", () => {
  assert.equal(applyHoverToEdge(0.6, false), 0.6);
  assert.equal(applyHoverToEdge(IDLE_OPACITY, false), IDLE_OPACITY);
});

test("applyHoverToEdge: incident edge is raised to 0.9 regardless of baseline, unless baseline is already 1.0 (hop0, never demoted)", () => {
  assert.equal(applyHoverToEdge(0.6, true), 0.9);
  assert.equal(applyHoverToEdge(0.35, true), 0.9);
  assert.equal(applyHoverToEdge(0.03, true), 0.9);
  assert.equal(applyHoverToEdge(1.0, true), 1.0, "hop0 must never be demoted by hover");
});

test("idle path: with no active search and no active filter, computeHitSet returns null (flat 0.5 applies, computeHopDistances not invoked)", () => {
  const nodes = [{ id: "a" }, { id: "b" }];
  const filtersAllow = () => true;
  const hitSet = computeHitSet(nodes, null, { library: null, type: null }, filtersAllow);
  assert.equal(hitSet, null);
});

test("hit-set formation: filter-only active, hit set equals every node passing filtersAllow", () => {
  const nodes = [{ id: "a", group: "Dataset" }, { id: "b", group: "Step" }];
  const filtersAllow = (n, filters) => n.group === filters.type;
  const hitSet = computeHitSet(nodes, null, { library: null, type: "Dataset" }, filtersAllow);
  assert.deepEqual([...hitSet], ["a"]);
});

test("hit-set formation: search-only active, hit set equals state.searchMatches exactly", () => {
  const nodes = [{ id: "a" }, { id: "b" }, { id: "c" }];
  const filtersAllow = () => true;
  const searchMatches = new Set(["a", "c"]);
  const hitSet = computeHitSet(nodes, searchMatches, { library: null, type: null }, filtersAllow);
  assert.deepEqual([...hitSet].sort(), ["a", "c"]);
});

test("hit-set formation: both search and filter active, hit set is the intersection (must pass both)", () => {
  const nodes = [{ id: "a", group: "Dataset" }, { id: "b", group: "Dataset" }, { id: "c", group: "Step" }];
  const filtersAllow = (n, filters) => n.group === filters.type;
  const searchMatches = new Set(["a", "c"]); // c passes search but not filter (type Step)
  const hitSet = computeHitSet(nodes, searchMatches, { library: null, type: "Dataset" }, filtersAllow);
  assert.deepEqual([...hitSet], ["a"]);
});

test("hit-set formation: click/cursor hit id wins over an active search, hit set is the single clicked node only (unify-cursor-select-into-hit-set)", () => {
  const nodes = [{ id: "a" }, { id: "b" }, { id: "c" }];
  const filtersAllow = () => true;
  const searchMatches = new Set(["a", "c"]);
  const hitSet = computeHitSet(nodes, searchMatches, { library: null, type: null }, filtersAllow, "b");
  assert.deepEqual([...hitSet], ["b"]);
});

test("hit-set formation: click/cursor hit id alone (no search, no filter) still produces a single-node hit set, not idle null", () => {
  const nodes = [{ id: "a" }, { id: "b" }];
  const filtersAllow = () => true;
  const hitSet = computeHitSet(nodes, null, { library: null, type: null }, filtersAllow, "a");
  assert.deepEqual([...hitSet], ["a"]);
});

test("nodeLassoOpacity: hit and bridge nodes are revealed (opacity 1), true non-match dims", () => {
  const hitIds = new Set(["a", "b"]);
  const bridgeNodeIds = new Set(["bridge"]);
  assert.equal(nodeLassoOpacity("a", hitIds, bridgeNodeIds), 1);
  assert.equal(nodeLassoOpacity("bridge", hitIds, bridgeNodeIds), 1);
  assert.equal(nodeLassoOpacity("unrelated", hitIds, bridgeNodeIds), 0.1);
});

test("nodeLassoOpacity: single-hit case reveals nodes on the hit's bounded lineage chain via `distances`, even with empty bridgeNodeIds", () => {
  const hitIds = new Set(["hit"]);
  const bridgeNodeIds = new Set(); // single hit: no pair to bridge between
  const distances = new Map([["hit", 0], ["lineageChild", 1]]);
  assert.equal(nodeLassoOpacity("lineageChild", hitIds, bridgeNodeIds, distances), 1);
});

test("nodeLassoOpacity: a node absent from `distances` (no path to any hit within ceiling) still dims as a true non-match", () => {
  const hitIds = new Set(["hit"]);
  const bridgeNodeIds = new Set();
  const distances = new Map([["hit", 0], ["lineageChild", 1]]);
  assert.equal(nodeLassoOpacity("unrelated", hitIds, bridgeNodeIds, distances), 0.1);
});

test("nodeLassoOpacity: omitting `distances` entirely preserves old hit-or-bridge-only behavior", () => {
  const hitIds = new Set(["a"]);
  const bridgeNodeIds = new Set();
  assert.equal(nodeLassoOpacity("a", hitIds, bridgeNodeIds), 1);
  assert.equal(nodeLassoOpacity("other", hitIds, bridgeNodeIds), 0.1);
});

test("applyHoverToNode: highlighted node always fully visible, others keep baseline", () => {
  assert.equal(applyHoverToNode(0.1, true), 1);
  assert.equal(applyHoverToNode(0.1, false), 0.1);
  assert.equal(applyHoverToNode(1, false), 1);
});

test("hasNoLineage: a hit node with zero edges of the propagating types has no lineage", () => {
  const propagatingTypes = new Set(["reads_dataset", "writes_dataset", "implemented_by"]);
  const edges = [{ type: "contains_step", from: "program", to: "isolated" }];
  assert.equal(hasNoLineage("isolated", edges, propagatingTypes), true);
});

test("hasNoLineage: a node with at least one propagating-type edge has lineage", () => {
  const propagatingTypes = new Set(["reads_dataset", "writes_dataset", "implemented_by"]);
  const edges = [{ type: "reads_dataset", from: "step", to: "connected" }];
  assert.equal(hasNoLineage("connected", edges, propagatingTypes), false);
});

const PROPAGATING_TYPES = new Set(["reads_dataset", "writes_dataset", "implemented_by"]);
const labelOf = (id) => id; // tests use id as label for simplicity

test("deriveStatusMessage: (a) mixed hit set (one no-lineage hit + one normal connected hit) shows the no-lineage message naming the isolated hit, not the default", () => {
  const edges = [{ type: "reads_dataset", from: "connected", to: "other" }];
  const msg = deriveStatusMessage(["isolated", "connected"], new Map(), edges, PROPAGATING_TYPES, 5, labelOf);
  assert.match(msg, /no lineage/i);
  assert.match(msg, /"isolated"/);
});

test("deriveStatusMessage: (b) a no-lineage hit is never also reported as part of a disconnected pair message", () => {
  // Mixed, not all-no-lineage, so the pair loop (and its noLineage(a)||noLineage(b)
  // skip) actually runs — a genuine test of condition 3, not the all-no-lineage
  // early return.
  const edges = [{ type: "reads_dataset", from: "a", to: "c" }]; // a has lineage, b does not
  const pairStatus = new Map([["a|b", "disconnected"]]);
  const msg = deriveStatusMessage(["a", "b"], pairStatus, edges, PROPAGATING_TYPES, 5, labelOf);
  assert.doesNotMatch(msg, /not connected/);
  assert.match(msg, /"b"/);
});

test("deriveStatusMessage: (c) all-no-lineage hit set replaces the default message entirely", () => {
  const edges = [];
  const msg = deriveStatusMessage(["a", "b"], new Map(), edges, PROPAGATING_TYPES, 5, labelOf);
  assert.match(msg, /no lineage/i);
});

test("deriveStatusMessage: (c) a normal connected hit set (no no-lineage hits, no disconnected pairs) returns null so the default node/edge-count message applies", () => {
  const edges = [{ type: "reads_dataset", from: "a", to: "b" }];
  const pairStatus = new Map([["a|b", "reachable"]]);
  const msg = deriveStatusMessage(["a", "b"], pairStatus, edges, PROPAGATING_TYPES, 5, labelOf);
  assert.equal(msg, null);
});

test("deriveStatusMessage: mixed set with a genuinely disconnected pair among the connected hits appends the no-lineage note instead of burying either signal", () => {
  // x and y both have lineage edges (so the x|y pair is eligible for the
  // disconnected message), "isolated" has none (triggers the no-lineage note).
  const edges = [
    { type: "reads_dataset", from: "x", to: "unreachedFromX" },
    { type: "reads_dataset", from: "y", to: "unreachedFromY" },
  ];
  const pairStatus = new Map([["x|y", "disconnected"]]);
  const msg = deriveStatusMessage(["x", "y", "isolated"], pairStatus, edges, PROPAGATING_TYPES, 5, labelOf);
  assert.match(msg, /not connected/);
  assert.match(msg, /no lineage/i);
  assert.match(msg, /"isolated"/);
});

test("deriveStatusMessage: a large no-lineage hit list caps named nodes at 2 and adds a '+N more' count", () => {
  const edges = [{ type: "reads_dataset", from: "connected", to: "other" }];
  const hitList = ["connected", "iso1", "iso2", "iso3"];
  const msg = deriveStatusMessage(hitList, new Map(), edges, PROPAGATING_TYPES, 5, labelOf);
  assert.match(msg, /"iso1"/);
  assert.match(msg, /"iso2"/);
  assert.match(msg, /1 more/);
});

function demo() {
  console.log(`edgeBandOpacity(0,2) -> ${edgeBandOpacity(0, 2)}`);
  console.log(`applyHoverToEdge(1.0, true) -> ${applyHoverToEdge(1.0, true)} (hop0 never demoted)`);
  console.log("visualState.js: all checks passed");
}

if (require.main === module) {
  demo();
}
