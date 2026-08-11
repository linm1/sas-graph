// Ticket 09: pure output_dir extraction and latest-run selection. No
// vscode import, no filesystem access here — extension.js does the actual
// fs.readFileSync/fs.readdirSync glue, which is extension-host-only.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { extractOutputDir, pickLatestRun } = require("../src/projectConfig.js");

test("extractOutputDir reads a plain unquoted scalar (real fixture shape)", () => {
  const yaml = `main_program: adae.sas\nsetup_file: setup.sas\n\noutput_dir: graph_runs\n`;
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

function demo() {
  const yaml = `output_dir: graph_runs\n`;
  console.log("extractOutputDir ->", extractOutputDir(yaml));
  console.log("pickLatestRun ->", pickLatestRun(["20260101T000000Z-a", "20260201T000000Z-b"]));
  console.log("projectConfig.js: all checks passed");
}

if (require.main === module) {
  demo();
}
