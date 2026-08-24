"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");

const { createSafeModelError, isSafeModelError } = require("../src/cliRunner.js");

const toolsPath = require.resolve("../src/languageModelTools.js");

class FakeLanguageModelTextPart {
  constructor(value) {
    this.value = value;
  }
}

class FakeLanguageModelToolResult {
  constructor(content) {
    this.content = content;
  }
}

async function withMocks({ runCliQuery, resolveGraphJsonPath }, callback) {
  const originalLoad = Module._load;
  delete require.cache[toolsPath];
  Module._load = function load(request, parent, isMain) {
    if (request === "./cliRunner.js") {
      return { runCliQuery, createSafeModelError, isSafeModelError };
    }
    if (request === "./projectConfig.js") return { resolveGraphJsonPath };
    if (request === "vscode") {
      return {
        LanguageModelTextPart: FakeLanguageModelTextPart,
        LanguageModelToolResult: FakeLanguageModelToolResult,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  try {
    return await callback(require(toolsPath));
  } finally {
    Module._load = originalLoad;
    delete require.cache[toolsPath];
  }
}

test("invoke validates input before graph resolution or CLI dispatch", async () => {
  let resolverCalled = false;
  let runnerCalled = false;

  await withMocks({
    resolveGraphJsonPath: () => {
      resolverCalled = true;
      throw new Error("graph should not be resolved");
    },
    runCliQuery: () => {
      runnerCalled = true;
      throw new Error("CLI should not run");
    },
  }, async ({ SasGraphSearchTool }) => {
    const result = await new SasGraphSearchTool().invoke({ input: { query: 42 } });

    assert.equal(result.content[0].value, "error: query must be a string");
  });

  assert.equal(resolverCalled, false);
  assert.equal(runnerCalled, false);
});
test("invoke maps validated input to the shared CLI runner and returns compact JSON", async () => {
  const calls = [];

  await withMocks({
    resolveGraphJsonPath: () => ({ path: "C:\\workspace\\graph.json" }),
    runCliQuery: async (...args) => {
      calls.push(args);
      return { start: "dataset:work.a", upstream_nodes: [], downstream_nodes: [], edges: [] };
    },
  }, async ({ SasGraphTraceLineageTool }) => {
    const result = await new SasGraphTraceLineageTool().invoke({
      input: { node: "dataset:work.a", direction: "downstream" },
    });

    assert.equal(
      result.content[0].value,
      '{"start":"dataset:work.a","upstream_nodes":[],"downstream_nodes":[],"edges":[]}',
    );
  });

  assert.deepEqual(calls, [[
    "query-lineage",
    "C:\\workspace\\graph.json",
    ["--node", "dataset:work.a", "--direction", "downstream"],
  ]]);
});

test("invoke converts resolver failures into sanitized model text", async () => {
  await withMocks({
    resolveGraphJsonPath: () => ({
      error: "SAS Graph: no project.yaml found at the workspace root (C:\\private\\project.yaml). Run sas-graph in a workspace with a project.yaml, or choose one manually.",
    }),
    runCliQuery: async () => ({}),
  }, async ({ SasGraphSearchTool }) => {
    const result = await new SasGraphSearchTool().invoke({ input: { query: "ae" } });

    assert.equal(
      result.content[0].value,
      "error: SAS Graph: no project.yaml found in the workspace. Run sas-graph in a workspace with a project.yaml, or choose one manually.",
    );
    assert.ok(!result.content[0].value.includes("private"));
  });
});

test("invoke maps every known resolver shape to fixed safe guidance", async () => {
  const cases = [
    [
      "SAS Graph: no workspace folder is open. Open the folder containing project.yaml first.",
      "error: SAS Graph: no workspace folder is open. Open the folder containing project.yaml first.",
    ],
    [
      "SAS Graph: no project.yaml found at the workspace root (C:\\private\\project.yaml). Run sas-graph in a workspace with a project.yaml, or choose one manually.",
      "error: SAS Graph: no project.yaml found in the workspace. Run sas-graph in a workspace with a project.yaml, or choose one manually.",
    ],
    [
      "SAS Graph: project.yaml at C:\\private\\project.yaml does not declare output_dir.",
      "error: SAS Graph: project.yaml does not declare output_dir.",
    ],
    [
      "SAS Graph: no runs found under C:\\private\\graph_runs. Run \"sas-graph run\" first, then try again.",
      "error: SAS Graph: no runs found. Run \"sas-graph run\" first, then try again.",
    ],
    [
      "SAS Graph: run 20260824 has no graph.json at C:\\private\\graph.json.",
      "error: SAS Graph: latest run has no graph.json. Run \"sas-graph run\" again.",
    ],
  ];

  for (const [error, expected] of cases) {
    await withMocks({
      resolveGraphJsonPath: () => ({ error }),
      runCliQuery: async () => ({}),
    }, async ({ SasGraphSearchTool }) => {
      const result = await new SasGraphSearchTool().invoke({ input: { query: "ae" } });
      assert.equal(result.content[0].value, expected);
    });
  }
});

test("invoke rejects forged resolver text instead of trusting its prefix", async () => {
  await withMocks({
    resolveGraphJsonPath: () => ({
      error: "SAS Graph: no project.yaml found at the workspace root (C:\\private\\project.yaml). API_KEY=secret",
    }),
    runCliQuery: async () => ({}),
  }, async ({ SasGraphSearchTool }) => {
    const result = await new SasGraphSearchTool().invoke({ input: { query: "ae" } });

    assert.equal(result.content[0].value, "error: SAS Graph query failed.");
  });
});

test("invoke falls back when resolver returns an unknown or malformed error", async () => {
  for (const resolverResult of [
    { error: "unexpected resolver failure" },
    { error: { message: "SAS Graph: no workspace folder is open." } },
    {},
  ]) {
    await withMocks({
      resolveGraphJsonPath: () => resolverResult,
      runCliQuery: async () => ({}),
    }, async ({ SasGraphSearchTool }) => {
      const result = await new SasGraphSearchTool().invoke({ input: { query: "ae" } });
      assert.equal(result.content[0].value, "error: SAS Graph query failed.");
    });
  }
});

test("invoke returns an actionable safe CLI reason", async () => {
  await withMocks({
    resolveGraphJsonPath: () => ({ path: "graph.json" }),
    runCliQuery: async () => {
      throw createSafeModelError(
        "sas-graph query-lineage failed (exit code 1): lineage start must be Dataset, Step, or SqlStatement",
      );
    },
  }, async ({ SasGraphTraceLineageTool }) => {
    const result = await new SasGraphTraceLineageTool().invoke({
      input: { node: "variable:work.flag" },
    });

    assert.equal(
      result.content[0].value,
      "error: sas-graph query-lineage failed (exit code 1): lineage start must be Dataset, Step, or SqlStatement",
    );
  });
});
