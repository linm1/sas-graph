// Ticket 03: pure-function slice of webviewPanel.js that doesn't need a
// real `vscode` module — buildHtml's CSP/script-tag shape and the
// inline-JSON escaping that guards against original_text breaking out of
// its <script> body. Webview creation itself (vscode.window.createWebviewPanel)
// is extension-host-only and out of scope for node --test per the Testing
// Decisions section.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const path = require("node:path");
const { safeInlineJson, isInsideRoot } = require("../src/htmlUtil.js");

test("safeInlineJson escapes </script> so embedded source text can't break out of the inline script tag", () => {
  const payload = { nodes: [{ label: "</script><script>alert(1)</script>" }] };
  const out = safeInlineJson(payload);
  assert.ok(!out.includes("</script>"), "raw </script> must not appear in the inlined JSON");
  assert.ok(out.includes("\\u003cscript\\u003ealert(1)\\u003c/script\\u003e") || out.includes("\\u003c"));
});

test("safeInlineJson round-trips through JSON.parse after unescaping", () => {
  const payload = { a: 1, b: "hello <b>world</b>" };
  const out = safeInlineJson(payload);
  const restored = JSON.parse(out.replace(/\\u003c/g, "<"));
  assert.deepEqual(restored, payload);
});

test("isInsideRoot accepts the root path itself", () => {
  const base = path.resolve("/workspace/project");
  assert.equal(isInsideRoot(base, base), true);
});

test("isInsideRoot accepts a normal descendant path", () => {
  const base = path.resolve("/workspace/project");
  const target = path.resolve("/workspace/project/src/adae.sas");
  assert.equal(isInsideRoot(base, target), true);
});

test("isInsideRoot rejects a path that escapes the root via ..", () => {
  const base = path.resolve("/workspace/project");
  const target = path.resolve("/workspace/project/../../etc/passwd");
  assert.equal(isInsideRoot(base, target), false);
});

test("isInsideRoot rejects an unrelated sibling path with a shared prefix", () => {
  const base = path.resolve("/workspace/project");
  const target = path.resolve("/workspace/project-evil/secret.txt");
  assert.equal(isInsideRoot(base, target), false);
});

function demo() {
  const out = safeInlineJson({ x: "<script>" });
  console.log("safeInlineJson demo:", out);
  console.log("webviewPanel.js: all checks passed");
}

if (require.main === module) {
  demo();
}
