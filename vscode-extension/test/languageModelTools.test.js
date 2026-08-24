"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createSafeModelError } = require("../src/cliRunner.js");

const {
  searchCliArgs,
  lineageCliArgs,
  impactCliArgs,
  compactJson,
  safeErrorText,
} = require("../src/languageModelTools.js");

function modelSafeError(message) {
  return createSafeModelError(message);
}

test("search input maps directly to the query CLI flag", () => {
  assert.deepEqual(searchCliArgs({ query: "macro" }), ["--query", "macro"]);
});

test("lineage input maps node and optional direction flags", () => {
  assert.deepEqual(lineageCliArgs({ node: "dataset:work.a" }), ["--node", "dataset:work.a"]);
  assert.deepEqual(lineageCliArgs({ node: "step:001", direction: "upstream" }), [
    "--node", "step:001", "--direction", "upstream",
  ]);
});

test("impact input maps directly to the variable CLI flag", () => {
  assert.deepEqual(impactCliArgs({ variable: "variable:work.a.flag" }), [
    "--variable", "variable:work.a.flag",
  ]);
});

test("CLI data is returned as compact JSON text without changing its shape", () => {
  assert.equal(compactJson({ start: "dataset:work.a", nodes: [] }), '{"start":"dataset:work.a","nodes":[]}');
});

test("tool argument helpers reject malformed runtime input", () => {
  assert.throws(() => searchCliArgs(null), /input must be an object/);
  assert.throws(() => searchCliArgs([]), /input must be an object/);
  assert.throws(() => searchCliArgs({ query: 42 }), /query must be a string/);
  assert.throws(() => lineageCliArgs({ node: 42 }), /node must be a string/);
  assert.throws(() => lineageCliArgs({ node: "step:001", direction: "sideways" }), /direction/);
  assert.throws(() => impactCliArgs({ variable: null }), /variable must be a string/);
});

test("model-facing errors redact absolute paths and stay on one line", () => {
  const text = safeErrorText(new Error(
    "failed at C:\\workspace\\graph.json\nsecond line",
  ));

  assert.equal(text, "error: SAS Graph query failed.");
  assert.ok(!text.includes("workspace"));
});

test("tagged resolver failures remain fixed and path-free", () => {
  const cases = [
    [
      "SAS Graph: no workspace folder is open. Open the folder containing project.yaml first.",
      "error: SAS Graph: no workspace folder is open. Open the folder containing project.yaml first.",
    ],
    [
      "SAS Graph: no project.yaml found in the workspace. Run sas-graph in a workspace with a project.yaml, or choose one manually.",
      "error: SAS Graph: no project.yaml found in the workspace. Run sas-graph in a workspace with a project.yaml, or choose one manually.",
    ],
    [
      "SAS Graph: project.yaml does not declare output_dir.",
      "error: SAS Graph: project.yaml does not declare output_dir.",
    ],
    [
      "SAS Graph: no runs found. Run \"sas-graph run\" first, then try again.",
      "error: SAS Graph: no runs found. Run \"sas-graph run\" first, then try again.",
    ],
    [
      "SAS Graph: latest run has no graph.json. Run \"sas-graph run\" again.",
      "error: SAS Graph: latest run has no graph.json. Run \"sas-graph run\" again.",
    ],
  ];

  for (const [message, expected] of cases) {
    const text = safeErrorText(modelSafeError(message));
    assert.equal(text, expected);
  }
});

test("unknown paths and credentials fail closed without dangling punctuation", () => {
  const cases = [
    "unexpected failure at C:\\workspace\\Project. Secret\\graph_runs (permission denied) and retry",
    "unexpected failure (/workspace/Project. Secret/graph_runs) and retry",
    "unexpected failure at /workspace/Project. Secret/graph_runs (permission denied) and retry",
    "unexpected failure at .\\Secrets\\graph.json",
    "unexpected failure at C:relative\\Secrets\\graph.json",
    "unexpected failure at relative\\secret\\file.sas",
    "unexpected failure",
    "unexpected failure: API_KEY=top-secret; password='hunter2'",
    "unexpected failure: AWS_SECRET_ACCESS_KEY=top-secret",
    "unexpected failure: Authorization: Bearer abc.def.ghi",
    "unexpected failure: Authorization=Bearer abc.def.ghi",
    "unexpected failure: Authorization: Basic abc123",
    "unexpected failure: Authorization=Basic abc123",
    "unexpected failure: accessToken=top-secret",
    "unexpected failure: accessToken=",
    "unexpected failure: clientSecret=top-secret",
    "unexpected failure: clientSecret=",
    "unexpected failure: API KEY=top-secret",
    "lineage start must be Dataset, Step, or SqlStatement",
  ];

  for (const message of cases) {
    assert.equal(safeErrorText(new Error(message)), "error: SAS Graph query failed.");
  }
});

test("only tagged controlled messages remain readable", () => {
  assert.equal(
    safeErrorText(modelSafeError(
      "sas-graph query-lineage failed (exit code 1): lineage start must be Dataset, Step, or SqlStatement",
    )),
    "error: sas-graph query-lineage failed (exit code 1): lineage start must be Dataset, Step, or SqlStatement",
  );
});

test("forged resolver prefixes are not trusted as safe messages", () => {
  assert.equal(
    safeErrorText(new Error(
      "SAS Graph: no project.yaml found at the workspace root (C:\\private\\project.yaml). API_KEY=secret",
    )),
    "error: SAS Graph query failed.",
  );
});

test("forged safe provenance fails closed for unknown relative secrets", () => {
  const error = new Error("unexpected failure at .\\Secrets\\api-token.txt");
  Object.defineProperty(error, Symbol.for("sasGraph.modelSafeError"), { value: true });

  assert.equal(safeErrorText(error), "error: SAS Graph query failed.");
});
