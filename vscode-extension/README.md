# SAS Graph Explorer

**SAS Graph Explorer** is an interactive visualization tool for sas-graph run output. The VS Code extension automatically discovers your project configuration, loads the latest dependency graph, and renders an interactive node-link diagram—wired directly to your SAS source code.

Click any node to jump to its source statement. Search, filter by library, zoom, pan, and hover for details. Built for SAS programmers using sas-graph to analyze macro dependencies, data flow, and program execution paths.

## Installing the Extension

### From VSIX

Obtain the `.vsix` file (e.g., `sas-graph-explorer-0.1.0.vsix`) and install it in VS Code:

1. Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (macOS)
2. Run `Extensions: Install from VSIX...`
3. Select the `.vsix` file
4. VS Code will install and activate the extension

### Building the VSIX

To package the extension for distribution:

```bash
cd vscode-extension
npx vsce package
```

No global install needed — `npx` fetches `@vscode/vsce` on demand. To install it once instead: `npm install -g @vscode/vsce`, then just run `vsce package`.

This generates a file like `sas-graph-explorer-0.1.0.vsix` in the current directory. Share or distribute this file for installation.

**Note:** The first `vsce package` run may prompt you to confirm the publisher; you can skip this with `--no-update-package-json` if desired.

## Opening a Graph

### Auto-Detect Flow (Recommended)

1. Open a folder containing `project.yaml` at its root in VS Code
2. Press `Ctrl+Shift+P` and run `SAS Graph: Open Graph View`
3. The extension scans `project.yaml`, locates `output_dir`, and loads the latest run's `graph.json`

### Manual Picker Flow

If auto-detect doesn't work or you want to load a specific graph:

1. Press `Ctrl+Shift+P` and run `SAS Graph: Open Graph View (choose graph.json manually)`
2. Browse to a `graph.json` file
3. The webview opens with your selected graph

## Using the Graph

Once the graph loads:

- **Click a node** — Jump to its source statement in the editor
- **Search** — Type in the search box (top of the webview) to filter nodes by name or type
- **Filter by library** — Use the library dropdown to show only specific node types (e.g., PROC, DATA, MACRO)
- **Zoom & pan** — Mouse wheel to zoom; drag with mouse to pan
- **Hover** — See node details (ID, type, source file and line number)

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| "no workspace folder is open" | No folder loaded in VS Code | Open the folder containing `project.yaml` |
| "no project.yaml found at workspace root" | Config file is in a subdirectory | Move `project.yaml` to the workspace root or open the parent directory |
| "project.yaml does not declare output_dir" | Malformed YAML or missing field | Check your `project.yaml` syntax; ensure `output_dir:` is defined |
| "runs directory not found" | `output_dir/runs/` doesn't exist | Run sas-graph first to generate outputs |
| "No runs found" | No subdirectories in `output_dir/runs/` | Ensure sas-graph has completed at least one run |
| Webview shows blank | `graph.json` is invalid or malformed | Check the file size and schema; run sas-graph again |

## Development

For detailed developer setup, TDD workflow, architecture documentation, and contributing guidelines, see `.claude/CLAUDE.md` in this directory (not packaged in the VSIX — source checkout only).

Quick start for developers:

```bash
cd vscode-extension
npm install
npm test            # Run all tests
npm run build       # If a build script is defined
```

Press **F5** to launch a debug window and test the extension.

## Related Resources

- **sas-graph (main project)** — See `../README.md` for the Python dependency graph parser
- **VS Code Extension API** — https://code.visualstudio.com/api
- **Webview Guide** — https://code.visualstudio.com/api/extension-guides/webview
- **vis-network** — https://visjs.org/
