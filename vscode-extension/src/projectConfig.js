// @ts-check
"use strict";

const path = require("node:path");
const fs = require("node:fs");

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
 * Resolve the run directory from a workspace root and project output_dir.
 * Absolute output_dir values must stay absolute; relative values are rooted
 * at the workspace, matching the Python config loader.
 * @param {string} workspaceRoot
 * @param {string} outputDir
 * @returns {string}
 */
function resolveRunsDir(workspaceRoot, outputDir) {
  return path.resolve(workspaceRoot, outputDir, "runs");
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

/**
 * Locate the latest graph.json from the current workspace's project.yaml.
 * @returns {{ path?: string, error?: string }}
 */
function resolveGraphJsonPath() {
  const vscode = require("vscode");
  const workspaceFolders = vscode.workspace.workspaceFolders;
  if (!workspaceFolders || !workspaceFolders.length) {
    return { error: "SAS Graph: no workspace folder is open. Open the folder containing project.yaml first." };
  }

  const workspaceRoot = workspaceFolders[0].uri.fsPath;
  const projectYamlPath = path.join(workspaceRoot, "project.yaml");
  if (!fs.existsSync(projectYamlPath)) {
    return { error: `SAS Graph: no project.yaml found at the workspace root (${workspaceRoot}). Run sas-graph in a workspace with a project.yaml, or choose one manually.` };
  }

  const yamlText = fs.readFileSync(projectYamlPath, "utf-8");
  const outputDir = extractOutputDir(yamlText);
  if (!outputDir) {
    return { error: `SAS Graph: project.yaml at ${projectYamlPath} does not declare output_dir.` };
  }

  const runsDir = resolveRunsDir(workspaceRoot, outputDir);
  let runDirNames = [];
  try {
    runDirNames = fs.readdirSync(runsDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);
  } catch {
    runDirNames = [];
  }

  const latestRun = pickLatestRun(runDirNames);
  if (!latestRun) {
    return { error: `SAS Graph: no runs found under ${runsDir}. Run "sas-graph run" first, then try again.` };
  }

  const graphJsonPath = path.join(runsDir, latestRun, "graph.json");
  if (!fs.existsSync(graphJsonPath)) {
    return { error: `SAS Graph: run ${latestRun} has no graph.json at ${graphJsonPath}.` };
  }
  return { path: graphJsonPath };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { extractOutputDir, pickLatestRun, resolveRunsDir, resolveGraphJsonPath };
}
