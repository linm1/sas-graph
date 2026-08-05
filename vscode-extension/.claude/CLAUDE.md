# SAS Graph Explorer — VS Code Extension

**Version:** 0.1.0  
**Display Name:** SAS Graph Explorer  
**Package Name:** `sas-graph-explorer`  
**Requires:** VS Code ^1.85.0

## Overview

SAS Graph Explorer is an interactive visualization tool for sas-graph run output (`graph.json`). The extension automatically discovers project configuration, resolves the latest run's graph data, and renders an interactive node-link diagram wired to the SAS source in your editor.

**For whom:** SAS programmers using sas-graph to analyze macro dependencies, data flow, and program execution paths. Developers of the sas-graph project itself.

**What it does:**
- Auto-detects workspace `project.yaml`, resolves `output_dir/runs/` for the latest run
- Loads and validates `graph.json` against the schema contract
- Renders interactive visualization with search, filtering, and cursor-to-node sync
- Highlights program statements in the editor when you click nodes
- Supports two activation flows: auto-detect (command: `sasGraph.openGraphView`) and manual picker (command: `sasGraph.openGraphViewManual`)

## User Guide

### Installation

**From VSIX (production build):**
- Obtain the `.vsix` file from the project release
- Open VS Code
- Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on macOS)
- Run `Extensions: Install from VSIX...`
- Select the `.vsix` file

**From source (development build):**
```bash
cd vscode-extension
npm install
npm run build  # if a build script is defined
# Then open the vscode-extension folder in VS Code and press F5
```

### Opening a Graph

1. **Auto-detect:** Open a folder containing `project.yaml` at its root
2. Run command `SAS Graph: Open Graph View` (via `Ctrl+Shift+P`)
3. The extension scans `project.yaml`, finds `output_dir`, and loads the most recent run's `graph.json`

**Alternative (manual picker):**
- Run `SAS Graph: Open Graph View (choose graph.json manually)`
- Browse to a `graph.json` file directly

### Navigation & Interaction

- **Click a node** — Jump to its source statement in the editor
- **Search** — Type in the search box (top of webview) to filter nodes by name or type
- **Filter by library** — Dropdown filters (e.g., show only PROC-type nodes)
- **Zoom & pan** — Mouse wheel to zoom, drag to pan
- **Hover** — See node details (id, type, source location)

### Common Errors & Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| "no workspace folder is open" | No folder loaded in VS Code | Open the folder containing `project.yaml` |
| "no project.yaml found at workspace root" | `project.yaml` is in a subdirectory | Move it to the workspace root or open the parent directory |
| "project.yaml does not declare output_dir" | Malformed YAML or missing field | Check `project.yaml` syntax; ensure `output_dir:` is defined |
| "runs directory not found" | `output_dir/runs/` doesn't exist | Run sas-graph first to generate outputs |
| "No runs found" | No subdirectories in `output_dir/runs/` | Ensure sas-graph completed a run |
| Webview shows blank | graph.json is invalid or malformed | Check the file size and schema; run sas-graph again |

## Architecture

### Module Map

| Module | Responsibility |
|--------|-----------------|
| **extension.js** | VS Code activation, command registration, workspace root detection, error messages |
| **webviewPanel.js** | Webview panel creation, lifecycle management, message passing to webview frontend |
| **projectConfig.js** | Parse project.yaml, extract output_dir, scan runs/ for the latest run |
| **lineIndex.js** | Build line-to-node index for cursor-to-node synchronization (and reverse lookup) |
| **convert.js** | Normalize and validate graph.json against schema contract |
| **searchPredicate.js** | Query parsing, node matching (by name, type, source location) |
| **libraryFilter.js** | Filter nodes by library/type (proc, data, etc.) |
| **htmlUtil.js** | Safe HTML injection helpers, path normalization, nonce generation for CSP |
| **webview.js** (media/) | Frontend visualization logic (vis-network library, event handlers) |

### Data Flow

```
Workspace (project.yaml)
  ↓
projectConfig.js (extract output_dir, scan runs/)
  ↓
graph.json (latest run)
  ↓
convert.js (normalize + validate)
  ↓
lineIndex.js (index nodes by line for cursor sync)
  ↓
webviewPanel.js (send to frontend)
  ↓
webview.js + vis-network (render interactive graph)
```

### Graph Schema Contract

The extension expects `graph.json` to conform to this schema:

```json
{
  "schema_version": "0.1.0",
  "run_status": "COMPLETE | PARTIAL | FAILED",
  "nodes": [
    {
      "id": "node_1",
      "type": "proc|data|macro|...",
      "label": "Node Label",
      "source": {
        "file": "path/to/file.sas",
        "line": 42
      }
    }
  ],
  "edges": [
    {
      "id": "edge_1",
      "from": "node_1",
      "to": "node_2",
      "type": "depends|calls|..."
    }
  ],
  "findings": [
    {
      "id": "finding_1",
      "status": "error|warning|info",
      "message": "...",
      "source": { "file": "...", "line": 42 }
    }
  ]
}
```

See `../README.md` for full schema documentation.

## Developer Setup

### Prerequisites
- Node.js ^16
- VS Code ^1.85.0
- Git

### Clone & Install

```bash
git clone <repo>
cd sas-graph/vscode-extension
npm install
```

### Run in Debug Mode

1. Open `vscode-extension/` folder in VS Code
2. Press `F5` (or go to **Run** → **Start Debugging**)
3. A new VS Code window opens with the extension loaded
4. Open a workspace with `project.yaml` and run the extension command

**Debug tips:**
- Set breakpoints in `src/extension.js` or any module
- Use `console.log()` during development (removed before commit; see **Code Style**)
- Reload the debug window (`Ctrl+R`) to pick up code changes

### Test Execution

```bash
npm test
# Runs all test files in test/ via Node's --test framework
# Output includes line coverage (aim for 80%+ on any new code)
```

List all test files:
```bash
ls test/*.test.js
```

Run a single test:
```bash
node --test test/convert.test.js
```

## Development Workflow

### TDD Workflow (Mandatory)

1. **Write test first (RED)**
   ```javascript
   import test from 'node:test';
   import assert from 'node:assert/strict';

   test('should normalize node labels', () => {
     const result = normalizeLabel('MY_PROC');
     assert.equal(result, 'my_proc');
   });
   ```
   Run `npm test` — test fails (RED)

2. **Implement minimum code (GREEN)**
   ```javascript
   function normalizeLabel(label) {
     return label.toLowerCase();
   }
   ```
   Run `npm test` — test passes (GREEN)

3. **Refactor (IMPROVE)**
   - Extract repeated logic
   - Improve naming
   - Run `npm test` to verify still passing

### Code Review

After writing or modifying code:
1. Self-review: Check the code quality checklist (see **Code Style**)
2. Run tests: `npm test` — all must pass
3. Verify coverage: Check console output for line coverage
4. Submit for review: Open a PR with comprehensive summary

### Commit Standards

Follow [Conventional Commits](https://www.conventionalcommits.org/):
```
<type>: <description>

<optional body>
```

**Types:** feat, fix, refactor, docs, test, chore, perf, ci

Example:
```
feat: add search query parser for node filtering

Implements searchPredicate.js to parse user search strings
and match against node names, types, and source locations.
Supports quoted phrases and exclusion operators.

Fixes #42
```

See global `~/.claude/CLAUDE.md` for full Git workflow guidance.

## Code Style

This extension extends the global ECC coding standards. See `~/.claude/CLAUDE.md` for complete rules; highlights below.

### JavaScript (No TypeScript)

Use JSDoc for type hints in `.js` files:

```javascript
/**
 * @param {string} yamlText
 * @returns {string | undefined}
 */
function extractOutputDir(yamlText) {
  const match = yamlText.match(/output_dir:\s*["']([^"']+)["']/);
  return match?.[1];
}
```

### Immutability (CRITICAL)

Always return new objects; never mutate:

```javascript
// WRONG
function setNodeLabel(node, label) {
  node.label = label;  // MUTATION!
  return node;
}

// CORRECT
function setNodeLabel(node, label) {
  return { ...node, label };
}
```

### Error Handling

Handle errors explicitly. Never silently swallow:

```javascript
// WRONG
try {
  const data = JSON.parse(graphJson);
} catch (error) {
  // Silent fail, continues with undefined
}

// CORRECT
try {
  const data = JSON.parse(graphJson);
} catch (error) {
  vscode.window.showErrorMessage(`Invalid graph.json: ${error.message}`);
  return undefined;
}
```

### No console.log in Production

Use only during development. Before committing, remove all `console.log()` statements (or use a logging library for server-side code).

### File Size & Function Length

- **Files:** Max 800 lines; 200–400 typical
- **Functions:** Max 50 lines; prefer early returns to reduce nesting
- **Nesting:** Max 4 levels; break into smaller functions

### Input Validation

Validate at boundaries (user input, file reads, external data):

```javascript
function openFile(filePath) {
  // Validate path exists and is a file
  if (!fs.existsSync(filePath)) {
    throw new Error(`File not found: ${filePath}`);
  }

  const stat = fs.statSync(filePath);
  if (!stat.isFile()) {
    throw new Error(`Not a file: ${filePath}`);
  }

  return fs.readFileSync(filePath, 'utf-8');
}
```

## Security Checklist

**Before any commit:**
- [ ] No hardcoded secrets (API keys, tokens, credentials)
- [ ] All user inputs validated before use
- [ ] Path traversal prevention (use `path.resolve()` and check bounds)
- [ ] Webview scripts use CSP nonce (never `'unsafe-inline'`)
- [ ] HTML injection sanitized (use `htmlUtil.js` helpers)
- [ ] Error messages don't leak sensitive data
- [ ] No credentials in logs or error output

### Webview Sandboxing (Nonce-Based CSP)

The webview uses a Content Security Policy to prevent inline script injection:

```javascript
// webviewPanel.js
const nonce = generateNonce();  // htmlUtil.js
const html = `
  <!DOCTYPE html>
  <html>
    <head>
      <meta charset="UTF-8">
      <meta
        http-equiv="Content-Security-Policy"
        content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline';"
      >
    </head>
    <body>
      <script nonce="${nonce}" src="${webviewScript}"></script>
    </body>
  </html>
`;
```

**Rule:** Never inject user-controlled strings into inline scripts. Always load scripts from `media/` or use message passing.

### Path Traversal Prevention

When constructing file paths:

```javascript
// WRONG: User input directly in path
const filePath = path.join(baseDir, userInput);

// CORRECT: Validate that result stays within bounds
const filePath = path.resolve(baseDir, userInput);
if (!filePath.startsWith(baseDir)) {
  throw new Error('Path traversal detected');
}
```

Use `htmlUtil.isInsideRoot()` for this check:

```javascript
const { isInsideRoot } = require('./htmlUtil.js');

if (!isInsideRoot(fullPath, rootPath)) {
  throw new Error(`${fullPath} is outside ${rootPath}`);
}
```

### Input Validation

```javascript
// Validate graph.json structure before use
function validateGraph(data) {
  if (!data.schema_version) {
    throw new Error('graph.json missing schema_version');
  }

  if (!Array.isArray(data.nodes)) {
    throw new Error('graph.json.nodes must be an array');
  }

  // Validate each node
  data.nodes.forEach((node, idx) => {
    if (!node.id) throw new Error(`Node ${idx} missing id`);
    if (!node.type) throw new Error(`Node ${idx} missing type`);
  });

  return data;
}
```

### Message Validation

When receiving messages from the webview:

```javascript
// webviewPanel.js
webviewPanel.webview.onDidReceiveMessage((message) => {
  if (!message.command) {
    console.error('Invalid message: missing command');
    return;
  }

  if (message.command === 'jumpToSource') {
    if (typeof message.file !== 'string' || typeof message.line !== 'number') {
      console.error('Invalid jumpToSource message');
      return;
    }
    // Safe to use message.file and message.line
  }
});
```

## Test Coverage

**Minimum: 80%** across all modified or new code.

Test types (all required):
- **Unit tests** — Individual functions (convert.js, projectConfig.js, etc.)
- **Integration tests** — Module interactions (lineIndex + convert, etc.)
- **E2E tests** — Critical UI flows (open graph, search, filter)

Run full suite and check coverage:
```bash
npm test
# Output includes: tests passed, coverage summary
```

## Releasing

When ready to release:
1. Update version in `package.json`
2. Build VSIX: `vsce package` (requires `vsce` CLI: `npm install -g @vscode/vsce`)
3. Test the VSIX locally (see **Installation** above)
4. Tag the release in git and push to remote
5. Upload VSIX to the VS Code Marketplace (requires publisher account)

For detailed steps, see [VS Code Extension Publishing Guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension).

## Related Resources

- **Global instructions:** `~/.claude/CLAUDE.md` (ECC principles, agents, testing, git workflow)
- **Project documentation:** `../README.md`
- **VS Code Extension API:** https://code.visualstudio.com/api
- **Webview documentation:** https://code.visualstudio.com/api/extension-guides/webview
- **vis-network library:** https://visjs.org/
