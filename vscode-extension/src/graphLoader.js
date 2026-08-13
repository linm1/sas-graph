// @ts-check
"use strict";

const fs = require("node:fs");
const { buildLineIndex } = require("./lineIndex.js");

const SUPPORTED_SCHEMA_VERSION = "0.2.0";

/** @param {any} graphJson */
function sourceRecordsFromGraph(graphJson) {
  return (graphJson.nodes || [])
    .filter((node) => node.source)
    .map((node) => ({
      id: node.id,
      type: node.type,
      file: node.source.file,
      line_start: node.source.line_start,
      line_end: node.source.line_end,
    }));
}

/** @param {string} graphPath */
function loadGraphFromPath(graphPath) {
  let graphJson;
  try {
    graphJson = JSON.parse(fs.readFileSync(graphPath, "utf-8"));
  } catch (err) {
    return {
      error: `SAS Graph: failed to read/parse ${graphPath}: ${err instanceof Error ? err.message : String(err)}`,
    };
  }

  if (graphJson.schema_version !== SUPPORTED_SCHEMA_VERSION) {
    return {
      error: `SAS Graph: unsupported graph.json schema_version ${JSON.stringify(graphJson.schema_version)}; expected ${SUPPORTED_SCHEMA_VERSION}. Regenerate the graph with sas-graph.`,
    };
  }

  return {
    graphJson,
    lineIndex: buildLineIndex(sourceRecordsFromGraph(graphJson)),
  };
}

module.exports = { loadGraphFromPath, SUPPORTED_SCHEMA_VERSION };
