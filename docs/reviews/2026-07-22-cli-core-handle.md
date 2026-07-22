---
slug: cli-core-handle
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-cli-core-handle
diff: git diff main...HEAD
created: 2026-07-22
---

# Review: convert core CLI commands to the store handle — ADR-0015 step 3.6

## Brief  _(Author — Claude)_

**Intent.** Continue ADR-0015 step 3 by replacing the ad-hoc
`_check_store_schema(root)` guard with a `StoreHandle` opened at each command's
entry via a new `_open_store_or_exit(root, mode)` helper, and routing each
command's own store/registry access through handle methods. Deliberately scoped
to the **four commands that depend only on already-migrated modules**
(`enable`, `status`, `curate`, `init --guided`); `seed` + the `recommend` family
stay on the legacy `_check_store_schema` and convert with the recommender module
(they write through unconverted recommender sub-modules).

**Headline:** this closes the **G1 `init --guided` mutate-before-guard hole** —
`_init_guided` used to `register_project` (mutating `registry.toml`) *before* the
schema guard fired inside `ensure_tree`, and `UnsupportedSchemaError` then
propagated uncaught. The WRITE handle now opens before `register_project`.

**Scope.** Branch `adr-0015-cli-core-handle`, `git diff main...HEAD`
(implementation commit `d331a81`). Key files:

- `src/neurobase/cli/__init__.py` — `_open_store_or_exit` helper; `enable`,
  `status`, `curate`, `_init_guided` converted; `_check_store_schema` retained
  (now documented as the legacy guard for the unconverted commands).
- `tests/test_cli_init.py` — `test_guided_init_refuses_newer_schema_without_registering`.

**Focus areas.**

1. **Mode per command (the boundary rule).** `enable` = WRITE (register +
   ensure_tree through the handle); `init --guided` = WRITE (G1); `status` = READ;
   `curate` = READ. The two READ choices are deliberate: `status` only reads, and
   `curate` delegates *all* writes to `run_curate`, which owns its own WRITE
   handle (3.3) — the CLI handle there is only the guard + `resolve_project`.
   Confirm you agree curate should hold READ, not WRITE.
2. **G1 closure.** `_init_guided` opens `_open_store_or_exit(root, WRITE)` inside
   `if enable_repo:` (after the possible interactive root re-prompt, before
   `register_project`). The regression test was verified failing against the
   pre-fix path (it registered first, then crashed in `ensure_tree`). Confirm the
   ordering is correct and nothing writes before the handle opens.
3. **Guard-before-resolve ordering change.** Old `status`/`curate` resolved the
   project *before* guarding, so a non-enabled dir on a too-new store printed
   "Not an enabled project". Now the handle (guard) opens first, so that edge
   prints the schema error + exits 1 instead. Intended — flag if you'd rather
   preserve the old ordering.
4. **READ no longer creates `store.toml`.** `status`/`curate` on a fresh store no
   longer materialize `store.toml` (READ never writes). For an enabled project
   `store.toml` already exists, so this only affects the not-enabled path, which
   exits anyway. Confirm no test/consumer relied on that side effect.

**Known risks / tradeoffs.**

- `_check_store_schema` (which still creates `store.toml` via
  `ensure_store_metadata`) coexists with `_open_store_or_exit` until the
  recommender pass removes the last legacy callers.
- Unconverted helpers (`is_stale`, `read_fact_count_trend`, `locks`,
  `run_curate`) receive `handle.root`, matching the established boundary (e.g.
  MCP's `search.search(handle.root, …)`).

**How to verify.**

- `git diff main...HEAD`
- `uv run pytest tests/test_cli.py tests/test_cli_curate.py tests/test_cli_init.py tests/test_cli_phase1.py tests/test_hook_schema_guard.py -q`
- `uv run python scripts/ci.py` — full gate green: ruff, format, mypy,
  `1137 passed, 1 skipped`; coverage 91.67%.

**Out of scope.** `seed` + the `recommend` commands and their recommender
sub-modules (`corpus`/`seed`/`emitters`/`proposals`/`miner`/`ranker`); removing
`_check_store_schema` (happens when its last caller converts); the deferred
distill/linkify edges; per-method mode enforcement; steps 4–5.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->
