// Placeholder test proving `node --test` is wired before any real pure-function
// module exists. Mirrors tests/test_renderer_mermaid.py's shape: plain
// assertions, a demo() entry point, no framework.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

test("scaffold: test runner is wired", () => {
  assert.equal(1 + 1, 2);
});

function demo() {
  assert.equal(1 + 1, 2);
  console.log("scaffold: test runner wired, all checks passed");
}

if (require.main === module) {
  demo();
}
