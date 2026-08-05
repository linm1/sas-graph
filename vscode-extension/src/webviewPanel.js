// @ts-check
"use strict";

const vscode = require("vscode");
const crypto = require("node:crypto");
const { convertGraph } = require("./convert.js");
const { safeInlineJson, isInsideRoot } = require("./htmlUtil.js");

function nonce() {
  return crypto.randomBytes(16).toString("hex");
}

/**
 * Open (or reveal) the graph webview panel, rendering the given graph.json
 * object. Render-model payload is inlined into the HTML at creation time
 * (chosen over postMessage-after-load for simplicity at this file size —
 * see ticket 03 note; the message channel still exists for click/cursor
 * wiring added in later tickets).
 * @param {vscode.ExtensionContext} context
 * @param {any} graphJson - parsed graph.json
 */
class GraphSidebarProvider {
  /** @param {vscode.ExtensionContext} context */
  constructor(context) {
    this.context = context;
    this.view = undefined;
    this.graphJson = undefined;
    this.errorMessage = undefined;
  }

  /** @param {vscode.WebviewView} view */
  resolveWebviewView(view) {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this.context.extensionUri, "media"),
        vscode.Uri.joinPath(this.context.extensionUri, "src"),
      ],
    };
    view.webview.onDidReceiveMessage((message) => handleWebviewMessage(message));
    view.onDidDispose(() => {
      if (this.view === view) this.view = undefined;
    });
    this.render();
  }

  /** @param {any} graphJson */
  setGraph(graphJson) {
    this.graphJson = graphJson;
    this.errorMessage = undefined;
    this.render();
  }

  /** @param {string} message */
  setError(message) {
    this.graphJson = undefined;
    this.errorMessage = message;
    this.render();
  }

  /** @param {any} message */
  postMessage(message) {
    return this.view ? this.view.webview.postMessage(message) : false;
  }

  render() {
    if (!this.view) return;
    if (this.graphJson) {
      const renderModel = convertGraph(this.graphJson);
      this.view.webview.html = buildHtml(this.view.webview, this.context, renderModel, this.graphJson.run_status);
      return;
    }
    this.view.webview.html = buildErrorHtml(this.errorMessage || "SAS Graph: no graph is loaded.");
  }
}

/**
 * @param {{ type: string, file?: string, line?: number }} message
 */
async function handleWebviewMessage(message) {
  if (message && message.type === "jumpToLine") {
    await jumpToSourceLine(message.file, message.line);
  }
}

/**
 * Reveal `file` (opening it if not already open) and move the editor
 * selection/cursor to `line` (1-based, as graph.json's line_start is).
 *
 * `file`/`line` originate from the loaded graph.json's node `source` field,
 * not direct user input — but graph.json is a file on disk and could be
 * malformed or adversarial, so this still validates: `line` must be a
 * finite positive integer, and the resolved path must stay inside the
 * workspace root (rejects `../`-style traversal) when a workspace is open.
 * @param {string} file
 * @param {number} line
 */
async function jumpToSourceLine(file, line) {
  if (typeof file !== "string" || !file) return;
  if (typeof line !== "number" || !Number.isFinite(line) || line < 1) return;

  const workspaceFolders = vscode.workspace.workspaceFolders;
  const baseUri = workspaceFolders && workspaceFolders.length ? workspaceFolders[0].uri : undefined;
  const targetUri = baseUri ? vscode.Uri.joinPath(baseUri, file) : vscode.Uri.file(file);

  if (baseUri && !isInsideRoot(baseUri.fsPath, targetUri.fsPath)) {
    return;
  }

  const doc = await vscode.workspace.openTextDocument(targetUri);
  const editor = await vscode.window.showTextDocument(doc, { preview: false });
  const zeroBasedLine = Math.max(0, line - 1);
  const range = editor.document.lineAt(Math.min(zeroBasedLine, editor.document.lineCount - 1)).range;
  editor.selection = new vscode.Selection(range.start, range.start);
  editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
}

/**
 * @param {vscode.Webview} webview
 * @param {vscode.ExtensionContext} context
 * @param {{ nodes: any[], edges: any[] }} renderModel
 * @param {string} [runStatus]
 */
function buildHtml(webview, context, renderModel, runStatus) {
  const scriptNonce = nonce();
  const visUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "media", "vis-network.min.js"));
  const cssUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "media", "webview.css"));
  const searchPredicateUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "src", "searchPredicate.js"));
  const libraryFilterUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "src", "libraryFilter.js"));
  const hopDistanceUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "src", "hopDistance.js"));
  const visualStateUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "src", "visualState.js"));
  const webviewScriptUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "media", "webview.js"));

  const csp = [
    `default-src 'none'`,
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${scriptNonce}' ${webview.cspSource}`,
  ].join("; ");

  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<title>SAS Graph</title>
<link rel="stylesheet" href="${cssUri}">
</head>
<body>
<div id="network"></div>
<div id="toolbar">
  <input id="search" placeholder="Search nodes…" />
  <button id="filterToggle" type="button" aria-label="toggle filters">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="6" x2="20" y2="6"/><circle cx="9" cy="6" r="2" fill="currentColor" stroke="none"/><line x1="4" y1="12" x2="20" y2="12"/><circle cx="15" cy="12" r="2" fill="currentColor" stroke="none"/><line x1="4" y1="18" x2="20" y2="18"/><circle cx="11" cy="18" r="2" fill="currentColor" stroke="none"/></svg>
    <span id="filterBadge"></span>
  </button>
</div>
<div id="filterPopover">
  <div class="filter-row">
    <select id="typeFilter" class="filter-select"><option value="">All types</option></select>
    <select id="libraryFilter" class="filter-select"><option value="">All libraries</option></select>
  </div>
  <div id="hopGroup">
    <div id="hopHeader"><label for="hopCeiling">Highlight depth</label><span id="hopValue">5 hops</span></div>
    <input id="hopCeiling" type="range" min="1" max="10" value="3" step="1" aria-label="Depth ceiling (hops)" />
  </div>
</div>
<span id="status"></span>
<div id="info"></div>

<script nonce="${scriptNonce}" src="${visUri}"></script>
<script nonce="${scriptNonce}" src="${searchPredicateUri}"></script>
<script nonce="${scriptNonce}" src="${libraryFilterUri}"></script>
<script nonce="${scriptNonce}" src="${hopDistanceUri}"></script>
<script nonce="${scriptNonce}" src="${visualStateUri}"></script>
<script nonce="${scriptNonce}">
  const GRAPH_DATA = ${safeInlineJson(renderModel)};
  const RUN_STATUS = ${safeInlineJson(runStatus || "")};
</script>
<script nonce="${scriptNonce}" src="${webviewScriptUri}"></script>
</body>
</html>`;
}

/** @param {string} message */
function buildErrorHtml(message) {
  const escaped = String(message).replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[char]));
  return `<!doctype html><html><body><p>${escaped}</p></body></html>`;
}

module.exports = { GraphSidebarProvider, buildHtml, buildErrorHtml, jumpToSourceLine };
