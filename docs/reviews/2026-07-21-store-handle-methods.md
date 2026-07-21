---
slug: store-handle-methods
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-handle-methods
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: StoreHandle method surface — ADR-0015 migration step 2

## Brief  _(Author — Claude)_

**Intent.** **Step 2 of the ADR-0015 migration.** Give `StoreHandle` the store and
registry API as **methods** (`handle.memory_dir(project)`, `handle.write_raw(...)`,
`handle.load_registry()`, `handle.resolve_project(cwd)`, …). Each drops the
`root: Path` argument — the handle already carries the validated root, and holding
the handle is proof the D11 schema guard ran at `open_store()`. This is the API
that step 3 will migrate every caller onto.

**Design decision worth knowing (the maintainer chose this explicitly).** The ADR
end-state wording is "every store/project API *takes* a `StoreHandle`… raw-`Path`
signatures removed." Two functions can't share a name with different signatures, so
a param-based conversion (`store.memory_dir(handle, project)`) would force flipping
`store.py`/`projects.py` signatures **and** all ~21 caller files in one big-bang PR.
We chose **methods on the handle** instead: a distinct namespace, so the root-taking
functions stay untouched and every commit stays green. Methods also match ADR-0012's
own example (`store = open_store(root); store.memory_dir(project)`). Consequence:
step 2 is purely additive; the guard-site porting folds into step 3's per-module
conversion (a module that opens a handle gets the guard for free, so its separate
`ensure_store_metadata` call goes away then).

**Scope.** Branch `adr-0015-handle-methods`, `git diff main...HEAD` (single commit
`29a92d6`). Key files:
- `src/neurobase/core/store_handle.py` — added 15 delegating methods to `StoreHandle`
  (12 store accessors + 3 registry accessors). Also switched the module to
  `store.`-qualified references (`from neurobase.core import projects, store`) so the
  new delegations and the existing `open_store`/`_parse_schema` code read
  consistently — **no behavior change** in `open_store` itself, only the reference
  style.
- `tests/test_store_handle.py` — added a "handle method surface" section (9 tests)
  exercising every method against a real store and asserting each targets the
  handle's root.

**Focus areas.**
1. **Faithful, behavior-preserving delegation.** Each method must forward to the
   right root-taking function with the arguments in the right order and the root
   supplied as `self.root`. Please check the two with many params — `write_raw`
   (keyword-only block) and `upsert_curated` — arg-for-arg. `raw_path`/`write_raw`
   pass `(self.root, project, …)`; `memory_dir`/`ensure_tree` pass `(project,
   self.root)` (that ordering asymmetry mirrors `core/store.py`, not a bug).
2. **No behavior change to `open_store`.** The `store.`-qualification refactor of
   `open_store`/`_parse_schema` should be a pure rename of references. Confirm the
   guard logic is byte-for-byte equivalent to what you approved in step 1.
3. **Import hygiene / no cycles.** `store_handle` now imports `projects` and `store`;
   neither imports `store_handle`. Confirm no import cycle and that `projects` (which
   imports `store`) is safe to import here.
4. **Additive + callerless still holds.** Nothing in `src/` calls these methods yet;
   the root-taking `store.py`/`projects.py` functions are unchanged. Confirm the diff
   touches only `store_handle.py` + its test.

**Known risks / tradeoffs.**
- **No per-method mode enforcement.** A `WRITE`-only method (e.g. `write_raw`) can be
  called on a `READ` handle — the mode governs `open_store`'s create/validate
  behavior, not per-operation permission. Deferred deliberately: it is a separate
  hardening, and with no callers yet it changes nothing. Flag if you think step 2
  should already gate writes by mode.
- **Transitional signature duplication.** Each method's parameter list mirrors its
  root-taking function until step 4 deletes the latter and moves the logic here.
  Intentional for the migration window.

**How to verify.**
- `git diff main...HEAD`
- `uv run pytest tests/test_store_handle.py -q` (34 pass)
- `uv run python scripts/ci.py` — full gate green (1116 passed, 1 skipped;
  `store_handle.py` 100% coverage; ruff/format/mypy pass).

**Out of scope.** Converting any caller onto the methods (step 3); removing the
root-taking `store.py`/`projects.py` functions (step 4); the CI AST check (step 5);
per-method mode enforcement; the schema-2 / profile-resolution work (ADR-0016).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _(pending)_
