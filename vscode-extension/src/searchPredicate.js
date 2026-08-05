// @ts-check
"use strict";

// Pure search match predicate (ticket 05). No vscode import, no DOM.
// Case-insensitive substring match against a render-model node's `label`
// OR `group` (vis-network's field carrying the node's type, from ticket
// 02's conversion) — per specify-search-behavior.

/**
 * @param {string} query - already trimmed; caller decides what "empty" means
 * @param {{ label: string, group: string }} node - render-model node
 * @returns {boolean}
 */
function matchesSearch(query, node) {
  const q = query.toLowerCase();
  const label = String(node.label ?? "").toLowerCase();
  const group = String(node.group ?? "").toLowerCase();
  return label.includes(q) || group.includes(q);
}

// Dual environment: `require`d by node --test and the extension host, and
// loaded as a plain <script> in the webview (no bundler, no module system
// there — see the repo's CommonJS-throughout / no-bundler decision).
if (typeof module !== "undefined" && module.exports) {
  module.exports = { matchesSearch };
}
if (typeof window !== "undefined") {
  window.matchesSearch = matchesSearch;
}

