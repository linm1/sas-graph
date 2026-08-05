// Ticket 08: library/step-type filter predicate. Pure function, no DOM.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { deriveLibrary, filtersAllow } = require("../src/libraryFilter.js");
const { convertGraph } = require("../src/convert.js");

const FIXTURE_PATH = path.resolve(__dirname, "..", "..", "examples", "hand_written_graph.json");

function loadRenderModel() {
  const graph = JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf-8"));
  return convertGraph(graph);
}

test("deriveLibrary reads the id-prefix libref for Dataset nodes", () => {
  assert.equal(deriveLibrary({ id: "dataset:sdtm.ae", group: "Dataset" }), "sdtm");
  assert.equal(deriveLibrary({ id: "dataset:adam.adsl", group: "Dataset" }), "adam");
  assert.equal(deriveLibrary({ id: "dataset:work.adae_pre", group: "Dataset" }), "work");
});

test("deriveLibrary returns null for unknowndataset:&unknown_out. (no libref.member shape) — correctly excluded, not mismatched", () => {
  assert.equal(deriveLibrary({ id: "unknowndataset:&unknown_out.", group: "UnknownDataset" }), null);
});

test("deriveLibrary returns null for non-Dataset/UnknownDataset types even if the id superficially looks like <a>:<b>.<c>", () => {
  // program:adae.sas and setup:setup.sas both look like `<type>:<a>.<b>` on
  // a naive split, but Program/SetupFile are not in the library-eligible
  // type set, so they must never surface a bogus library.
  assert.equal(deriveLibrary({ id: "program:adae.sas", group: "Program" }), null);
  assert.equal(deriveLibrary({ id: "setup:setup.sas", group: "SetupFile" }), null);
  assert.equal(deriveLibrary({ id: "macrocall:003", group: "MacroCall" }), null);
});

test("filtersAllow: step-type filter shows only nodes of that type", () => {
  const { nodes } = loadRenderModel();
  const shown = nodes.filter((n) => filtersAllow(n, { library: null, type: "MacroCall" }));
  assert.deepEqual(shown.map((n) => n.id).sort(), ["macrocall:003", "macrocall:004"]);
});

test("filtersAllow: library filter shows matching-library nodes AND every node with no derivable library", () => {
  const { nodes } = loadRenderModel();
  const shown = nodes.filter((n) => filtersAllow(n, { library: "sdtm", type: null }));
  const shownIds = new Set(shown.map((n) => n.id));

  assert.ok(shownIds.has("dataset:sdtm.ae"));
  // other datasets with a DIFFERENT derivable library must be hidden
  assert.ok(!shownIds.has("dataset:adam.adsl"));
  assert.ok(!shownIds.has("dataset:work.adae_pre"));
  // non-library nodes stay visible regardless (placeholder rule)
  assert.ok(shownIds.has("program:adae.sas"));
  assert.ok(shownIds.has("step:001"));
  assert.ok(shownIds.has("macrocall:003"));
  // unknowndataset has no derivable library -> stays visible too
  assert.ok(shownIds.has("unknowndataset:&unknown_out."));
});

test("filtersAllow: library and type filters combine with AND", () => {
  const { nodes } = loadRenderModel();
  const shown = nodes.filter((n) => filtersAllow(n, { library: "adam", type: "Dataset" }));
  assert.deepEqual(shown.map((n) => n.id).sort(), ["dataset:adam.adae", "dataset:adam.adsl"]);
});

test("filtersAllow: no filters set (both null) allows every node", () => {
  const { nodes } = loadRenderModel();
  const shown = nodes.filter((n) => filtersAllow(n, { library: null, type: null }));
  assert.equal(shown.length, nodes.length);
});

function demo() {
  const { nodes } = loadRenderModel();
  const sdtmShown = nodes.filter((n) => filtersAllow(n, { library: "sdtm", type: null }));
  console.log(`library=sdtm -> ${sdtmShown.length} nodes visible (of ${nodes.length})`);
  console.log("libraryFilter.js: all checks passed");
}

if (require.main === module) {
  demo();
}
