"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { loadGraphFromPath } = require("../src/graphLoader.js");
const { lookupInnermostNode } = require("../src/lineIndex.js");

const FIXTURE_PATH = path.resolve(__dirname, "..", "..", "examples", "hand_written_graph.json");

test("rejects an unsupported schema before building a line index", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sas-graph-loader-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const graphPath = path.join(dir, "graph.json");
  fs.writeFileSync(graphPath, JSON.stringify({ schema_version: "0.1.0", nodes: {} }));

  const result = loadGraphFromPath(graphPath);

  assert.match(result.error, /schema_version.*0\.2\.0/i);
  assert.equal(result.graphJson, undefined);
  assert.equal(result.lineIndex, undefined);
});

test("accepts schema 0.2.0 and builds the cursor index", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sas-graph-loader-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const graphPath = path.join(dir, "graph.json");
  fs.writeFileSync(graphPath, JSON.stringify({
    schema_version: "0.2.0",
    nodes: [{ id: "step:001", type: "Step", source: { file: "a.sas", line_start: 1, line_end: 1 } }],
  }));

  const result = loadGraphFromPath(graphPath);

  assert.equal(result.error, undefined);
  assert.equal(result.graphJson.schema_version, "0.2.0");
  assert.equal(result.lineIndex.get("a.sas")[0].id, "step:001");
});

test("loaded graph's cursor index resolves the expected node", () => {
  const result = loadGraphFromPath(FIXTURE_PATH);

  assert.equal(result.error, undefined);
  assert.equal(lookupInnermostNode(result.lineIndex, "adae.sas", 2)?.id, "step:001");
});
