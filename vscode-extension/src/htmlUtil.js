// @ts-check
"use strict";

const path = require("node:path");

// Pure helpers used when building the webview HTML and validating
// webview->host messages. Split out from webviewPanel.js so they're
// testable with `node --test` without a `vscode` module on the require
// path (webviewPanel.js itself requires "vscode" at load time, which only
// resolves inside the extension host).

// Escape `</script>` and friends so arbitrary original_text/label content in
// the render model can't break out of the inline <script> body it's
// embedded in.
/**
 * @param {any} value
 * @returns {string}
 */
function safeInlineJson(value) {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

/**
 * True if `targetPath` is `basePath` itself or a descendant of it. Used to
 * reject `../`-style traversal in the webview's jumpToLine message before
 * the extension host opens the resolved file (ticket 04's postMessage
 * carries a `file` string from graph.json, which is trusted CLI output but
 * still a file on disk that could be malformed or adversarial).
 * @param {string} basePath - absolute path, no trailing separator required
 * @param {string} targetPath - absolute path to check
 * @returns {boolean}
 */
function isInsideRoot(basePath, targetPath) {
  const normalizedBase = basePath.replace(/[\\/]+$/, "");
  return targetPath === normalizedBase || targetPath.startsWith(normalizedBase + path.sep);
}

module.exports = { safeInlineJson, isInsideRoot };
