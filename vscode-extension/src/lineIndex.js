// @ts-check
"use strict";

// Pure line-index construction and innermost-covering-node lookup (ticket
// 06). No vscode import, no DOM. Built by the extension at load time from
// each node's source.file/line_start/line_end — graph.json itself carries
// no reverse index (line-index-built-on-load).

// Cursor-resolution eligibility allowlist, per cursor-highlights-innermost-node
// (amended): Program/SetupFile/Library/MacroVariable/MacroDefinition/
// MacroSourceFile/MacroContract/CommentBlock are never eligible regardless
// of range — this excludes Program (spans the whole file) from candidacy.
// MacroParameter dropped (unify-cursor-select-into-hit-set): convert.js
// excludes MacroParameter from the render model's canvas entirely (dead-end
// leaves, ticket 01), so a cursor line resolving to one would post a nodeId
// nodeById can never find — same node id space as the render model, not the
// full graph.json.
const ELIGIBLE_TYPES = new Set([
  "Step", "SqlBlock", "SqlStatement", "MacroCall",
  "ConditionalBranch", "UnknownMacro", "UnknownDataset",
]);

/**
 * @typedef {{ id: string, type: string, file: string|null, line_start: number|null, line_end: number|null }} SourceRecord
 */

/**
 * Build an interval index from a flat list of source records. Records with
 * no file or no line range are skipped (nothing to index).
 * @param {SourceRecord[]} records
 * @returns {Map<string, SourceRecord[]>} file -> array of eligible-or-not records (allowlist is applied at lookup time, not build time, so the index stays a faithful reflection of the input)
 */
function buildLineIndex(records) {
  /** @type {Map<string, SourceRecord[]>} */
  const byFile = new Map();
  for (const rec of records) {
    if (!rec.file || rec.line_start == null || rec.line_end == null) continue;
    if (!byFile.has(rec.file)) byFile.set(rec.file, []);
    byFile.get(rec.file).push(rec);
  }
  return byFile;
}

/**
 * Return the innermost allowlisted node covering `line` in `file`, or null.
 * "Innermost" = narrowest (line_end - line_start) among covering,
 * allowlisted candidates.
 *
 * Tie-break for identical ranges: NOT A DECIDED RULE. The spec's
 * cursor-highlights-innermost-node amendment fixed the allowlist but
 * explicitly left the tie-break open (see wayfinder spec Further Notes #6,
 * ticket 06). This resolves ties to the first allowlisted match in the
 * input array's document order, purely so the function is deterministic
 * and testable — this is a placeholder, not a decision. Real fixture case:
 * examples/hand_written_graph.json line 11 has macrocall:003 tied exactly
 * with macroparam:003:inds and macroparam:003:outds (all lines 11-11).
 *
 * @param {Map<string, SourceRecord[]>} index
 * @param {string} file
 * @param {number} line
 * @returns {SourceRecord | null}
 */
function lookupInnermostNode(index, file, line) {
  const records = index.get(file);
  if (!records) return null;

  let best = null;
  let bestWidth = Infinity;
  for (const rec of records) {
    if (!ELIGIBLE_TYPES.has(rec.type)) continue;
    if (line < rec.line_start || line > rec.line_end) continue;
    const width = rec.line_end - rec.line_start;
    if (width < bestWidth) {
      best = rec;
      bestWidth = width;
    }
    // width === bestWidth: keep `best` (first match in document order) —
    // this is the placeholder tie-break described above, not a decision.
  }
  return best;
}

module.exports = { buildLineIndex, lookupInnermostNode, ELIGIBLE_TYPES };
