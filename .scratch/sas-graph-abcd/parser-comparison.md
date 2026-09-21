# PR 9 — Optional parser comparison report

Date: 2026-09-21
Decision scope: legacy frontend versus `ix-infrastructure/tree-sitter-sas` only

## Decision

**Recommendation: optional desktop-only frontend.** The isolated parser installs
successfully on this Windows x86_64 host and exposes useful typed DATA-step,
PROC SQL, include, and macro nodes, while the measured span differences and the
unverified restricted Linux environment rule out replacing the legacy frontend
or making Tree-sitter a core dependency. The desktop option preserves the
Python 3.8 core and keeps parser-specific objects out of the graph contract;
the current report does not build an adapter.

## Scope and method

This is the Phase B3 comparison requested by [dev-plan §7 Phase B3](dev-plan.md#phase-b3--optional-parser-evaluation-spike-3-5-days-time-boxed).
Ticket 09 is treated as settled input. In particular, `tree-sitter-sas` is the
only candidate that the feasibility report found both installable and
license-clean; `pjankiewicz/sasparser` has no published release and therefore
cannot be a harness target ([ticket 09](issues/09-parser-frontend-feasibility.md#answer),
[feasibility report](parser-feasibility.md#2-ix-infrastructuretree-sitter-sas)).
No `sas-lexer`, `sasparser`, `ceyson/sas-parser`, or third-party source code was
used.

The harness is [`tools/parser_compare.py`](../../tools/parser_compare.py). It:

1. Creates a temporary virtualenv and installs exactly
   `tree-sitter-sas==0.4.2` and `tree-sitter==0.24.0` with
   `--only-binary=:all:`.
2. Re-executes itself inside that environment, imports the legacy scanner from
   `src/sas_graph`, and parses every committed `.sas` file under
   `tests/fixtures/`.
3. Records the legacy statement's raw and token-start UTF-8 byte offsets,
   line spans, normalized text, termination flag, comments, and findings. It
   records Tree-sitter named-node spans, node types, error nodes, and missing
   nodes.
4. Calls the existing DATA-step and PROC SQL IR entry points without mutating a
   graph. The harness is not imported by `src/sas_graph` and writes no virtualenv
   into the repository.

The legacy comparison is against the actual scanner contract: `Statement`
retains normalized and verbatim text plus line spans
([`statements.py:58-84`](../../src/sas_graph/statements.py#L58-L84)), comments are
kept as inactive evidence, and malformed delimiters become findings rather
than silently disappearing ([`statements.py:290-420`](../../src/sas_graph/statements.py#L290-L420)).
`group_blocks` is used only to identify the existing DATA/PROC IR inputs
([`blocks.py:69-107`](../../src/sas_graph/blocks.py#L69-L107)).

`TS boundaries` below counts deduplicated named grammar nodes whose type ends in
`_statement` or `_header`, plus explicit macro/NULL nodes. It is deliberately
not presented as the same abstraction as one legacy statement: Tree-sitter
contains SQL SELECT nodes inside CREATE nodes and macro control nodes spanning
their bodies. `Exact token bytes` trims only leading horizontal indentation
from a legacy raw span, then checks for a named Tree-sitter node with the same
UTF-8 byte range; this avoids treating indentation as a grammar disagreement.

## Installation feasibility

### Windows — measured

The command was run on Windows 10 AMD64, Python 3.13.2:

```text
python tools/parser_compare.py --format markdown
```

The harness created and removed a temporary virtualenv and successfully
installed both pinned packages with binary-only pip. The parser then imported
and parsed all 26 fixtures. This is a real installation and grammar-load
measurement for this host; it is not an inference from package metadata.
Ticket 09's wheel inventory explains why this works on Windows: the grammar
release publishes a CPython 3.10 ABI-3 Windows x86_64 wheel and the runtime is
pinned separately ([feasibility report, §2](parser-feasibility.md#2-ix-infrastructuretree-sitter-sas)).

### Restricted Linux server — not verified

No restricted Linux server, its Python executable, glibc level, or offline
wheelhouse was available in this run. I did not claim an install or fabricate a
Linux result. The desk research records a manylinux x86_64 grammar wheel, so a
staged wheelhouse may work on a compatible Python 3.10+ server, but the actual
server ABI, whether a second Python can be installed, whether the runtime wheel
is staged, and whether the grammar loads there remain **UNKNOWN**. The harness
will skip cleanly and print the exact captured virtualenv or pip error if it is
run where Python/venv or the binary-only install is unavailable.

The Python 3.8 core constraint remains binding: even a successful optional
Python 3.10+ installation cannot be added to project install requirements.

## Measured corpus comparison

The gold corpus contained 26 committed `.sas` files and 3,391 UTF-8 bytes. The
legacy frontend emitted 95 statements, 5 comment records, and 5
`unterminated_comment` findings. Tree-sitter emitted 101 grammar boundary
spans, had 86 exact token-byte matches to legacy statements (86/95 = 90.5%),
and reported 2 `ERROR` nodes plus 3 missing nodes.

### Statement boundaries and source spans

| Fixture | Bytes | Legacy statements | TS boundaries | Exact token bytes | Legacy findings | TS error/missing |
|---|---:|---:|---:|---:|---:|---:|
| `basic_adae/adae.sas` | 335 | 8 | 8 | 8 | 0 | 0/0 |
| `basic_adae/macros/gm_derive.sas` | 298 | 5 | 5 | 4 | 0 | 0/0 |
| `basic_adae/setup.sas` | 100 | 4 | 4 | 4 | 0 | 0/0 |
| `includes/broken_immediately_a.sas` | 11 | 0 | 0 | 0 | 1 | 1/0 |
| `includes/broken_immediately_b.sas` | 11 | 0 | 0 | 0 | 1 | 1/0 |
| `includes/child.sas` | 16 | 1 | 1 | 1 | 0 | 0/0 |
| `includes/child_with_comment.sas` | 54 | 1 | 1 | 1 | 0 | 0/0 |
| `includes/child_with_mid_comment.sas` | 46 | 2 | 2 | 2 | 0 | 0/0 |
| `includes/child_with_parse_error.sas` | 23 | 1 | 2 | 1 | 1 | 0/1 |
| `includes/nests_another.sas` | 22 | 1 | 1 | 1 | 0 | 0/0 |
| `includes/nests_missing.sas` | 75 | 3 | 3 | 3 | 0 | 0/0 |
| `includes/self_loop.sas` | 26 | 1 | 1 | 1 | 0 | 0/0 |
| `includes/sibling_a_broken.sas` | 23 | 1 | 2 | 1 | 1 | 0/1 |
| `includes/sibling_b_broken.sas` | 23 | 1 | 2 | 1 | 1 | 0/1 |
| `sql_constructs/setup.sas` | 54 | 0 | 0 | 0 | 0 | 0/0 |
| `sql_constructs/sql_constructs.sas` | 501 | 4 | 6 | 4 | 0 | 0/0 |
| `synthetic_multi_program/autoexec.sas` | 83 | 3 | 3 | 3 | 0 | 0/0 |
| `synthetic_multi_program/bootstrap.sas` | 298 | 8 | 8 | 6 | 0 | 0/0 |
| `synthetic_multi_program/macros/dynamic_split.sas` | 278 | 13 | 12 | 8 | 0 | 0/0 |
| `synthetic_multi_program/macros/simple_copy.sas` | 99 | 5 | 5 | 4 | 0 | 0/0 |
| `synthetic_multi_program/program_001_derive.sas` | 253 | 8 | 8 | 8 | 0 | 0/0 |
| `synthetic_multi_program/program_002_summary.sas` | 235 | 4 | 4 | 4 | 0 | 0/0 |
| `variable_lineage/adam_adlb.sas` | 136 | 9 | 9 | 9 | 0 | 0/0 |
| `variable_lineage/sdtm_lb.sas` | 61 | 4 | 4 | 4 | 0 | 0/0 |
| `variable_lineage/setup.sas` | 108 | 4 | 4 | 4 | 0 | 0/0 |
| `variable_lineage/tlf_summary.sas` | 222 | 4 | 6 | 4 | 0 | 0/0 |

The nine unmatched legacy spans were:

- `basic_adae/macros/gm_derive.sas`, bytes 207–239: `%macro gm_derive(inds=, outds=);`.
- `synthetic_multi_program/bootstrap.sas`, bytes 0–21 `%macro env_bootstrap;`, and bytes 78–133 `libname refdata ... access=readonly;`.
- `synthetic_multi_program/macros/dynamic_split.sas`, bytes 0–36 `%macro ...;`, 41–77 `%if ... %then %do;`, 139–144 and 251–256 `%end;`, and 149–159 `%else %do;`.
- `synthetic_multi_program/macros/simple_copy.sas`, bytes 0–34 `%macro simple_copy(inds=, outds=);`.

These are not random span failures. Tree-sitter represents macro definitions
as one aggregate `macro_definition` node, represents `%if/%do/%else/%end` as
nested control nodes, and does not type the `libname` line in this corpus as an
exactly matching statement span. The legacy scanner's explicit bare macro-call
handling is documented at [`statements.py:23-34`](../../src/sas_graph/statements.py#L23-L34);
Tree-sitter instead recognizes bare calls as grammar nodes, which accounts for
one of the macro-count differences below.

### Recovered errors in `includes/`

The 11 include fixtures were parsed independently, without expanding `%include`.

| Include fixture | Legacy statements | Legacy finding types | TS errors | TS missing |
|---|---:|---|---:|---:|
| `includes/broken_immediately_a.sas` | 0 | `{"unterminated_comment": 1}` | 1 | 0 |
| `includes/broken_immediately_b.sas` | 0 | `{"unterminated_comment": 1}` | 1 | 0 |
| `includes/child.sas` | 1 | `{}` | 0 | 0 |
| `includes/child_with_comment.sas` | 1 | `{}` | 0 | 0 |
| `includes/child_with_mid_comment.sas` | 2 | `{}` | 0 | 0 |
| `includes/child_with_parse_error.sas` | 1 | `{"unterminated_comment": 1}` | 0 | 1 |
| `includes/nests_another.sas` | 1 | `{}` | 0 | 0 |
| `includes/nests_missing.sas` | 3 | `{}` | 0 | 0 |
| `includes/self_loop.sas` | 1 | `{}` | 0 | 0 |
| `includes/sibling_a_broken.sas` | 1 | `{"unterminated_comment": 1}` | 0 | 1 |
| `includes/sibling_b_broken.sas` | 1 | `{"unterminated_comment": 1}` | 0 | 1 |

Legacy totals are 5 findings. Tree-sitter retains a tree for all 11 files, but
uses two `ERROR` nodes for comments that start the file and three inserted
missing semicolon nodes for the other malformed files. Neither result should
be silently normalized into a clean statement stream: an adapter would need a
diagnostic mapping that preserves both the syntax-node span and the existing
finding semantics.

### Macro behavior

The legacy column is a scanner marker count; the Tree-sitter column is a named
grammar-node count, so the counts intentionally measure different abstractions.

| Fixture | Legacy scanner markers (`definitions / ends / ifs / includes / calls / variable refs`) | Tree-sitter named macro nodes |
|---|---|---|
| `basic_adae/adae.sas` | 0 / 0 / 0 / 0 / 2 / 1 | `macro_call_statement=2, macro_variable_ref=1` |
| `basic_adae/macros/gm_derive.sas` | 1 / 1 / 0 / 0 / 0 / 2 | `macro_definition=1, macro_end=1, macro_variable_ref=2` |
| `basic_adae/setup.sas` | 0 / 0 / 0 / 0 / 0 / 2 | `macro_variable_assignment=2` |
| `includes/nests_another.sas` | 0 / 0 / 0 / 1 / 0 / 0 | `include_statement=1` |
| `includes/nests_missing.sas` | 0 / 0 / 0 / 2 / 0 / 0 | `include_statement=2` |
| `includes/self_loop.sas` | 0 / 0 / 0 / 1 / 0 / 0 | `include_statement=1` |
| `synthetic_multi_program/autoexec.sas` | 0 / 0 / 0 / 1 / 0 / 0 | `include_statement=1, macro_variable_assignment=2` |
| `synthetic_multi_program/bootstrap.sas` | 1 / 1 / 0 / 0 / 2 / 2 | `macro_call_statement=3, macro_definition=1, macro_end=1, macro_variable_assignment=2, macro_variable_ref=2` |
| `synthetic_multi_program/macros/dynamic_split.sas` | 1 / 1 / 1 / 0 / 0 / 5 | `macro_definition=1, macro_do_statement=2, macro_end=1, macro_if_statement=1, macro_variable_ref=5` |
| `synthetic_multi_program/macros/simple_copy.sas` | 1 / 1 / 0 / 0 / 0 / 2 | `macro_definition=1, macro_end=1, macro_variable_ref=2` |
| `synthetic_multi_program/program_001_derive.sas` | 0 / 0 / 0 / 0 / 1 / 0 | `macro_call_statement=1, macro_variable_assignment=1` |
| `synthetic_multi_program/program_002_summary.sas` | 0 / 0 / 0 / 0 / 3 / 0 | `macro_call_statement=3, macro_variable_assignment=1` |
| `variable_lineage/setup.sas` | 0 / 0 / 0 / 0 / 0 / 3 | `macro_variable_assignment=1` |

Corpus marker totals are legacy `definitions=4`, `ends=4`, `ifs=1`,
`includes=5`, `calls=8`, `variable_refs=17`; Tree-sitter totals are
`macro_definition=4`, `macro_end=4`, `macro_if_statement=1`,
`macro_do_statement=2`, `include_statement=5`, `macro_call_statement=9`,
`macro_variable_assignment=9`, and `macro_variable_ref=12`. The extra Tree-sitter
call is the bare `%env_bootstrap;` call, which the legacy marker regex does not
count because it has no parentheses. The lower Tree-sitter variable-reference
count is a grammar classification difference, not evidence that the source
contains fewer macro references.

### IR coverage

The current IR is intentionally small: `SourceSpan`, `IRAssignment`, and
`IRUnknownExpression` are defined in [`ir.py:13-108`](../../src/sas_graph/ir.py#L13-L108);
the DATA-step entry point is `parse_assignment`
([`data_step_ir.py:353-384`](../../src/sas_graph/data_step_ir.py#L353-L384));
PROC SQL uses `IRSqlStatement` and `parse_sql`
([`sql_ir.py:107-121`](../../src/sas_graph/sql_ir.py#L107-L121),
[`sql_ir.py:784-820`](../../src/sas_graph/sql_ir.py#L784-L820)). The harness
called these existing entry points against the same legacy statements and
reported the Tree-sitter typed nodes alongside them.

| Fixture with current IR input | DATA assignment candidates / parsed / unknown | SQL candidates / parsed / errors | Tree-sitter typed coverage |
|---|---:|---:|---|
| `sql_constructs/sql_constructs.sas` | 0 / 0 / 0 | 2 / 2 / 0 | `sql_create_statement=2, sql_select_statement=2, sql_join_clause=2, table_reference=4` |
| `synthetic_multi_program/macros/dynamic_split.sas` | 0 / 0 / 0 | 0 / 0 / 0 | `data_step=2, data_step_header=2, set_statement=2, generic_statement=1` |
| `synthetic_multi_program/macros/simple_copy.sas` | 0 / 0 / 0 | 0 / 0 / 0 | `data_step=1, data_step_header=1, set_statement=1` |
| `synthetic_multi_program/program_001_derive.sas` | 0 / 0 / 0 | 0 / 0 / 0 | `data_step=1, data_step_header=1, set_statement=1, generic_statement=1` |
| `variable_lineage/adam_adlb.sas` | 3 / 3 / 0 | 0 / 0 / 0 | `data_step=2, data_step_header=2, set_statement=2, generic_statement=3` |
| `variable_lineage/sdtm_lb.sas` | 1 / 1 / 0 | 0 / 0 / 0 | `data_step=1, data_step_header=1, set_statement=1, generic_statement=1` |
| `variable_lineage/tlf_summary.sas` | 0 / 0 / 0 | 2 / 2 / 0 | `sql_create_statement=2, sql_select_statement=2, table_reference=2` |

Across the full corpus, Tree-sitter typed 9 DATA steps, 9 DATA headers, 9 SET
statements, 4 SQL CREATE statements, 4 SQL SELECT statements, 2 SQL JOIN
clauses, and 6 table references. It also left 18 ordinary statements as
`generic_statement`; the existing 4 direct DATA assignment candidates all
parsed, and the 4 SQL candidates all parsed. This supports a specific
architectural conclusion:

- A future adapter could reach SQL `CREATE`/`SELECT`/JOIN/table structure and
  macro control structure more directly than the current statement-text
  route.
- Tree-sitter does not make the current `IRAssignment` slice disappear: the
  assignment-bearing statements remain generic in this corpus, so the adapter
  would still need the existing conservative expression parser or equivalent
  clean-room behavior.
- Tree-sitter's aggregate macro nodes and missing/error nodes require an
  explicit source-span and diagnostic translation before they can feed the
  current IR, whose source evidence must remain exact rather than guessed.

## Interpretation

The measurements show a useful but bounded improvement, not a drop-in
replacement:

1. **Boundaries and spans:** 86/95 legacy token spans have exact Tree-sitter
   named-node spans. The nine misses cluster in macro directive boundaries and
   one `libname` statement; SQL contributes extra nested grammar boundaries
   rather than losing the enclosing CREATE span.
2. **Errors:** both frontends preserve evidence for malformed include files,
   but they expose different diagnostics. Legacy reports five unterminated
   comments and retains the valid prefix; Tree-sitter reports two error nodes
   and three inserted semicolon nodes. A replacement that only counts parsed
   statements would hide this difference.
3. **Macros:** Tree-sitter recognizes macro definitions, `%if/%do`, `%include`,
   assignments, references, and bare calls as distinct node types. The legacy
   scanner's behavior is intentionally different and is backed by its own
   delimiter rules; a migration would need differential tests for both models.
4. **IR:** the strongest gain is typed SQL and macro structure. The DATA
   assignment slice remains text-level, so adopting Tree-sitter would add an
   adapter and diagnostics without eliminating the current IR parser.

## Reproduction and acceptance notes

The standalone smoke command is:

```text
python tools/parser_compare.py --format markdown
```

Use `--format json` for the complete per-node and per-span evidence. If the
host is below Python 3.10, cannot create a venv, or cannot install the pinned
binary wheels, the command exits 0 with `[SKIP]` and the exact reason captured
from the failing operation; it never installs into the active interpreter and
is not part of pytest. The run recorded here completed successfully.

No files under `src/sas_graph/` or `vscode-extension/` were changed, and no
runtime dependency or schema version was changed. The report corrects the
obsolete B3 `sasparser` harness target rather than pretending that an
unpublished package was compared.

The repository regression suite also passed unchanged: `python -m pytest tests
-q` reported `699 passed, 12 skipped in 3.98s`.

## Recommendation

**Recommendation: optional desktop-only frontend.** Keep the legacy scanner as
the only core frontend, preserve its exact source/finding contract, and permit
a later isolated Tree-sitter adapter for desktop analysis after differential
tests define the nine boundary mismatches and the five include diagnostics;
the measured Windows installation and typed SQL/macro coverage justify the
option, while the unverified restricted Linux host and Python 3.8 core rule out
making it mandatory or shipping it as the server path now.
