---
slug: recall-handle
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-recall-handle
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: convert recall_common to the store handle — ADR-0015 step 3.1

## Brief  _(Author — Claude)_

**Intent.** **First caller conversion of ADR-0015 migration step 3.** Convert
`adapters/recall_common.py` (the shared SessionStart recall/inject path, used by
both the Claude and Codex adapters and by `mcp serve`) to obtain a `StoreHandle`
instead of calling root-taking store/registry functions. This establishes the
per-module conversion pattern the rest of step 3 will follow: **open one handle at
the module's entry point, use handle methods internally, and delete the module's
own `ensure_store_metadata` guard call** — because the D11 guard now runs at
`open_store()`.

**Scope.** Branch `adr-0015-recall-handle`, `git diff main...HEAD` (single commit
`347646f`). Key files:
- `src/neurobase/adapters/recall_common.py` — `build_context(root, cwd)` now opens
  a `READ` handle up front and uses `handle.resolve_project(cwd)` /
  `handle.memory_dir(project)`; `_node_bodies` takes the handle. The
  `store.ensure_store_metadata(root)` guard call is removed (folded into
  `open_store`). `store.read_doc(path)` stays as-is (a pure file parser, not a
  root-scoped store op). The module's **public signatures are unchanged**
  (`build_context(root, cwd)`, `emit(root, cwd)`, `spawn_curate_if_stale(root, cwd)`),
  so its callers — `mcp/server.py`, both adapters' `recall` re-exports — are
  untouched.
- `tests/test_recall_common.py` — added `test_read_recall_does_not_create_store_toml`.

**Focus areas.**
1. **Behavior preservation of the guard.** The old code resolved the project
   *first*, then guarded; the new code opens the `READ` handle (guard) *first*,
   then resolves. Please confirm every case lands the same: untracked cwd → `None`;
   newer-schema store → fails closed (`None`, injects nothing); uninitialized store
   → `None`; tracked project with nodes → same context, same `[inject].max_chars`
   cap. My reasoning: all four still return the same value; the only observable
   delta is the intentional one below.
2. **The one intentional behavior change.** `READ` never writes, so recall no
   longer creates `store.toml` as a side effect (the old `ensure_store_metadata`
   would create it on first call). This only matters for the odd state
   "project registered but store uninitialized," and not-writing on a read path is
   the correct, ADR-0015-intended behavior. Pinned by the new regression test.
3. **Fail-safe still holds.** `emit()` wraps `build_context` in a blanket
   `except Exception → None`; a corrupt `registry.toml` still surfaces via
   `handle.resolve_project` → propagates → `emit` returns `None`, exactly as before
   (`open_store` validates `store.toml` only, never the registry — ADR-0015 F1).
4. **`_node_bodies` signature change is safe.** It is only called internally
   (one call site); re-exported in `claude/recall.py`'s `__all__` but nothing
   external invokes it.

**Known risks / tradeoffs.**
- The guard/resolve **reordering** (focus area 1) is the only structural change;
  I believe it is behavior-equivalent but it is the thing most worth a careful
  read.
- `open_store` now runs on every recall including untracked cwds (one extra
  `store.toml` stat/parse before `resolve_project`). Negligible; recall already
  did a `store.toml` read via the old guard for tracked projects.

**How to verify.**
- `git diff main...HEAD`
- `uv run pytest tests/test_recall_common.py tests/test_claude_recall.py tests/test_hook_schema_guard.py tests/test_cross_agent.py -q` (all pass)
- `uv run python scripts/ci.py` — full gate green (ruff/format/mypy/pytest + coverage floor).

**Out of scope.** Converting other caller modules (the rest of step 3 — curator,
scribes, MCP, recommender, CLI); removing the root-taking `store.py`/`projects.py`
functions (step 4); the CI AST check (step 5). `store.read_doc`/`write_doc` are
format primitives and are intentionally *not* moved onto the handle.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _(pending)_
