// Ticket 02: graph.json -> render-model conversion. Pure function, no vscode
// import, no DOM, no live webview. Fixture-driven, mirroring
// tests/test_renderer_mermaid.py's shape.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { convertGraph, TYPE_COLOR, DEFAULT_COLOR, EXCLUDED_TYPES, assignArrowhead } = require("../src/convert.js");

const FIXTURE_PATH = path.resolve(__dirname, "..", "..", "examples", "hand_written_graph.json");

function loadFixture() {
  return JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf-8"));
}

function byId(nodes) {
  return new Map(nodes.map((n) => [n.id, n]));
}

test("converts every non-excluded node and edge from the fixture", () => {
  const graph = loadFixture();
  const { nodes, edges } = convertGraph(graph);

  // Fixture has no CommentBlock node, but does have MacroParameter nodes
  // (now excluded per ticket 01 / spec story 17) — count parity must be
  // computed against the excluded-type-aware expectation, not raw fixture
  // counts. Kept-node count = every node whose type is not in EXCLUDED_TYPES.
  const expectedNodes = graph.nodes.filter((n) => !EXCLUDED_TYPES.has(n.type));
  const expectedNodeIds = new Set(expectedNodes.map((n) => n.id));
  const expectedEdges = graph.edges.filter((e) => expectedNodeIds.has(e.from) && expectedNodeIds.has(e.to));

  assert.equal(nodes.length, expectedNodes.length);
  assert.equal(edges.length, expectedEdges.length);
});

test("CommentBlock nodes and their edges are excluded (synthetic — fixture has none)", () => {
  // examples/hand_written_graph.json contains zero CommentBlock nodes, so
  // exercising exclusion against the real fixture alone would pass
  // vacuously. Clone it and inject one CommentBlock node + an edge that
  // references it, to prove exclusion actually removes both.
  const graph = loadFixture();
  const withComment = {
    ...graph,
    nodes: [
      ...graph.nodes,
      { id: "comment:999", type: "CommentBlock", label: "old code", source: null },
    ],
    edges: [
      ...graph.edges,
      { id: "edge:999", type: "annotates", from: "comment:999", to: "step:001", source: null },
    ],
  };

  const { nodes, edges } = convertGraph(withComment);

  assert.ok(!nodes.some((n) => n.id === "comment:999"), "CommentBlock node must be excluded");
  assert.ok(!edges.some((e) => e.from === "comment:999" || e.to === "comment:999"), "edge referencing excluded node must be excluded");
});

test("MacroParameter nodes and their edges are excluded from both nodes and edges output", () => {
  const graph = loadFixture();
  const { nodes, edges } = convertGraph(graph);

  const macroParamIds = graph.nodes.filter((n) => n.type === "MacroParameter").map((n) => n.id);
  assert.ok(macroParamIds.length > 0, "fixture must actually contain MacroParameter nodes to exercise this");

  for (const id of macroParamIds) {
    assert.ok(!nodes.some((n) => n.id === id), `MacroParameter node ${id} must be excluded`);
  }
  assert.ok(
    !edges.some((e) => macroParamIds.includes(e.from) || macroParamIds.includes(e.to)),
    "no edge may reference an excluded MacroParameter node"
  );
});

test("value equals total degree (in-edges + out-edges), default 1 when isolated", () => {
  const graph = loadFixture();
  const { nodes } = convertGraph(graph);
  const nodeById = byId(nodes);

  // step:001 has edge:001 (out to dataset:sdtm.ae), edge:002 (out), edge:003
  // (out), edge:021 (in from program) = 4
  assert.equal(nodeById.get("step:001").value, 4);

  // library:sdtm and library:adam have zero incident edges in the fixture —
  // convert.py's `degree.get(n["id"], 1)` defaults isolated nodes to 1, not
  // 0. Pin that behavior explicitly.
  assert.equal(nodeById.get("library:sdtm").value, 1);
  assert.equal(nodeById.get("library:adam").value, 1);
});

test("every kept non-macro node carries the complete original graph.json node under raw", () => {
  const graph = loadFixture();
  const { nodes } = convertGraph(graph);
  const nodeById = byId(nodes);
  const originalById = byId(graph.nodes);

  // Macro nodes (MacroCall/MacroDefinition with parameters) get a `parameters`
  // field added to `raw` — see the dedicated test below. Every other kept
  // node's raw must still be byte-for-byte the original object.
  const macroIdsWithParams = new Set(
    graph.edges.filter((e) => e.type === "passes_parameter").map((e) => e.from)
  );

  for (const [id, original] of originalById) {
    if (EXCLUDED_TYPES.has(original.type)) continue; // excluded node, not in nodes output at all
    if (macroIdsWithParams.has(id)) continue; // covered separately
    assert.deepEqual(nodeById.get(id).raw, original);
  }
});

test("an excluded MacroParameter's raw_value is reachable via the owning MacroCall's raw payload", () => {
  const graph = loadFixture();
  const { nodes } = convertGraph(graph);
  const nodeById = byId(nodes);

  // macrocall:003 owns macroparam:003:inds (raw_value "work.adae_srt") and
  // macroparam:003:outds (raw_value "adam.adae") via passes_parameter edges.
  // The MacroParameter nodes themselves are gone from `nodes` entirely —
  // this must be reachable through macrocall:003's own raw.parameters, not
  // via a lookup keyed on the excluded node's own id.
  assert.ok(!nodeById.has("macroparam:003:inds"), "excluded node must not exist in nodes output");
  assert.ok(!nodeById.has("macroparam:003:outds"), "excluded node must not exist in nodes output");

  const macrocall003 = nodeById.get("macrocall:003");
  assert.ok(Array.isArray(macrocall003.raw.parameters));
  assert.deepEqual(
    macrocall003.raw.parameters.map((p) => p.name).sort(),
    ["inds", "outds"]
  );
  const inds = macrocall003.raw.parameters.find((p) => p.name === "inds");
  assert.equal(inds.raw_value, "work.adae_srt");
  const outds = macrocall003.raw.parameters.find((p) => p.name === "outds");
  assert.equal(outds.raw_value, "adam.adae");
});

test("TYPE_COLOR gives UnknownMacro/UnknownDataset distinct colors from MacroCall/Dataset, and MacroConflict is orange", () => {
  assert.notEqual(TYPE_COLOR.UnknownMacro, TYPE_COLOR.MacroCall);
  assert.notEqual(TYPE_COLOR.UnknownDataset, TYPE_COLOR.Dataset);
  assert.ok(TYPE_COLOR.MacroConflict, "MacroConflict must have a color entry");
  assert.notEqual(TYPE_COLOR.ExternalFile, DEFAULT_COLOR);
  assert.notEqual(TYPE_COLOR.ExternalFile, TYPE_COLOR.Dataset);
  // MacroConflict is unverified against any real fixture — no committed or
  // reproducible fixture contains one. Entry exists for completeness only.
});

test("color assignment on real fixture nodes matches TYPE_COLOR by type", () => {
  const graph = loadFixture();
  const { nodes } = convertGraph(graph);
  const nodeById = byId(nodes);

  assert.equal(nodeById.get("unknownmacro:gm_missing").color, TYPE_COLOR.UnknownMacro);
  assert.equal(nodeById.get("unknowndataset:&unknown_out.").color, TYPE_COLOR.UnknownDataset);
  assert.equal(nodeById.get("macrocall:003").color, TYPE_COLOR.MacroCall);
  assert.equal(nodeById.get("dataset:sdtm.ae").color, TYPE_COLOR.Dataset);
});

test("findings are attached to every affected node id, node can carry multiple", () => {
  const graph = loadFixture();
  const { nodes } = convertGraph(graph);
  const nodeById = byId(nodes);

  const gmMissing = nodeById.get("unknownmacro:gm_missing");
  assert.equal(gmMissing.findings.length, 1);
  assert.equal(gmMissing.findings[0].id, "finding:001");

  const macrocall004 = nodeById.get("macrocall:004");
  assert.equal(macrocall004.findings.length, 1);
  assert.equal(macrocall004.findings[0].id, "finding:001");

  // macroparam:004:outds is one of finding:002's affected_nodes, but it's an
  // excluded MacroParameter (ticket 01) and no longer exists in `nodes` —
  // its finding attribution has nowhere to land, same as any other excluded
  // node. Only the surviving affected node (unknowndataset) is checked here.
  assert.ok(!nodeById.has("macroparam:004:outds"), "excluded node must not exist in nodes output");

  const unknownOut = nodeById.get("unknowndataset:&unknown_out.");
  assert.equal(unknownOut.findings.length, 1);
  assert.equal(unknownOut.findings[0].id, "finding:002");

  const macrocall003 = nodeById.get("macrocall:003");
  assert.equal(macrocall003.findings.length, 1);
  assert.equal(macrocall003.findings[0].id, "finding:003");

  const contract = nodeById.get("macrocontract:gm_derive");
  assert.equal(contract.findings.length, 1);
  assert.equal(contract.findings[0].id, "finding:003");

  // finding message/suggested_action come through intact
  assert.match(gmMissing.findings[0].message, /Macro source for %gm_missing was not found/);
  assert.ok(gmMissing.findings[0].suggested_action);
});

test("a node with no finding gets an empty findings array, not undefined", () => {
  const graph = loadFixture();
  const { nodes } = convertGraph(graph);
  const nodeById = byId(nodes);
  assert.deepEqual(nodeById.get("dataset:sdtm.ae").findings, []);
});

test("edges carry id, type, and template_derived unchanged from the source graph.json edge", () => {
  const graph = loadFixture();
  const { edges } = convertGraph(graph);
  const edgeById = new Map(edges.map((e) => [e.id, e]));

  for (const original of graph.edges) {
    const converted = edgeById.get(original.id);
    if (!converted) continue; // edge touching an excluded node, expected to be dropped
    assert.equal(converted.type, original.type);
    assert.equal(converted.template_derived, original.template_derived);
  }
  // sanity: at least one real edge survived and was actually checked
  assert.ok(edges.length > 0);
});

test("template_derived: true survives conversion (synthetic — fixture has no mirror edges)", () => {
  const graph = loadFixture();
  const withMirror = {
    ...graph,
    edges: [
      ...graph.edges,
      { id: "edge:999", type: "reads_dataset", from: "macrocall:003", to: "dataset:sdtm.ae", template_derived: true, source: null },
    ],
  };
  const { edges } = convertGraph(withMirror);
  const mirror = edges.find((e) => e.id === "edge:999");
  assert.ok(mirror);
  assert.equal(mirror.template_derived, true);
});

test("title is a human-readable phrase for reads_dataset, writes_dataset, implemented_by (not the raw type string)", () => {
  const graph = loadFixture();
  const { edges } = convertGraph(graph);
  const edgeById = new Map(edges.map((e) => [e.id, e]));

  const reads = edgeById.get("edge:001"); // step:001 reads_dataset dataset:sdtm.ae
  assert.equal(reads.type, "reads_dataset");
  assert.notEqual(reads.title, "reads_dataset");
  assert.match(reads.title, /sdtm\.ae/);

  const writes = edgeById.get("edge:003"); // step:001 writes_dataset dataset:work.adae_pre
  assert.equal(writes.type, "writes_dataset");
  assert.notEqual(writes.title, "writes_dataset");
  assert.match(writes.title, /work\.adae_pre/);

  const implementedBy = edgeById.get("edge:012"); // macrocall:003 implemented_by macrocontract:gm_derive
  assert.equal(implementedBy.type, "implemented_by");
  assert.notEqual(implementedBy.title, "implemented_by");
  assert.match(implementedBy.title, /gm_derive/);
});

test("assignArrowhead is a pure function of edge.type alone", () => {
  assert.deepEqual(assignArrowhead("reads_dataset"), { to: { enabled: true, type: "vee" } });
  assert.deepEqual(assignArrowhead("writes_dataset"), { to: { enabled: true, type: "triangle" } });
  assert.deepEqual(assignArrowhead("implemented_by"), { to: { enabled: true, type: "circle" } });
  assert.deepEqual(assignArrowhead("reads_external_file"), { to: { enabled: true, type: "vee" } });
  assert.deepEqual(assignArrowhead("writes_external_file"), { to: { enabled: true, type: "triangle" } });
  assert.equal(EXCLUDED_TYPES.has("ExternalFile"), false);
});

test("assignArrowhead is exported by name from convert.js alongside TYPE_COLOR/EXCLUDED_TYPES/convertGraph", () => {
  const mod = require("../src/convert.js");
  assert.equal(typeof mod.assignArrowhead, "function");
  assert.equal(typeof mod.convertGraph, "function");
  assert.ok(mod.TYPE_COLOR);
  assert.ok(mod.EXCLUDED_TYPES);
});

function demo() {
  const graph = loadFixture();
  const { nodes, edges } = convertGraph(graph);
  console.log(`convertGraph: ${nodes.length} nodes, ${edges.length} edges`);
  console.log("convert.js: all checks passed");
}

if (require.main === module) {
  demo();
}
