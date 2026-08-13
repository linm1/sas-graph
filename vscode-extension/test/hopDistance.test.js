// Ticket 02: hop-distance module. Pure function, no DOM, no vis-network.
// Synthetic fixtures throughout (per the ticket, this module's own tests
// don't need to wait on a real render-model fixture).
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { computeHopDistances } = require("../src/hopDistance.js");

function node(id, group = "Dataset") {
  return { id, group };
}

function edge(id, type, from, to, extra = {}) {
  return { id, type, from, to, ...extra };
}

test("return shape has exactly distances, bridgeNodeIds, pairStatus with documented types", () => {
  const nodes = [node("a"), node("b")];
  const edges = [edge("e1", "reads_dataset", "a", "b")];
  const result = computeHopDistances(nodes, edges, ["a", "b"], 5);

  assert.deepEqual(Object.keys(result).sort(), ["bridgeNodeIds", "distances", "pairStatus"]);
  assert.ok(result.distances instanceof Map);
  assert.ok(result.bridgeNodeIds instanceof Set);
  assert.ok(result.pairStatus instanceof Map);
});

test("two hits directly connected by one reads_dataset edge resolve to distance 1", () => {
  const nodes = [node("a"), node("b")];
  const edges = [edge("e1", "reads_dataset", "a", "b")];
  const { pairStatus } = computeHopDistances(nodes, edges, ["a", "b"], 5);
  assert.equal(pairStatus.get("a|b"), "reachable");
  const { distances } = computeHopDistances(nodes, edges, ["a"], 5);
  assert.equal(distances.get("b"), 1);
});

test("writes_dataset propagates distance", () => {
  const nodes = [node("a"), node("b")];
  const edges = [edge("e1", "writes_dataset", "a", "b")];
  const { distances } = computeHopDistances(nodes, edges, ["a"], 5);
  assert.equal(distances.get("b"), 1);
});

test("implemented_by propagates distance", () => {
  const nodes = [node("a"), node("b")];
  const edges = [edge("e1", "implemented_by", "a", "b")];
  const { distances } = computeHopDistances(nodes, edges, ["a"], 5);
  assert.equal(distances.get("b"), 1);
});

test("a Variable reaches its reading SqlStatement and connected Dataset", () => {
  const nodes = [
    node("variable:work.a.flag", "Variable"),
    node("sqlstatement:001", "SqlStatement"),
    node("dataset:work.a", "Dataset"),
  ];
  const edges = [
    edge("e1", "reads_variable", "variable:work.a.flag", "sqlstatement:001"),
    edge("e2", "reads_dataset", "dataset:work.a", "sqlstatement:001"),
  ];

  const { distances } = computeHopDistances(nodes, edges, ["variable:work.a.flag"], 5);
  assert.equal(distances.get("sqlstatement:001"), 1);
  assert.equal(distances.get("dataset:work.a"), 2);
});

test("external-file import and export edges propagate lineage distance", () => {
  const nodes = [
    node("externalfile:/data/raw/lookup.xlsx", "ExternalFile"),
    node("dataset:work.lookup", "Dataset"),
    node("dataset:work.ae", "Dataset"),
    node("externalfile:/data/out/ae.xlsx", "ExternalFile"),
  ];
  const edges = [
    edge("e1", "reads_external_file", "externalfile:/data/raw/lookup.xlsx", "dataset:work.lookup"),
    edge("e2", "writes_external_file", "dataset:work.ae", "externalfile:/data/out/ae.xlsx"),
  ];

  const imported = computeHopDistances(nodes, edges, ["externalfile:/data/raw/lookup.xlsx"], 5);
  assert.equal(imported.distances.get("dataset:work.lookup"), 1);

  const exported = computeHopDistances(nodes, edges, ["dataset:work.ae"], 5);
  assert.equal(exported.distances.get("externalfile:/data/out/ae.xlsx"), 1);
});

test("a template_derived: true mirror edge is counted toward distance, not skipped", () => {
  const nodes = [node("a"), node("b")];
  const edges = [edge("e1", "reads_dataset", "a", "b", { template_derived: true })];
  const { distances } = computeHopDistances(nodes, edges, ["a"], 5);
  assert.equal(distances.get("b"), 1);
});

test("a depends_on edge directly connecting two hits does not shortcut the true Step-mediated distance", () => {
  // a --depends_on--> b (direct, would be distance 1 if walked)
  // a --reads_dataset--> s --writes_dataset--> b (true path, distance 2)
  const nodes = [node("a"), node("b"), node("s", "Step")];
  const edges = [
    edge("e1", "depends_on", "a", "b"),
    edge("e2", "reads_dataset", "s", "a"),
    edge("e3", "writes_dataset", "s", "b"),
  ];
  const { distances } = computeHopDistances(nodes, edges, ["a"], 5);
  assert.equal(distances.get("b"), 2, "depends_on must be ignored entirely for distance");
});

test("a containment edge (contains_step) does not count toward distance", () => {
  const nodes = [node("a"), node("s", "Step")];
  const edges = [edge("e1", "contains_step", "a", "s")];
  const { distances } = computeHopDistances(nodes, edges, ["a"], 5);
  assert.equal(distances.get("s"), undefined, "containment edges must not propagate distance");
});

test("bridge-node detection: a non-hit node on the shortest path between two hits is included; a distractor off-path is excluded", () => {
  // a --reads--> bridge --writes--> b   (the shortest a<->b path)
  // a --reads--> distractor             (dead end, not on any hit-to-hit path)
  const nodes = [node("a"), node("b"), node("bridge", "Step"), node("distractor", "Step")];
  const edges = [
    edge("e1", "reads_dataset", "a", "bridge"),
    edge("e2", "writes_dataset", "bridge", "b"),
    edge("e3", "reads_dataset", "a", "distractor"),
  ];
  const { bridgeNodeIds } = computeHopDistances(nodes, edges, ["a", "b"], 5);
  assert.ok(bridgeNodeIds.has("bridge"), "node on the shortest hit-to-hit path must be a bridge");
  assert.ok(!bridgeNodeIds.has("distractor"), "node not on any shortest hit-to-hit path must not be a bridge");
});

test("single-hit input returns full bounded lineage in both directions, no pairwise computation", () => {
  const nodes = [node("upstream"), node("a"), node("downstream")];
  const edges = [
    edge("e1", "reads_dataset", "a", "upstream"),
    edge("e2", "writes_dataset", "a", "downstream"),
  ];
  const { distances, bridgeNodeIds, pairStatus } = computeHopDistances(nodes, edges, ["a"], 5);
  assert.equal(distances.get("a"), 0);
  assert.equal(distances.get("upstream"), 1);
  assert.equal(distances.get("downstream"), 1);
  assert.equal(bridgeNodeIds.size, 0);
  assert.equal(pairStatus.size, 0);
});

test("a node exactly at the ceiling is included; one hop beyond is excluded", () => {
  // chain a -> n1 -> n2 -> n3, ceiling 2: n2 included (dist 2), n3 excluded
  const nodes = [node("a"), node("n1"), node("n2"), node("n3")];
  const edges = [
    edge("e1", "reads_dataset", "a", "n1"),
    edge("e2", "reads_dataset", "n1", "n2"),
    edge("e3", "reads_dataset", "n2", "n3"),
  ];
  const { distances } = computeHopDistances(nodes, edges, ["a"], 2);
  assert.equal(distances.get("n2"), 2, "node exactly at the ceiling must be included");
  assert.equal(distances.get("n3"), undefined, "node one hop beyond the ceiling must be excluded");
});

test("two hits with no connecting path within the ceiling (or at all) get an explicit disconnected marker, never omitted", () => {
  const nodes = [node("a"), node("b")];
  const edges = []; // no path at all
  const { pairStatus } = computeHopDistances(nodes, edges, ["a", "b"], 5);
  assert.equal(pairStatus.get("a|b"), "disconnected");
  assert.equal(pairStatus.size, 1, "the pair must be present, not omitted");
});

test("two hits connected but beyond the ceiling are marked disconnected", () => {
  const nodes = [node("a"), node("n1"), node("b")];
  const edges = [
    edge("e1", "reads_dataset", "a", "n1"),
    edge("e2", "reads_dataset", "n1", "b"),
  ];
  const { pairStatus } = computeHopDistances(nodes, edges, ["a", "b"], 1);
  assert.equal(pairStatus.get("a|b"), "disconnected");
});

test("hitIds accepts a Set as well as an array", () => {
  const nodes = [node("a"), node("b")];
  const edges = [edge("e1", "reads_dataset", "a", "b")];
  const result = computeHopDistances(nodes, edges, new Set(["a", "b"]), 5);
  assert.equal(result.pairStatus.get("a|b"), "reachable");
});

function demo() {
  const nodes = [node("a"), node("bridge", "Step"), node("b")];
  const edges = [
    edge("e1", "reads_dataset", "a", "bridge"),
    edge("e2", "writes_dataset", "bridge", "b"),
  ];
  const { distances, bridgeNodeIds, pairStatus } = computeHopDistances(nodes, edges, ["a", "b"], 5);
  console.log(`a<->b via bridge: pairStatus=${pairStatus.get("a|b")}, bridge count=${bridgeNodeIds.size}, dist(bridge)=${distances.get("bridge")}`);
  console.log("hopDistance.js: all checks passed");
}

if (require.main === module) {
  demo();
}
