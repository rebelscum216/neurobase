---
slug: distill-locks-handle
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-distill-locks-handle
diff: git diff main...HEAD
created: 2026-07-23
---

# Review: route the last two raw-root store consumers through a StoreHandle — ADR-0015 step 4b

## Brief  _(Author — Claude)_

**Intent.** Step 3 landed the primary modules on the `StoreHandle`; 4a threaded the
first deferred edges (`search`/`linkify`). This is the **last pair of deferred
edges**: `core/locks.py` and `curator/distill.py`, the two remaining modules that
still built a store path off a bare `root` with no schema guard. Both modules'
*only* store **access** is `store.memory_dir(project, root)` — route each through a
validated handle so no store-path construction survives outside the chokepoint.
`read_doc`/`write_doc` on the digest-cache paths stay as **format-primitives** (the
boundary MCP and the recommender already set): the handle guards path *construction*,
not every byte-level read/write of a doc.

**The design rule (thread vs self-open).** This branch applies the same rule 4a and
3.7 established, and it is the crux of both changes:

> **Thread** the handle when the sole caller already holds one and test churn is
> small; **self-open** a handle internally (keeping the `root` signature) when there
> are many test call sites.

- `locks.py` → **thread** (like 4a search/linkify): the one production caller
  `cli.curate` already holds a READ handle, and there are only ~4 test sites.
- `distill.py` → **self-open** (like the 3.7 recommender): `distill_docs` has ~20
  test call sites, so it keeps its `root` signature and self-opens internally — zero
  test churn.

**Scope.** Branch `adr-0015-distill-locks-handle`, `git diff main...HEAD`
(implementation commit `513cf23`). Files:

- `core/locks.py` — `curate_lock_path(handle, project)` and
  `try_curate_lock(handle, project)` now take a `StoreHandle`; the lock path is
  `handle.memory_dir(project) / ".locks" / "curate.lock"`. The
  `from neurobase.core import store` import is dropped (unused) and replaced with
  `from neurobase.core.store_handle import StoreHandle` — the same import idiom 4a
  used in `search.py`/`linkify.py`.
- `curator/distill.py` — `_digests_dir(root, project)` self-opens a READ handle:
  `open_store(root, StoreMode.READ).memory_dir(project) / "raw" / _DIGESTS_DIRNAME`.
  `distill_docs` and `_distill_one` keep their `root` signature. `store` is still
  imported (used by `store.Document`/`read_doc`/`write_doc`); `StoreMode`/`open_store`
  added.
- `cli/__init__.py:220` — `locks.try_curate_lock(handle, project_slug)` (was
  `handle.root`).
- `tests/test_locks.py` — the scoping test opens two READ handles and passes them.
- `tests/test_cli_curate.py` — the busy-lock test opens a READ handle inline.

**Focus areas.**

1. **`locks.py` threading is behavior-preserving.** `handle.memory_dir(project)`
   delegates to `store.memory_dir(project, handle.root)`, so for the production
   caller — which passed `handle.root` before — the computed lock path is byte-for-byte
   identical. Confirm the path is unchanged and that dropping the `store` import left
   nothing dangling.

2. **`distill.py` self-open preserves D16 fail-soft.** `_digests_dir` is called at
   `distill.py:321`, *inside* `_distill_one`'s `try`. In production the store is
   already validated (the engine curates under its own handle — `engine.py:325` calls
   `distill_docs` after opening its curate handle), so `open_store` here never fails;
   it is a redundant-but-cheap guard. But if it ever did raise
   `UnsupportedSchemaError` (a too-new store), that raw degrades to its skim via
   `_distill_one`'s terminal `except Exception → return None` — **not** an aborted
   pass. Confirm the exception routing is right: `UnsupportedSchemaError` is neither
   `BrainError` nor `budget.BudgetExhausted`, so it does **not** trip the pass-level
   breakers at `distill.py:351/360` (those `raise`) and cannot be mistaken for a
   systemic backend failure — it lands on the D16 skim path.

3. **The format-primitive boundary is intact.** `_cache_read`/`_cache_write`
   (`store.read_doc`/`store.write_doc` on the `.digests/` sidecar) are deliberately
   left as path-primitives, matching the MCP/recommender boundary — the handle guards
   path *construction* (`_digests_dir`), and the read/write of a specific doc under
   that path stays a primitive. Confirm this is the same boundary and I didn't leave a
   raw-root store *access* anywhere in either module.

4. **Test parity.** `test_distill.py`'s `root` fixture is `tmp_path / "store"` with no
   `store.toml`; a READ open of an absent `store.toml` returns a handle with
   `schema is None` (uninitialized, not an error), and `handle.memory_dir` returns the
   same path the test helpers build with `store.memory_dir` directly — hence zero
   churn there. The two lock tests open real READ handles. Confirm the tests still
   assert the same contracts (lock scoping by store+project; busy-lock skips before
   the brain resolves).

**Known risks / tradeoffs.**

- **Per-raw self-open in `distill.py` (deliberate, flag if you disagree).**
  `_digests_dir` self-opens once per raw that reaches the cache-path step, so a
  backlog of N raws does N tiny `store.toml` reads per pass. This is the exact cost
  3.7 shipped and Codex accepted there ("tiny `store.toml` reads"). The alternative —
  opening once at the top of `distill_docs` and threading the handle down to
  `_distill_one`/`_digests_dir` (both are underscore-internal, so **no** test churn) —
  would collapse it to one open per pass, but it moves the open *outside*
  `_distill_one`'s `try`, so a schema failure would then need its own guard to
  preserve D16 rather than degrading for free. I chose the plan's literal per-raw
  self-open because it matches 3.7 and keeps the fail-soft behavior structural. If you
  think the once-per-pass internal thread ages better, say so — it's a clean follow-up.
- **Threading vs self-open is a judgment call**, same as 4a. `locks` threads because
  its sole caller holds a handle; `distill` self-opens because of its call-site count.
  If you'd draw either line differently, flag it.

**How to verify.**

- `git diff main...HEAD`
- `uv run pytest tests/test_locks.py tests/test_cli_curate.py tests/test_distill.py tests/test_curate_budget.py tests/test_curator.py -q`
  (focused: locks, curate integration, distill, budget, curator engine — all green here)
- `uv run python scripts/ci.py` — full gate (ruff, format, mypy, pytest, coverage).

**Out of scope.** The remaining ADR-0015 steps, none touched here:
- **4c** — remove the raw-`Path` `store.py`/`projects.py` signatures by folding their
  logic into the handle methods, **and** resolve the doctor's corrupt-`store.toml`
  `resolve_project` fallback (`diagnostics.py:146` — a registry read that must survive
  when no handle can open). **No `store.py`/`projects.py` signature changes in 4b.**
- **5** — the CI AST check forbidding store-path construction outside
  `core/store.py`, `core/store_handle.py`, `core/projects.py`.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->

### F1 — minor — `src/neurobase/core/locks.py:73`

The branch does not pass the repository's authoritative CI gate. `uv run python
scripts/ci.py` reports `ruff format --check` as failed because the new
`try_curate_lock` signature is not in Ruff's canonical format; `uv run ruff
format --check --diff src/neurobase/core/locks.py` shows that Ruff would collapse
lines 73–75 to one line. Ruff check, mypy, and all 1,147 tests pass, but the
format gate is a required pre-push check, so the brief's full-gate-green claim is
not currently reproducible. Suggested direction: run the configured formatter
on this file and rerun the full gate.

### F2 — minor — `tests/test_distill.py:116`

No test exercises the only new behavior in `curator/distill.py`: refusing digest
cache access through `_digests_dir` when `store.toml` has an unsupported schema,
then routing that refusal through D16's per-raw skim fallback. Every existing
distill test uses a supported or uninitialized store and would still pass if
line 252 regressed to the old raw-root `store.memory_dir(...)` call, so the suite
does not enforce this branch's central §10 chokepoint claim or the D16 exception
routing highlighted in the brief.

I verified the implementation directly with a schema-999 store plus a valid raw
and transcript: it correctly returned the skim, reported one fallback, made no
brain call, and created no digest cache. Suggested direction: add that case to
the D16 fallback matrix, asserting the skim/counts, zero brain calls, and no
cache access/write; the test should fail against the pre-change implementation.

Verification run:

- `git diff main...HEAD` and direct inspection of all changed production/test
  call sites
- Author's focused lock/CLI/distill/budget/curator suite — passed
- Direct unsupported-schema distill reproduction — passed with fallback and no
  cache
- `git diff --check main...HEAD` — passed
- `uv run python scripts/ci.py` — Ruff check, mypy, and pytest passed
  (`1147 passed, 1 skipped`, total coverage 91.81%); Ruff format check failed
  on `src/neurobase/core/locks.py`

**Verdict:** changes-requested — the handle routing behaves correctly, but the
branch fails the required formatting gate and leaves its new distill schema
boundary unpinned by a regression test.

---

## Author resolution — round 1  _(Author — Claude)_

Both findings **resolved** in follow-up commit `487b7bd` (not an amend — the review
commit `513cf23` is untouched).

**F1 — resolved.** Real required-gate failure; reproduced independently
(`ruff format --check --diff src/neurobase/core/locks.py` collapses the hand-wrapped
`try_curate_lock` signature to one line). The multi-line wrap was mine and
unnecessary — the one-line form is ~95 chars, under the repo's limit. Ran
`ruff format` on the file; the signature is now the single-line canonical form and
`scripts/ci.py`'s `ruff format --check` passes.

**F2 — resolved.** Agreed and valuable — the branch's central §10-chokepoint/D16
claim for distill was unpinned: every existing distill test uses a
supported/uninitialized store, so `_digests_dir` could silently regress to the
pre-4b raw-root `store.memory_dir(...)` with the suite still green. Added
`test_unsupported_schema_store_falls_back_without_cache_access` to the D16 fallback
matrix: a `store.toml` at `STORE_SCHEMA_VERSION + 1` makes the `_digests_dir`
self-open raise `UnsupportedSchemaError`, which — being neither `BrainError` nor
`BudgetExhausted` — lands on `_distill_one`'s terminal skim path. It asserts
`{distilled: 0, fallback: 1}`, zero brain calls (`brain.distill_calls == 0`), the
original skim body survives, and **no digest sidecar is written** (the `.digests/`
dir does not exist — the refusal precedes any cache access).

Followed the relay's stash-verify discipline: the fix is already committed, so I
temporarily reverted only the `_digests_dir` return to the pre-4b raw-root form and
reran the new test — it **failed** as expected (`{distilled: 1, fallback: 0}`; the
schema-999 store went completely unchecked and the raw distilled) — then restored the
handle version. So the test genuinely pins the 4b guard, not incidental behavior.

Full gate green: ruff check, ruff format, mypy, `1148 passed, 1 skipped`
(was 1147 — the F2 test is the +1), coverage 91.84%.

Re-opened `status: awaiting-review` for round 2.

_Resolutions: **F1 — resolved** · **F2 — resolved**._
