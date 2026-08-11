# sas-graph

Source-only static dependency graph for SAS task programs. Reads SAS source,
emits a graph — never executes SAS, never opens a dataset.

See the project's internal design plan for the full design.

## Status

Phases 0-4 of the dev plan (section 21) are implemented: configuration,
source parsing, `%include` / `%let` resolution, DATA/PROC/macro rules, and the
CLI `run` command produce an auditable static graph. Phase 5, acceptance on a
real anonymized program, has run and closed with concerns.

| Phase | What | State |
|-------|------|-------|
| 0 | Output contract, Mermaid and findings renderers | done |
| 1 | Config loader, allowed-root validation, manifest | done |
| 2 | Tokenizer and statement model | done |
| 3 | Macro state, `%include`, `%let` resolution | done |
| 4 | DATA / PROC SORT / PROC SQL / macro rules and `run` | done |
| 5 | Acceptance test on an anonymized program | done with concerns |

Phase 5's acceptance evidence — the run, every §21/§23 criterion verdict, and
the two remaining concerns — is recorded in the project's internal acceptance
report.

`manifest.json` sits in Phase 1 per §21, not Phase 0, even though §4.2 lists it
in the generation order — it records source hashes, which need the config
loader.

## Try it (Windows Command Prompt)

Open **Command Prompt** in the repository root. The installed command is
`sas-graph`; `sas-graph.cli` is not a command name. Install the project once
in editable mode:

```cmd
python -m pip install -e .
```

Then run:

```cmd
sas-graph validate --config tests\fixtures\basic_adae\project.yaml
sas-graph run --config tests\fixtures\basic_adae\project.yaml
sas-graph render-mermaid --graph examples\hand_written_graph.json --out runs\demo\graph.mmd
sas-graph render-findings --graph examples\hand_written_graph.json --out runs\demo\findings.md
```

If you do not want to install the project, run it directly from the source
checkout instead. This setting applies only to the current Command Prompt
window:

```cmd
set "PYTHONPATH=src"
python -m sas_graph.cli validate --config tests\fixtures\basic_adae\project.yaml
python -m sas_graph.cli run --config tests\fixtures\basic_adae\project.yaml
python -m sas_graph.cli render-mermaid --graph examples\hand_written_graph.json --out runs\demo\graph.mmd
python -m sas_graph.cli render-findings --graph examples\hand_written_graph.json --out runs\demo\findings.md
```

`validate` exits 0 when the config is clean and 1 when it is not, printing the
same findings format a real run would write. It also writes
`<output_dir>/runs/<run_id>/manifest.json` — the record that a validation
happened, which for a FAILED run is the only artifact produced. `run` writes
`graph.json`, `graph.mmd`, `findings.md`, and `manifest.json` under its own
`<output_dir>/runs/<run_id>/` directory; it exits 0 for COMPLETE/PARTIAL graphs
and 1 for config or graph failure.

Validating the fixture writes into `tests/fixtures/basic_adae/graph_runs/`;
that directory is generated output, not source.

## Config safety

`validate` is the safety boundary, so two rules are worth knowing:

- **Relative paths in `project.yaml` resolve against the config file**, not the
  working directory, so a config means the same thing from any shell.
- **`allowed_roots` is a read boundary.** Every declared source file must resolve
  inside one, checked with `Path.resolve()` and a component-aware relative-path
  check so `..` and a shared name prefix (`/study/adae-evil` against
  `/study/adae`) both fail. The check remains compatible with Python 3.8.
  `output_dir` is a write target and is deliberately not held to it — it is only
  refused if it points into a production or log location.

Every config problem is FAILED, never PARTIAL (dev plan §5.3), and all of them
are reported in one pass rather than stopping at the first.

## Tests

From Command Prompt:

```cmd
python -m pytest tests -q
```

Each test module also runs standalone (`python tests/test_renderer_mermaid.py`)
and prints its rendered output.

## The graph contract

`examples/hand_written_graph.json` is the frozen `schema_version` `0.2.0`
contract, hand-written per dev plan section 22. It is the reference for what
the parser must emit and what consumers may rely on.

```json
{
  "schema_version": "0.2.0",
  "run_status": "COMPLETE | PARTIAL | FAILED",
  "nodes": [{"id": "...", "type": "...", "label": "...", "source": {}}],
  "edges": [{"id": "...", "type": "...", "from": "...", "to": "...", "source": {}}],
  "findings": [{"id": "...", "status": "...", "source": {}}]
}
```

Every node, edge and finding carries a nullable `source` block of `file`,
`line_start`, `line_end`, `statement_order`, `original_text`, `rule` — section 7
of the plan. Contract-derived edges carry a second `contract_source` block, so
an inferred dependency always names both the SAS call and the macro contract
that justified it.

Two properties worth knowing before building on this:

- **Dataset nodes have `source: null`.** A dataset is written by one step and
  read by another, so it has no single line range. Its locations live on the
  `reads_dataset` and `writes_dataset` edges instead.
- **Node ids keep their dots** (`dataset:sdtm.ae`). The Mermaid renderer
  sanitizes them at render time, so the canonical file stays readable.

## Design principle

> The MVP should preserve known structure and expose unknowns.
> It should not hide uncertainty by guessing.

Concretely: a macro whose source is missing gets an `UnknownMacro` node and no
invented reads or writes. An unresolved `&macrovar.` becomes an
`UnknownDataset` rather than a guess. Both show up in `findings.md` with the
line that caused them.
