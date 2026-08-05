// @ts-check
"use strict";

const vscode = require("vscode");
const fs = require("node:fs");
const path = require("node:path");
const { GraphSidebarProvider } = require("./webviewPanel.js");
const { buildLineIndex, lookupInnermostNode } = require("./lineIndex.js");
const { extractOutputDir, pickLatestRun } = require("./projectConfig.js");

/**
 * Ticket 09: locate the current workspace's project.yaml, resolve its
 * output_dir, and find the most recent run's graph.json under
 * output_dir/runs/. Answer to Further Notes #3: workspace-root project.yaml
 * scan (not a VS Code setting) — see projectConfig.js's doc comment.
 *
 * Falls back to ticket 03's manual file picker only when a workspace is
 * open but has no project.yaml at its root and the user explicitly wants
 * to point at an arbitrary graph.json anyway; project.yaml-present-but-
 * misconfigured cases surface a specific error instead (never a blank
 * webview, never a silent fallback), per the ticket's acceptance criteria.
 * @returns {{ path?: string, error?: string }}
 */
function resolveGraphJsonPath() {
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

  const runsDir = path.join(workspaceRoot, outputDir, "runs");
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

/**
 * Ticket 03's original manual-selection path, kept as a second command for
 * pointing the webview at an arbitrary graph.json (e.g. outside the
 * project.yaml/output_dir convention). Not the primary day-to-day command
 * — see resolveGraphJsonPath for that (ticket 09).
 * @returns {Promise<string | undefined>}
 */
async function pickGraphJsonManually() {
  const active = vscode.window.activeTextEditor;
  if (active && active.document.fileName.toLowerCase().endsWith("graph.json")) {
    return active.document.fileName;
  }
  const picked = await vscode.window.showOpenDialog({
    canSelectMany: false,
    filters: { "graph.json": ["json"] },
    title: "Select graph.json",
  });
  return picked && picked.length ? picked[0].fsPath : undefined;
}

/**
 * Build source records (id/type/file/line_start/line_end) from a parsed
 * graph.json's nodes, for the line-index (ticket 06).
 * @param {any} graphJson
 */
function sourceRecordsFromGraph(graphJson) {
  return (graphJson.nodes || [])
    .filter((n) => n.source)
    .map((n) => ({
      id: n.id,
      type: n.type,
      file: n.source.file,
      line_start: n.source.line_start,
      line_end: n.source.line_end,
    }));
}

/** @param {vscode.ExtensionContext} context */
function activate(context) {
  /** @type {Map<string, any[]> | undefined} */
  let lineIndex;
  const sidebar = new GraphSidebarProvider(context);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("sasGraph.sidebar", sidebar)
  );

  /** @param {string} graphPath */
  function loadGraphFromPath(graphPath) {
    let graphJson;
    try {
      graphJson = JSON.parse(fs.readFileSync(graphPath, "utf-8"));
    } catch (err) {
      lineIndex = undefined;
      sidebar.setError(`SAS Graph: failed to read/parse ${graphPath}: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }

    lineIndex = buildLineIndex(sourceRecordsFromGraph(graphJson));
    sidebar.setGraph(graphJson);
  }

  function loadLatestGraph() {
    const result = resolveGraphJsonPath();
    if (result.path) {
      loadGraphFromPath(result.path);
    } else {
      lineIndex = undefined;
      sidebar.setError(result.error || "SAS Graph: no graph is loaded.");
    }
  }

  async function revealSidebar() {
    await vscode.commands.executeCommand("workbench.view.extension.sasGraph");
  }

  const openDisposable = vscode.commands.registerCommand("sasGraph.openGraphView", async () => {
    await revealSidebar();
    loadLatestGraph();
  });

  const openManualDisposable = vscode.commands.registerCommand("sasGraph.openGraphViewManual", async () => {
    await revealSidebar();
    const graphPath = await pickGraphJsonManually();
    if (graphPath) loadGraphFromPath(graphPath);
  });

  // Cursor-to-node sync (ticket 06): on every selection change in the
  // active editor, look up the innermost allowlisted covering node and
  // post it to the webview. Extension-host cursor-listener wiring is
  // manual-smoke-only per Testing Decisions — the lookup itself
  // (buildLineIndex/lookupInnermostNode) is the tested pure-function seam.
  const cursorListener = vscode.window.onDidChangeTextEditorSelection((event) => {
    if (!lineIndex) return;
    const document = event.textEditor.document;
    const line = event.selections[0].active.line + 1; // 1-based, matches graph.json's line_start
    const workspaceFolders = vscode.workspace.workspaceFolders;
    const rawRelativeFile = workspaceFolders && workspaceFolders.length
      ? vscode.workspace.asRelativePath(document.uri, false)
      : document.fileName;
    // graph.json's source.file values always use forward slashes (they come
    // from the CLI's own path handling); asRelativePath returns backslashes
    // on Windows for nested paths (e.g. contracts\gm_derive.yaml). Without
    // this normalization the line index — keyed by graph.json's forward-
    // slash strings — silently never matches for any file below the
    // workspace root, and the cursor highlight would just never fire for
    // it (the flat adae.sas fixture hides this because it has no
    // subdirectory to expose the mismatch).
    const relativeFile = rawRelativeFile.replace(/\\/g, "/");

    const covering = lookupInnermostNode(lineIndex, relativeFile, line);
    sidebar.postMessage({ type: "cursorNode", nodeId: covering ? covering.id : null });
  });

  context.subscriptions.push(openDisposable, openManualDisposable, cursorListener);
  loadLatestGraph();
}

function deactivate() {}

module.exports = { activate, deactivate };
