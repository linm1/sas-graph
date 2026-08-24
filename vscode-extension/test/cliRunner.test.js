"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const EventEmitter = require("node:events");
const Module = require("node:module");

const { safeCliFailureMessage, isSafeModelError } = require("../src/cliRunner.js");

async function withRunnerMock({ spawn }, callback) {
  const runnerPath = require.resolve("../src/cliRunner.js");
  const originalLoad = Module._load;
  delete require.cache[runnerPath];
  Module._load = function load(request, parent, isMain) {
    if (request === "node:child_process") return { spawn };
    if (request === "vscode") {
      return { workspace: { getConfiguration: () => ({ get: () => "python3" }) } };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  try {
    return await callback(require(runnerPath));
  } finally {
    Module._load = originalLoad;
    delete require.cache[runnerPath];
  }
}

test("CLI failures expose a safe summary instead of child stderr", () => {
  const message = safeCliFailureMessage("query-search", 1);

  assert.equal(message, "sas-graph query-search failed (exit code 1); check the graph and workspace configuration");
  assert.ok(!message.includes("stderr"));
  assert.ok(!message.includes("C:\\"));
  assert.equal(isSafeModelError(new Error(message)), false);
});

test("CLI failures preserve an actionable safe reason", () => {
  assert.equal(
    safeCliFailureMessage(
      "query-lineage",
      1,
      "query-lineage: lineage start must be Dataset, Step, or SqlStatement",
    ),
    "sas-graph query-lineage failed (exit code 1): lineage start must be Dataset, Step, or SqlStatement",
  );
  assert.equal(
    safeCliFailureMessage(
      "query-search",
      1,
      "Traceback: File \"C:\\private\\secret.py\", line 1; API_KEY=top-secret",
    ),
    "sas-graph query-search failed (exit code 1); check the graph and workspace configuration",
  );
  assert.equal(
    safeCliFailureMessage(
      "query-search",
      2,
      "query-search: [Errno 2] No such file or directory: 'C:\\private\\graph.json'",
    ),
    "sas-graph query-search failed (exit code 2): no graph.json found for this workspace — run 'sas-graph run' first",
  );
});

test("CLI failure classification covers controlled query reasons without echoing ids", () => {
  const cases = [
    ["unsupported schema_version '9.9.9'", "graph.json uses an unsupported schema; run sas-graph again"],
    ["JSONDecodeError: malformed graph", "graph.json is malformed; run 'sas-graph run' again"],
    ["Expecting value: line 1 column 1 (char 0)", "graph.json is malformed; run 'sas-graph run' again"],
    ["impact start must be a Variable or UnknownVariable", "impact start must be a Variable or UnknownVariable"],
    ["direction must be upstream, downstream, or both", "direction must be upstream, downstream, or both"],
    ["query must be a string", "query must be a string"],
    ["node must be a string", "node must be a string"],
    ["variable must be a string", "variable must be a string"],
    ["node not found: dataset:C:\\private\\secret", "node not found; use a fully typed graph node id"],
    ["variable not found: variable:C:\\private\\secret", "variable not found; use a fully typed graph variable id"],
    ["lineage edge 'e1' points to missing node 'secret'", "lineage graph contains a missing referenced node"],
  ];

  for (const [stderr, reason] of cases) {
    assert.equal(
      safeCliFailureMessage("query-search", 1, stderr),
      `sas-graph query-search failed (exit code 1): ${reason}`,
    );
  }
});

test("runCliQuery rejects with a safe actionable summary", async () => {
  await withRunnerMock({
    spawn: () => {
      const process = new EventEmitter();
      process.stdout = new EventEmitter();
      process.stderr = new EventEmitter();
      queueMicrotask(() => {
        process.stderr.emit("data", "query-search: graph run_status is FAILED; query a completed graph");
        process.emit("close", 1);
      });
      return process;
    },
  }, async ({ runCliQuery, isSafeModelError }) => {
    await assert.rejects(
      runCliQuery("query-search", "C:\\private\\graph.json", ["--query", "ae"]),
      (error) => {
        assert.equal(
          error.message,
          "sas-graph query-search failed (exit code 1): graph run_status is FAILED; query a completed graph",
        );
        assert.equal(error.diagnostics, undefined);
        assert.equal(isSafeModelError(error), true);
        return true;
      },
    );
  });
});

test("runCliQuery parses successful JSON stdout", async () => {
  await withRunnerMock({
    spawn: () => {
      const process = new EventEmitter();
      process.stdout = new EventEmitter();
      process.stderr = new EventEmitter();
      queueMicrotask(() => {
        process.stdout.emit("data", '{"ok":true}');
        process.emit("close", 0);
      });
      return process;
    },
  }, async ({ runCliQuery }) => {
    assert.deepEqual(
      await runCliQuery("query-search", "graph.json", ["--query", "ae"]),
      { ok: true },
    );
  });
});

test("runCliQuery reports invalid JSON without exposing parser details", async () => {
  await withRunnerMock({
    spawn: () => {
      const process = new EventEmitter();
      process.stdout = new EventEmitter();
      process.stderr = new EventEmitter();
      queueMicrotask(() => {
        process.stdout.emit("data", "not-json");
        process.emit("close", 0);
      });
      return process;
    },
  }, async ({ runCliQuery }) => {
    await assert.rejects(
      runCliQuery("query-search", "graph.json", []),
      (error) => {
        assert.equal(error.message, "sas-graph query-search returned invalid JSON");
        assert.equal(error.diagnostics, undefined);
        return true;
      },
    );
  });
});

test("runCliQuery sanitizes synchronous and asynchronous process-start failures", async () => {
  await withRunnerMock({
    spawn: () => {
      throw new Error("spawn failed at C:\\private\\python.exe");
    },
  }, async ({ runCliQuery }) => {
    await assert.rejects(
      runCliQuery("query-search", "graph.json", []),
      (error) => {
        assert.equal(error.message, "sas-graph query-search could not start the configured Python interpreter");
        assert.equal(error.diagnostics, undefined);
        return true;
      },
    );
  });

  await withRunnerMock({
    spawn: () => {
      const process = new EventEmitter();
      process.stdout = new EventEmitter();
      process.stderr = new EventEmitter();
      queueMicrotask(() => process.emit("error", new Error("ENOENT: C:\\private\\python.exe")));
      return process;
    },
  }, async ({ runCliQuery }) => {
    await assert.rejects(
      runCliQuery("query-search", "graph.json", []),
      (error) => {
        assert.equal(error.message, "sas-graph query-search could not start the configured Python interpreter");
        assert.equal(error.diagnostics, undefined);
        return true;
      },
    );
  });
});
