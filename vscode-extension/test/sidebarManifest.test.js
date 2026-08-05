"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, "..", "package.json"), "utf-8"));

test("contributes one SAS Graph sidebar view in an Activity Bar container", () => {
  const containers = manifest.contributes.viewsContainers?.activitybar;
  const views = manifest.contributes.views?.sasGraph;

  assert.deepEqual(containers, [{ id: "sasGraph", title: "SAS Graph", icon: "media/graph.svg" }]);
  assert.deepEqual(views, [{ id: "sasGraph.sidebar", name: "Graph", type: "webview" }]);
});

test("activates when the sidebar view is opened", () => {
  assert.ok(manifest.activationEvents.includes("onView:sasGraph.sidebar"));
});
