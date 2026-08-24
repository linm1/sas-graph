// @ts-check
"use strict";

const {
  runCliQuery,
  createSafeModelError,
  isSafeModelError,
} = require("./cliRunner.js");
const { resolveGraphJsonPath } = require("./projectConfig.js");

const DIRECTIONS = new Set(["upstream", "downstream", "both"]);
const SAFE_QUERY_ERROR = "SAS Graph query failed.";

function requireInputObject(input) {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    throw createSafeModelError("input must be an object");
  }
}

function requireString(input, name) {
  if (typeof input[name] !== "string") {
    throw createSafeModelError(`${name} must be a string`);
  }
}

/** @param {{query: string}} input */
function searchCliArgs(input) {
  requireInputObject(input);
  requireString(input, "query");
  return ["--query", input.query];
}

/** @param {{node: string, direction?: string}} input */
function lineageCliArgs(input) {
  requireInputObject(input);
  requireString(input, "node");
  if (input.direction !== undefined && !DIRECTIONS.has(input.direction)) {
    throw createSafeModelError("direction must be upstream, downstream, or both");
  }
  const args = ["--node", input.node];
  if (input.direction) args.push("--direction", input.direction);
  return args;
}

/** @param {{variable: string}} input */
function impactCliArgs(input) {
  requireInputObject(input);
  requireString(input, "variable");
  return ["--variable", input.variable];
}

/** @param {object} value */
function compactJson(value) {
  return JSON.stringify(value);
}

function resolveGraphPathOrThrow() {
  const result = resolveGraphJsonPath();
  if (result.error) {
    const message = typeof result.error === "string" ? knownResolverError(result.error) : null;
    throw createSafeModelError(message || SAFE_QUERY_ERROR);
  }
  if (!result.path) throw createSafeModelError(SAFE_QUERY_ERROR);
  return result.path;
}

/** @param {string} message */
function knownResolverError(message) {
  if (message === "SAS Graph: no workspace folder is open. Open the folder containing project.yaml first.") {
    return "SAS Graph: no workspace folder is open. Open the folder containing project.yaml first.";
  }
  if (
    message.startsWith("SAS Graph: no project.yaml found at the workspace root (") &&
    message.endsWith("). Run sas-graph in a workspace with a project.yaml, or choose one manually.")
  ) {
    return "SAS Graph: no project.yaml found in the workspace. Run sas-graph in a workspace with a project.yaml, or choose one manually.";
  }
  if (
    message.startsWith("SAS Graph: project.yaml at ") &&
    message.endsWith(" does not declare output_dir.")
  ) {
    return "SAS Graph: project.yaml does not declare output_dir.";
  }
  if (
    message.startsWith("SAS Graph: no runs found under ") &&
    message.endsWith('. Run "sas-graph run" first, then try again.')
  ) {
    return "SAS Graph: no runs found. Run \"sas-graph run\" first, then try again.";
  }
  if (
    message.startsWith("SAS Graph: run ") &&
    message.includes(" has no graph.json at ") &&
    message.endsWith(".")
  ) {
    return "SAS Graph: latest run has no graph.json. Run \"sas-graph run\" again.";
  }
  return null;
}

/** @param {unknown} error */
function safeErrorText(error) {
  if (!isSafeModelError(error)) return `error: ${SAFE_QUERY_ERROR}`;
  const message = error instanceof Error ? error.message : "";
  const safeMessage = message.replace(/\s+/g, " ").trim() || SAFE_QUERY_ERROR;
  return `error: ${safeMessage || SAFE_QUERY_ERROR}`;
}

/** @param {string} text */
function textResult(text) {
  const vscode = require("vscode");
  return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(text)]);
}

/** @param {object} data */
function jsonResult(data) {
  return textResult(compactJson(data));
}

async function invokeQuery(options, subcommand, argsForInput) {
  const args = argsForInput(options?.input);
  const result = await runCliQuery(subcommand, resolveGraphPathOrThrow(), args);
  return jsonResult(result);
}

class SasGraphSearchTool {
  /** @param {{input: {query: string}}} options */
  async invoke(options) {
    try {
      return await invokeQuery(options, "query-search", searchCliArgs);
    } catch (error) {
      return textResult(safeErrorText(error));
    }
  }
}

class SasGraphTraceLineageTool {
  /** @param {{input: {node: string, direction?: string}}} options */
  async invoke(options) {
    try {
      return await invokeQuery(options, "query-lineage", lineageCliArgs);
    } catch (error) {
      return textResult(safeErrorText(error));
    }
  }
}

class SasGraphAnalyzeImpactTool {
  /** @param {{input: {variable: string}}} options */
  async invoke(options) {
    try {
      return await invokeQuery(options, "query-impact", impactCliArgs);
    } catch (error) {
      return textResult(safeErrorText(error));
    }
  }
}

module.exports = {
  SasGraphSearchTool,
  SasGraphTraceLineageTool,
  SasGraphAnalyzeImpactTool,
  searchCliArgs,
  lineageCliArgs,
  impactCliArgs,
  compactJson,
  safeErrorText,
};
