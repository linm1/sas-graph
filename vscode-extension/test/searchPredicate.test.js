// Ticket 05: search match predicate. Pure function, no DOM, no webview.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { matchesSearch } = require("../src/searchPredicate.js");
const { convertGraph } = require("../src/convert.js");

const FIXTURE_PATH = path.resolve(__dirname, "..", "..", "examples", "hand_written_graph.json");

function loadRenderModel() {
  const graph = JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf-8"));
  return convertGraph(graph);
}

test("label-only match: query matches label text not present in the type", () => {
  const { nodes } = loadRenderModel();
  const gmDerive = nodes.find((n) => n.id === "macrocall:003");
  assert.equal(gmDerive.label, "%gm_derive");
  assert.ok(matchesSearch("gm_derive", gmDerive));
  assert.ok(!matchesSearch("gm_derive", { label: "adam.adae", group: "Dataset" }));
});

test("type-only match: 'macro' matches nodes whose type contains it even if label doesn't", () => {
  const { nodes } = loadRenderModel();
  // macroparam:003:inds (MacroParameter) is excluded from the render model
  // as of ticket 01 (spec story 17), so it can no longer serve as this
  // case's fixture node. macrovar:root@1 (type MacroVariable) has the same
  // shape: label "root" doesn't contain "macro", but group does.
  const macroVar = nodes.find((n) => n.id === "macrovar:root@1");
  assert.ok(macroVar, "macrovar:root@1 must survive conversion (not an excluded type)");
  assert.equal(macroVar.label, "root");
  assert.ok(!macroVar.label.toLowerCase().includes("macro"));
  assert.ok(matchesSearch("macro", macroVar), "must match via type/group, not label");
});

test("query matching zero nodes returns false for every node", () => {
  const { nodes } = loadRenderModel();
  const hits = nodes.filter((n) => matchesSearch("zzz_no_such_thing", n));
  assert.equal(hits.length, 0);
});

test("match is case-insensitive", () => {
  assert.ok(matchesSearch("GM_DERIVE", { label: "%gm_derive", group: "MacroCall" }));
  assert.ok(matchesSearch("macrocall", { label: "%gm_derive", group: "MacroCall" }));
});

function demo() {
  const { nodes } = loadRenderModel();
  const hits = nodes.filter((n) => matchesSearch("macro", n));
  console.log(`matchesSearch("macro") -> ${hits.length} hits`);
  console.log("searchPredicate.js: all checks passed");
}

if (require.main === module) {
  demo();
}
