// Ticket 06: line-index construction + innermost-covering-node lookup.
// Pure functions, no vscode import, no DOM, no live editor.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { buildLineIndex, lookupInnermostNode } = require("../src/lineIndex.js");

const FIXTURE_PATH = path.resolve(__dirname, "..", "..", "examples", "hand_written_graph.json");

function loadFixtureRecords() {
  const graph = JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf-8"));
  return graph.nodes
    .filter((n) => n.source)
    .map((n) => ({
      id: n.id,
      type: n.type,
      file: n.source.file,
      line_start: n.source.line_start,
      line_end: n.source.line_end,
    }));
}

// --- (a) real fixture: a line covered by exactly one allowlisted node -----
test("(a) real fixture: line covered by exactly one allowlisted node returns that node", () => {
  const records = loadFixtureRecords();
  const index = buildLineIndex(records);
  // adae.sas line 2 is covered only by step:001 (Step, lines 1-3, allowlisted)
  // and program:adae.sas (Program, lines 1-13, not allowlisted).
  const result = lookupInnermostNode(index, "adae.sas", 2);
  assert.ok(result);
  assert.equal(result.id, "step:001");
});

// --- (b) synthetic: nested allowlisted ranges — the real fixture has none -
test("(b) synthetic: nested allowlisted ranges (narrower fully inside wider) returns the narrowest", () => {
  // examples/hand_written_graph.json has no nested-range pair among
  // allowlisted types — every overlap in the real fixture is an identical
  // range (see test (d)). This case is constructed synthetically to
  // exercise the narrowest-wins rule at all.
  const records = [
    { id: "outer:1", type: "Step", file: "synthetic.sas", line_start: 1, line_end: 20 },
    { id: "inner:1", type: "MacroCall", file: "synthetic.sas", line_start: 5, line_end: 7 },
  ];
  const index = buildLineIndex(records);
  const result = lookupInnermostNode(index, "synthetic.sas", 6);
  assert.ok(result);
  assert.equal(result.id, "inner:1");
});

// --- (c) real fixture: line covered only by non-allowlisted types --------
test("(c) real fixture: line covered only by non-allowlisted types (Program) returns none", () => {
  const records = loadFixtureRecords();
  const index = buildLineIndex(records);
  // adae.sas line 9 (a blank/comment line in the source) is covered only by
  // program:adae.sas (Program, lines 1-13) among indexed nodes — no
  // allowlisted node's range includes it.
  const result = lookupInnermostNode(index, "adae.sas", 9);
  assert.equal(result, null);
});

// --- (d) real fixture: former identical-range tie, now resolved by exclusion
test("(d) real fixture: line 11 tie between macrocall:003 and its MacroParameter children is resolved by MacroParameter's removal from ELIGIBLE_TYPES, not the placeholder tie-break", () => {
  // Was a genuine three-way tie (macrocall:003 / macroparam:003:inds /
  // macroparam:003:outds, all lines 11-11) before unify-cursor-select-into-
  // hit-set dropped MacroParameter from ELIGIBLE_TYPES (convert.js excludes
  // MacroParameter from the render model's canvas entirely, so a cursor
  // resolving to one could never be found by nodeById). Only macrocall:003
  // remains eligible, so this is now a deterministic single-candidate
  // result, not the placeholder tie-break described in lookupInnermostNode's
  // doc comment.
  const records = loadFixtureRecords();
  const index = buildLineIndex(records);
  const result = lookupInnermostNode(index, "adae.sas", 11);
  assert.ok(result);
  assert.equal(result.id, "macrocall:003");
});

test("lookup requires forward-slash file keys (caller's responsibility to normalize) — regression guard for Windows path separators", () => {
  // graph.json's source.file values always use forward slashes (e.g.
  // "contracts/gm_derive.yaml"). vscode.workspace.asRelativePath returns
  // backslashes for nested paths on Windows. extension.js normalizes with
  // .replace(/\\/g, "/") BEFORE calling lookupInnermostNode — this test
  // pins that the index itself is a plain string-keyed Map with no
  // separator normalization of its own, so callers on Windows MUST
  // normalize or lookups silently never match.
  const records = [
    { id: "nested:1", type: "MacroCall", file: "contracts/gm_derive.yaml", line_start: 5, line_end: 5 },
  ];
  const index = buildLineIndex(records);
  assert.ok(lookupInnermostNode(index, "contracts/gm_derive.yaml", 5), "forward-slash key must match");
  assert.equal(lookupInnermostNode(index, "contracts\\gm_derive.yaml", 5), null, "un-normalized backslash key must NOT match — proves normalization is required at the call site");
});

test("buildLineIndex skips records with no file or no line range", () => {
  const records = [
    { id: "a", type: "Dataset", file: null, line_start: null, line_end: null },
    { id: "b", type: "Step", file: "x.sas", line_start: 1, line_end: 1 },
  ];
  const index = buildLineIndex(records);
  assert.equal(index.get("x.sas").length, 1);
  assert.ok(!index.has(null));
});

test("lookupInnermostNode returns null for an unknown file", () => {
  const index = buildLineIndex(loadFixtureRecords());
  assert.equal(lookupInnermostNode(index, "nonexistent.sas", 1), null);
});

function demo() {
  const records = loadFixtureRecords();
  const index = buildLineIndex(records);
  console.log("line 2 ->", lookupInnermostNode(index, "adae.sas", 2)?.id);
  console.log("line 9 ->", lookupInnermostNode(index, "adae.sas", 9));
  console.log("line 11 (placeholder tie-break) ->", lookupInnermostNode(index, "adae.sas", 11)?.id);
  console.log("lineIndex.js: all checks passed");
}

if (require.main === module) {
  demo();
}
