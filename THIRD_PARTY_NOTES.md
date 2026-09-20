# Third-party evaluation notes

This file records evaluation only. No upstream code has been copied into this
repository, and no upstream dependency has been added. The facts below come
from `.scratch/sas-graph-abcd/parser-feasibility.md`; that report was a
source-and-metadata review, not an upstream installation or execution test.

## Provenance rule

Any copied or adapted code must record the upstream repository, exact commit,
file or function, license, local changes, and tests. A repository without a
clear license is idea-only until the rights holder supplies an explicit
license. Public schemas and design ideas may inform clean-room work, but they
are not copied code.

## Evaluated projects

### `mishamsk/sas-lexer`

- **Upstream and commit:** [repository](https://github.com/mishamsk/sas-lexer),
  commit [`7cd39aba21776f4ebbaa2689707e0c45f6097dce`](https://github.com/mishamsk/sas-lexer/commit/7cd39aba21776f4ebbaa2689707e0c45f6097dce).
- **Files reviewed:** [`pyproject.toml`](https://github.com/mishamsk/sas-lexer/blob/7cd39aba21776f4ebbaa2689707e0c45f6097dce/pyproject.toml),
  [`Cargo.toml`](https://github.com/mishamsk/sas-lexer/blob/7cd39aba21776f4ebbaa2689707e0c45f6097dce/Cargo.toml),
  and [`LICENSE`](https://github.com/mishamsk/sas-lexer/blob/7cd39aba21776f4ebbaa2689707e0c45f6097dce/LICENSE).
- **License:** `AGPL-3.0-or-later`; integration and delivery obligations remain
  unresolved for this project, so this is not a copied or required dependency.
- **Evaluation:** Native Rust/PyO3 package; published wheels cover CPython
  3.10-3.12 on the reviewed Windows and manylinux targets. It cannot be a
  Python 3.8 core dependency.
- **Local changes:** None. **Tests:** No upstream code was installed or run;
  no local compatibility tests were added.

### `ix-infrastructure/tree-sitter-sas`

- **Upstream and commit:** [repository](https://github.com/ix-infrastructure/tree-sitter-sas),
  commit [`3978750c85453fa81f263ae2561aad5bc153a80c`](https://github.com/ix-infrastructure/tree-sitter-sas/commit/3978750c85453fa81f263ae2561aad5bc153a80c),
  release `v0.4.2`.
- **Files reviewed:** [`pyproject.toml`](https://github.com/ix-infrastructure/tree-sitter-sas/blob/v0.4.2/pyproject.toml),
  [`setup.py`](https://github.com/ix-infrastructure/tree-sitter-sas/blob/v0.4.2/setup.py),
  and [`LICENSE`](https://github.com/ix-infrastructure/tree-sitter-sas/blob/v0.4.2/LICENSE).
- **License:** MIT.
- **Evaluation:** Native C extension with CPython 3.10+ `abi3` wheels on the
  reviewed Windows and manylinux targets. Retain only as a later isolated
  optional-adapter spike; it is not a Python 3.8 core dependency.
- **Local changes:** None. **Tests:** No upstream code was installed or run;
  no local compatibility tests were added.

### `pjankiewicz/sasparser`

- **Upstream and commit:** [repository](https://github.com/pjankiewicz/sasparser),
  commit [`576afe2575e0ebbb7918f8a13558ca07901854ee`](https://github.com/pjankiewicz/sasparser/commit/576afe2575e0ebbb7918f8a13558ca07901854ee).
- **Files reviewed:** [`pyproject.toml`](https://github.com/pjankiewicz/sasparser/blob/576afe2575e0ebbb7918f8a13558ca07901854ee/pyproject.toml)
  and [`README.md`](https://github.com/pjankiewicz/sasparser/blob/576afe2575e0ebbb7918f8a13558ca07901854ee/README.md).
- **License:** MIT is declared in project metadata and the README, but no
  standalone license file was found. Keep attribution and distribution review
  open.
- **Evaluation:** No published PyPI or GitHub release was found, so there is no
  upstream wheel to vendor. The repository declares Python 3.10+ and is
  conceptually useful only until an artifact and license-notice path are clear.
- **Local changes:** None. **Tests:** No upstream code was installed or run;
  no local compatibility tests were added.

### `ceyson/sas-parser`

- **Upstream and commit:** [repository](https://github.com/ceyson/sas-parser),
  commit [`da51082a01580078d5ca09fb219e540b123cdd3b`](https://github.com/ceyson/sas-parser/commit/da51082a01580078d5ca09fb219e540b123cdd3b).
- **Files reviewed:** [`pyproject.toml`](https://github.com/ceyson/sas-parser/blob/da51082a01580078d5ca09fb219e540b123cdd3b/pyproject.toml),
  [`README.md`](https://github.com/ceyson/sas-parser/blob/da51082a01580078d5ca09fb219e540b123cdd3b/README.md),
  and [`requirements.txt`](https://github.com/ceyson/sas-parser/blob/da51082a01580078d5ca09fb219e540b123cdd3b/requirements.txt).
- **License:** No license field, license notice, or LICENSE/COPYING/NOTICE file
  was found in the reviewed sources; usage and redistribution are therefore
  unknown. It remains idea-only.
- **Evaluation:** The actual distribution name is `sas_code_inventory`, not
  `sas-parser`; no published release or wheel was found. The repository
  declares Python 3.11+ and runtime dependencies including pandas and
  NetworkX, so it cannot be a Python 3.8 core dependency.
- **Local changes:** None. **Tests:** No upstream code was installed or run;
  no local compatibility tests were added.
