// @ts-check
"use strict";

// Ticket 09: locate graph.json from a CLI run.
//
// Answer to Further Notes #3 (concrete, not left open, per the ticket):
// the extension reads `project.yaml` itself, at the workspace root — no
// YAML library dependency (the repo has zero runtime deps beyond the
// vendored vis-network UMD bundle; PyYAML is a Python-side dependency only,
// not available/desired in the extension). `project.yaml`'s `output_dir`
// is a single scalar line (`output_dir: <path>`), so a line-scan regex is
// sufficient — no general YAML parsing is needed for this one field.
//
// Runs are picked by lexicographic descending sort of the run directory
// name. sas_graph.manifest.new_run_id documents its own format as
// "UTC timestamp, sortable, filesystem-safe" — `YYYYMMDDTHHMMSSZ-<hex>` —
// so string sort is chronological sort, and doesn't depend on filesystem
// mtime (which doesn't survive a fresh checkout/clone).

/**
 * Pure line-scan for `output_dir:` in a project.yaml's raw text. Handles a
 * trailing `# comment` and surrounding quotes. Not a general YAML parser —
 * deliberately scoped to this one scalar field.
 * @param {string} yamlText
 * @returns {string | null}
 */
function extractOutputDir(yamlText) {
  const match = /^\s*output_dir\s*:\s*(.*)$/m.exec(yamlText);
  if (!match) return null;
  let value = match[1].trim();
  // If the value is quoted, the quotes win: a `#` inside a quoted scalar is
  // literal YAML content, not a comment marker (e.g. `"runs/#latest"`).
  // Strip quotes first, then only look for a trailing comment on the
  // remaining unquoted text.
  const isQuoted = (value.startsWith('"') && value.endsWith('"') && value.length >= 2) ||
    (value.startsWith("'") && value.endsWith("'") && value.length >= 2);
  if (isQuoted) {
    value = value.slice(1, -1);
  } else {
    // strip a trailing # comment — good enough for this one scalar field;
    // project.yaml's own fixtures use unquoted plain paths
    const commentIdx = value.indexOf("#");
    if (commentIdx !== -1) value = value.slice(0, commentIdx).trim();
  }
  return value || null;
}

/**
 * Pick the most recent run directory name from a list of run directory
 * names under `output_dir/runs/`. Lexicographic descending sort on the
 * `YYYYMMDDTHHMMSSZ-<hex>` run_id format is chronological — see module doc
 * comment. Returns null if the list is empty.
 * @param {string[]} runDirNames
 * @returns {string | null}
 */
function pickLatestRun(runDirNames) {
  if (!runDirNames.length) return null;
  return [...runDirNames].sort().at(-1);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { extractOutputDir, pickLatestRun };
}
