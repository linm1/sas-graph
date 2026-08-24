"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, "..", "package.json"), "utf-8"));
const lockfile = JSON.parse(fs.readFileSync(path.resolve(__dirname, "..", "package-lock.json"), "utf-8"));
const extensionSource = fs.readFileSync(path.resolve(__dirname, "..", "src", "extension.js"), "utf-8");

test("contributes the three explicitly activated language model tools", () => {
  const names = manifest.contributes.languageModelTools.map((tool) => tool.name);
  assert.deepEqual(names, [
    "sas-graph_search",
    "sas-graph_traceLineage",
    "sas-graph_analyzeImpact",
  ]);
  for (const name of names) {
    assert.ok(manifest.activationEvents.includes(`onLanguageModelTool:${name}`));
  }
});

test("tool aliases are prompt-referenceable and schemas use the CLI vocabulary", () => {
  const tools = manifest.contributes.languageModelTools;
  assert.deepEqual(tools.map((tool) => tool.toolReferenceName), [
    "sasGraphSearch",
    "sasGraphTraceLineage",
    "sasGraphAnalyzeImpact",
  ]);
  assert.ok(tools.every((tool) => tool.canBeReferencedInPrompt));
  assert.deepEqual(Object.keys(tools[0].inputSchema.properties), ["query"]);
  assert.deepEqual(Object.keys(tools[1].inputSchema.properties), ["node", "direction"]);
  assert.deepEqual(Object.keys(tools[2].inputSchema.properties), ["variable"]);
});

test("extension registers every contributed tool by its package-qualified name", () => {
  for (const tool of manifest.contributes.languageModelTools) {
    assert.match(extensionSource, new RegExp(`vscode\\.lm\\.registerTool\\(\\"${tool.name}\\"`));
  }
});

test("pythonPath is configurable and defaults to python3", () => {
  assert.equal(manifest.contributes.configuration.properties["sasGraph.pythonPath"].default, "python3");
});

test("engine and lockfile require the finalized Language Model Tools API", () => {
  assert.equal(manifest.engines.vscode, "^1.95.0");
  assert.equal(lockfile.packages[""].engines.vscode, "^1.95.0");
  assert.equal(manifest.devDependencies && manifest.devDependencies["@types/vscode"], undefined);
  assert.equal(lockfile.packages["node_modules/@types/vscode"], undefined);
});

test("native coverage is reproducible through the extension package script", () => {
  assert.match(manifest.scripts["test:coverage"], /--experimental-test-coverage/);
});
