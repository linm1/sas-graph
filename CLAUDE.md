# sas-graph

Source-only static dependency graph parser for SAS task programs. Reads SAS
source, emits `graph.json`/`graph.mmd`/`findings.md`. Never executes SAS,
never opens a dataset (`sas_graph_dev_plan.md` section 2, non-negotiable).

## Architecture (as built, not aspirational)

Pipeline, in `run_pipeline.py`'s own order:

1. `config.py` — loads `project.yaml`, enforces `allowed_roots` as a read
   boundary. Every config problem is FAILED, all reported in one pass.
2. `statements.py` / `blocks.py` — tokenize source into statements, group
   into DATA/PROC blocks.
3. `macro_state.py` / `macro_index.py` / `macro_contracts.py` — `%include`
   expansion, `%let` resolution, macro source lookup, md-parsed macro contracts.
4. `rules_*.py` — one file per block kind (`data_step`, `proc_sort`,
   `proc_sql`, `libname`, `macro_call`, `inactive`). Each only knows its own
   block kind and writes into the one shared `GraphContext`. **No rule module
   knows about another** — this boundary is deliberate, keep it that way.
5. `graph_model.py` — `GraphContext` is the *only* place that mints node/edge
   ids or appends to the three lists. `ctx.add_node` / `ctx.add_edge` /
   `ctx.add_finding` are the only mutation surface; don't add a second one.
6. `graph_io.py` / `manifest.py` / `renderer_mermaid.py` / `renderer_findings.py`
   — save, reload, render. `cli.py` always saves then reloads before
   rendering (section 4.2) — renderers are only ever tested against a
   reloaded dict, so don't render straight from the in-memory graph.

## Where the design authority lives

The repository's module docstrings and documentation record the rationale
behind non-obvious choices. **Before changing behavior, find and read the
nearest documented rationale.** Don't infer intent from code shape alone when
a citation is sitting right there.

## Think Before Coding

- Trace the actual call path first: `cli.py` → `run_pipeline.run` →
  `_process_file` → the one `rules_*.apply` that owns this block kind. Don't
  guess which module handles something; `run_pipeline.py`'s dispatch (`if
  block.kind == "DATA"`, `elif block.proc_name == "sort"`, ...) is the ground
  truth.
- If a docstring cites a dev-plan section, read that section before touching
  the function under it. The comments in this codebase exist specifically to
  point at *why*, not to restate *what* — treat that as load-bearing, not
  decorative.
- Check `README.md`'s phase table before adding scope. Phase 5 (acceptance)
  already closed "with concerns" — know what those are before building on
  top of that area.

## Simplicity First

- The MVP's own design principle (README): **preserve known structure, expose
  unknowns, never guess.** An unresolvable macro variable becomes an
  `UnknownDataset` + a finding, not an inferred edge. Follow this pattern for
  new cases — don't invent resolution logic to make an edge "complete."
- One rule module per block kind already exists. A new case usually belongs
  inside an existing `rules_*.py`, not a new module — check there before
  adding a file.
- `GraphContext` deliberately carries no "current value of X" fields beyond
  `libref_map` and `sort_by_at`/`sort_by_of` (structural evidence, not macro
  substitution — section 11.3). Don't add a third unless it's the same kind
  of structural exception, not a convenience cache.

## Surgical Changes

- Keep the rule-module isolation intact: a change inside `rules_proc_sql.py`
  should never require touching `rules_data_step.py` to stay correct.
- Mutate the graph only through `ctx.add_node`/`add_edge`/`add_finding`.
  Id minting (`dataset_id`, `next_step_id`, ...) lives in `graph_model.py`
  only.
- Every rule module has a matching `tests/test_*.py`. Match that 1:1
  convention for new modules — and each test file must also run standalone
  (`python tests/test_x.py` prints its own rendered output), so keep tests
  free of pytest-only fixtures where the module supports it.
- Run `python -m pytest tests -q` (or `PYTHONPATH=src python -m pytest`)
  after any change. This is a small, fully-tested codebase — a change with no
  passing/updated test next to it is not done.

## Goal-Driven Execution

- Every change should map to a dev-plan phase/section, a `wayfinder/tickets/`
  decision, or an explicit user ask — not a hypothetical future need. This
  project already tracks phase completion in `README.md`; don't build ahead
  of the plan without being asked.
- Config-safety invariants are hard requirements, not style: relative paths
  in `project.yaml` resolve against the config file, `allowed_roots` is
  checked with `Path.resolve()` + `is_relative_to()`, and `output_dir` is
  only refused if it targets a production/log location. Don't loosen these
  to make a fixture pass.
- CLI exit codes are part of the contract: `0` success, `1`
  validation/run FAILED, `2` argparse usage error. Preserve them exactly.

## Subprojects

- `vscode-extension/` has its own `.claude/CLAUDE.md` — read that before
  working inside it; don't assume root conventions carry over 1:1 (it's
  TypeScript, not Python).
