"use strict";

const { spawn } = require("node:child_process");

const MODEL_SAFE_ERRORS = new WeakSet();

/** @param {string} message */
function createSafeModelError(message) {
  const error = new Error(message);
  MODEL_SAFE_ERRORS.add(error);
  return error;
}

/** @param {unknown} error */
function isSafeModelError(error) {
  return Boolean(error && typeof error === "object" && MODEL_SAFE_ERRORS.has(error));
}

function safeFailureReason(stderr) {
  const message = String(stderr || "").replace(/\s+/g, " ").trim();
  if (!message || /\btraceback\b/i.test(message)) return null;

  if (
    /(?:no such file or directory|not found|does not exist|missing)[^\r\n]*graph\.json/i.test(message) ||
    /graph\.json[^\r\n]*(?:not found|does not exist|missing)/i.test(message)
  ) {
    return "no graph.json found for this workspace — run 'sas-graph run' first";
  }
  if (/graph\s+run_status\s+is\s+FAILED;\s*query a completed graph/i.test(message)) {
    return "graph run_status is FAILED; query a completed graph";
  }
  if (/unsupported schema_version/i.test(message)) {
    return "graph.json uses an unsupported schema; run sas-graph again";
  }
  if (
    /invalid json|jsondecodeerror|malformed graph/i.test(message) ||
    /\b(?:expecting|extra data|unterminated string).*line\s+\d+\s+column\s+\d+/i.test(message)
  ) {
    return "graph.json is malformed; run 'sas-graph run' again";
  }
  if (/lineage start must be Dataset, Step, or SqlStatement/i.test(message)) {
    return "lineage start must be Dataset, Step, or SqlStatement";
  }
  if (/impact start must be a Variable or UnknownVariable/i.test(message)) {
    return "impact start must be a Variable or UnknownVariable";
  }
  if (/direction must be upstream, downstream, or both/i.test(message)) {
    return "direction must be upstream, downstream, or both";
  }
  if (/query must be a string/i.test(message)) return "query must be a string";
  if (/node must be a string/i.test(message)) return "node must be a string";
  if (/variable must be a string/i.test(message)) return "variable must be a string";
  if (/\bnode not found\s*:/i.test(message)) {
    return "node not found; use a fully typed graph node id";
  }
  if (/\bvariable not found\s*:/i.test(message)) {
    return "variable not found; use a fully typed graph variable id";
  }
  if (/lineage edge .* points to missing node/i.test(message)) {
    return "lineage graph contains a missing referenced node";
  }
  return null;
}

/**
 * Format a model-safe CLI failure while preserving only known actionable
 * reasons. Unknown stderr is deliberately discarded because it may contain
 * paths, tracebacks, credentials, or other process-local details.
 * @param {string} subcommand
 * @param {number | undefined} code
 * @param {unknown} stderr
 * @returns {string}
 */
function safeCliFailureMessage(subcommand, code, stderr = "") {
  const status = Number.isInteger(code) ? ` (exit code ${code})` : "";
  const reason = safeFailureReason(stderr);
  return reason
    ? `sas-graph ${subcommand} failed${status}: ${reason}`
    : `sas-graph ${subcommand} failed${status}; check the graph and workspace configuration`;
}

/**
 * Run one read-only sas-graph query and parse its JSON stdout.
 * @param {string} subcommand
 * @param {string} graphPath
 * @param {string[]} extraArgs
 * @returns {Promise<object>}
 */
function runCliQuery(subcommand, graphPath, extraArgs) {
  const vscode = require("vscode");
  const configuredPath = vscode.workspace.getConfiguration("sasGraph").get("pythonPath", "python3");
  const pythonPath = typeof configuredPath === "string" && configuredPath ? configuredPath : "python3";
  const args = ["-m", "sas_graph", subcommand, "--graph", graphPath, ...extraArgs];

  return new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    const fail = (message) => {
      if (settled) return;
      settled = true;
      reject(createSafeModelError(message));
    };

    let proc;
    try {
      proc = spawn(pythonPath, args);
    } catch {
      fail(`sas-graph ${subcommand} could not start the configured Python interpreter`);
      return;
    }

    proc.stdout.on("data", (chunk) => { stdout += chunk; });
    proc.stderr.on("data", (chunk) => { stderr += chunk; });
    proc.on("error", () => fail(
      `sas-graph ${subcommand} could not start the configured Python interpreter`,
    ));
    proc.on("close", (code) => {
      if (settled) return;
      if (code !== 0) {
        fail(safeCliFailureMessage(subcommand, code, stderr));
        return;
      }
      try {
        const parsed = JSON.parse(stdout);
        settled = true;
        resolve(parsed);
      } catch {
        fail(`sas-graph ${subcommand} returned invalid JSON`);
      }
    });
  });
}

module.exports = {
  runCliQuery,
  safeCliFailureMessage,
  createSafeModelError,
  isSafeModelError,
};
