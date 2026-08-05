// @ts-check
"use strict";

// Pure library/step-type filter predicate (ticket 08). No vscode import, no
// DOM. Two filter axes combine with AND when both are set.
//
// Library derivation gap (Further Notes #7, still open, not decided here):
// the frozen contract has no `library` field on non-Library nodes, and
// ticket 02's render model doesn't carry one either. This ticket picks the
// placeholder id-prefix heuristic scoped to Dataset/UnknownDataset node ids
// shaped like `<type>:<libref>.<member>` (e.g. `dataset:sdtm.ae` -> `sdtm`).
// It does NOT walk graph edges to a Library node — no committed/reproducible
// fixture demonstrates such an edge. Nodes with no derivable library (Step,
// MacroCall, Program, etc. — anything not matching the shape, INCLUDING
// unknowndataset:&unknown_out. which has no libref.member shape) are never
// hidden by a library filter; they stay visible regardless, since a step
// has no library to match against and hiding it would destroy structural
// context rather than narrow it. Real data path (id-prefix vs a real
// Library-to-Dataset edge) is still open per Further Notes #7.

const LIBRARY_ELIGIBLE_TYPES = new Set(["Dataset", "UnknownDataset"]);

// libref: SAS-shaped identifier, up to 8 chars, starting with a letter or
// underscore. member: anything non-empty after the dot. This is what keeps
// `unknowndataset:&unknown_out.` from being mismatched into library
// "&unknown_out" or similar — it has no `.` splitting a valid libref from a
// non-empty member, so it correctly derives no library at all.
const ID_PREFIX_PATTERN = /^[a-z]+:([A-Za-z_]\w{0,7})\.(.+)$/;

/**
 * @param {{ id: string, group: string }} node - render-model node
 * @returns {string | null} derived library, or null if none derivable
 */
function deriveLibrary(node) {
  if (!LIBRARY_ELIGIBLE_TYPES.has(node.group)) return null;
  const match = ID_PREFIX_PATTERN.exec(node.id);
  return match ? match[1] : null;
}

/**
 * @param {{ id: string, group: string }} node
 * @param {{ library: string|null, type: string|null }} filters
 * @returns {boolean} whether the node passes the combined (AND) filter
 */
function filtersAllow(node, filters) {
  if (filters.type && node.group !== filters.type) return false;
  if (filters.library) {
    const lib = deriveLibrary(node);
    // no derivable library -> node stays visible regardless of the active
    // library filter (placeholder rule, see module doc comment above)
    if (lib !== null && lib !== filters.library) return false;
  }
  return true;
}

// Dual environment, same pattern as searchPredicate.js: node --test / host
// via require, webview via plain <script> global.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { deriveLibrary, filtersAllow, LIBRARY_ELIGIBLE_TYPES };
}
if (typeof window !== "undefined") {
  window.filtersAllow = filtersAllow;
  window.deriveLibrary = deriveLibrary;
}
