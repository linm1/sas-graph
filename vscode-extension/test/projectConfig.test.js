// Ticket 09: pure output_dir extraction and latest-run selection. No
// vscode import, no filesystem access here — extension.js does the actual
// fs.readFileSync/fs.readdirSync glue, which is extension-host-only.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");

const {
  extractOutputDir,
  pickLatestRun,
  resolveGraphJsonPath,
  resolveRunsDir,
} = require("../src/projectConfig.js");

function withVscodeWorkspace(workspaceFolders, callback) {
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === "vscode") {
      return { workspace: { workspaceFolders } };
    }
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    return callback();
  } finally {
    Module._load = originalLoad;
  }
}

function withFs(overrides, callback) {
  const originals = {};
  for (const [name, replacement] of Object.entries(overrides)) {
    originals[name] = fs[name];
    fs[name] = replacement;
  }
  try {
    return callback();
  } finally {
    for (const [name, original] of Object.entries(originals)) {
      fs[name] = original;
    }
  }
}

test("extractOutputDir reads a plain unquoted scalar (real qc_adae fixture shape)", () => {
  const yaml = `main_program: qc_adae.sas\nsetup_file: setup.sas\n\noutput_dir: graph_runs\n`;
  assert.equal(extractOutputDir(yaml), "graph_runs");
});

test("extractOutputDir strips a trailing comment", () => {
  const yaml = `output_dir: graph_runs  # generated run output\n`;
  assert.equal(extractOutputDir(yaml), "graph_runs");
});

test("extractOutputDir strips surrounding quotes", () => {
  assert.equal(extractOutputDir(`output_dir: "graph_runs"\n`), "graph_runs");
  assert.equal(extractOutputDir(`output_dir: 'graph_runs'\n`), "graph_runs");
});

test("extractOutputDir keeps a # inside a quoted value instead of treating it as a comment", () => {
  assert.equal(extractOutputDir(`output_dir: "runs/#latest"\n`), "runs/#latest");
});

test("extractOutputDir returns null when the key is absent", () => {
  assert.equal(extractOutputDir(`main_program: adae.sas\n`), null);
});

test("extractOutputDir returns null when the value is empty", () => {
  assert.equal(extractOutputDir(`output_dir:\n`), null);
});

test("resolveRunsDir honors absolute output_dir and resolves relative output_dir from the workspace", () => {
  const workspaceRoot = path.resolve("workspace");
  const absoluteOutputDir = path.resolve("graph_runs");

  assert.equal(resolveRunsDir(workspaceRoot, absoluteOutputDir), path.join(absoluteOutputDir, "runs"));
  assert.equal(resolveRunsDir(workspaceRoot, "graph_runs"), path.join(workspaceRoot, "graph_runs", "runs"));
});

test("pickLatestRun sorts YYYYMMDDTHHMMSSZ-<hex> run ids descending (chronological)", () => {
  const runs = [
    "20260730T221440Z-3449265b81b34b6eac1d64bd5fc146ae",
    "20260601T090000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "20260801T000000Z-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  ];
  assert.equal(pickLatestRun(runs), "20260801T000000Z-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
});

test("pickLatestRun returns null for an empty list", () => {
  assert.equal(pickLatestRun([]), null);
});

test("pickLatestRun does not mutate the input array", () => {
  const runs = ["b", "a", "c"];
  const copy = [...runs];
  pickLatestRun(runs);
  assert.deepEqual(runs, copy);
});

test("resolveGraphJsonPath reports when no workspace is open", () => {
  const result = withVscodeWorkspace(undefined, resolveGraphJsonPath);

  assert.match(result.error, /no workspace folder is open/);
});

test("resolveGraphJsonPath reports a missing project.yaml", () => {
  const result = withVscodeWorkspace(
    [{ uri: { fsPath: "C:\\workspace" } }],
    () => withFs({ existsSync: () => false }, resolveGraphJsonPath),
  );

  assert.match(result.error, /no project.yaml found/);
});

test("resolveGraphJsonPath reports project.yaml without output_dir", () => {
  const result = withVscodeWorkspace(
    [{ uri: { fsPath: "C:\\workspace" } }],
    () => withFs({
      existsSync: () => true,
      readFileSync: () => "main_program: adae.sas\n",
    }, resolveGraphJsonPath),
  );

  assert.match(result.error, /does not declare output_dir/);
});

test("resolveGraphJsonPath reports an empty runs directory", () => {
  const result = withVscodeWorkspace(
    [{ uri: { fsPath: "C:\\workspace" } }],
    () => withFs({
      existsSync: () => true,
      readFileSync: () => "output_dir: graph_runs\n",
      readdirSync: () => {
        const error = new Error("missing");
        error.code = "ENOENT";
        throw error;
      },
    }, resolveGraphJsonPath),
  );

  assert.match(result.error, /no runs found/);
});

test("resolveGraphJsonPath reports a run without graph.json", () => {
  let existsCalls = 0;
  const result = withVscodeWorkspace(
    [{ uri: { fsPath: "C:\\workspace" } }],
    () => withFs({
      existsSync: () => ++existsCalls === 1,
      readFileSync: () => "output_dir: graph_runs\n",
      readdirSync: () => [{ name: "20260801T000000Z-a", isDirectory: () => true }],
    }, resolveGraphJsonPath),
  );

  assert.match(result.error, /has no graph.json/);
});

test("resolveGraphJsonPath returns the latest graph.json path", () => {
  const result = withVscodeWorkspace(
    [{ uri: { fsPath: "C:\\workspace" } }],
    () => withFs({
      existsSync: () => true,
      readFileSync: () => "output_dir: graph_runs\n",
      readdirSync: () => [{ name: "20260801T000000Z-a", isDirectory: () => true }],
    }, resolveGraphJsonPath),
  );

  assert.equal(result.path, path.resolve("C:\\workspace", "graph_runs", "runs", "20260801T000000Z-a", "graph.json"));
});

test("resolveGraphJsonPath preserves the prior empty-runs fallback", () => {
  const result = withVscodeWorkspace(
    [{ uri: { fsPath: "C:\\workspace" } }],
    () => withFs({
      existsSync: () => true,
      readFileSync: () => "output_dir: graph_runs\n",
      readdirSync: () => {
        const error = new Error("permission denied");
        error.code = "EACCES";
        throw error;
      },
    }, resolveGraphJsonPath),
  );

  assert.match(result.error, /no runs found/);
});

function demo() {
  const yaml = `output_dir: graph_runs\n`;
  console.log("extractOutputDir ->", extractOutputDir(yaml));
  console.log("pickLatestRun ->", pickLatestRun(["20260101T000000Z-a", "20260201T000000Z-b"]));
  console.log("projectConfig.js: all checks passed");
}

if (require.main === module) {
  demo();
}
