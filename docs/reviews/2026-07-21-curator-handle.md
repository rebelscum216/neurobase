---
slug: curator-handle
status: approved
author: claude
reviewer: codex
branch: adr-0015-curator-handle
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: convert curator/engine.py to the store handle — ADR-0015 step 3.3

## Brief  _(Author — Claude)_

**Intent.** Continue ADR-0015 step 3 with the heaviest caller so far: convert the
curator engine (the spec §2 loop, ~17 store calls) onto the `StoreHandle`. Same
per-module pattern — keep the public signatures, open a handle at each entry point,
use handle methods internally, drop the module's own guard call.

**Scope.** Branch `adr-0015-curator-handle`, `git diff main...HEAD` (single commit
`2a01582`). One file: `src/neurobase/curator/engine.py`. Three entry points + four
private helpers converted:
- `curate()` — opens **one WRITE handle** at entry (it always mutates). This runs the
  D11 guard exactly as the old `store.ensure_tree(project, root)` →
  `ensure_store_metadata` did (newer-schema store raises); then
  `handle.ensure_tree(project)` creates the subdirs. All internal store access
  (`list_raw`, `list_curated`, `upsert_curated`, `soft_delete_curated`,
  `mark_consumed`, `prune_tombstones`, `write_node`, `rebuild_index`, `memory_dir`)
  goes through the handle.
- `is_stale()`, `read_fact_count_trend()` — open **READ handles**.
- Helpers `_safe_soft_delete`, `_apply_upserts`, `_synthesize`, `_log_pass` now take
  `handle: StoreHandle` instead of `root: Path`.

**Focus areas.**
1. **`curate` faithfulness.** The critical property is that the WRITE handle at entry
   preserves every guarantee: the D9 abort-and-leave-unconsumed path, the D22 batch
   semantics (later failed batch + all later raws stay unconsumed; earlier committed
   batches stand and still prune/re-synthesize), the pinned-fact guard, and the
   partial/error/ok status logic. None of that logic changed — only how the store is
   reached. Please confirm the `handle.ensure_tree(project)` at entry is equivalent to
   the old `store.ensure_tree(project, root)` (it is: `handle.ensure_tree` delegates to
   exactly that, and `open_store(WRITE)` already ensured `store.toml`, so the guard
   runs once at the boundary + idempotently in `ensure_tree`).
2. **`mark_consumed` is now `handle.mark_consumed(doc.file_path)`.** Each `doc` comes
   from `handle.list_raw(project)`, so `doc.file_path` is inside the handle's store —
   it passes the `_require_within_store` boundary check added in the step-2 review
   (F1). Worth confirming the raw paths flow from the same handle.
3. **The deferred edges.** `distill_mod.distill_docs(root, project, ...)` still takes
   `root` (its own later step-3 item — 11 test callers pass root, and it has a single
   `memory_dir` call), and `linkify.linkify(handle.root, project)` passes the handle's
   root (linkify not yet converted). Both are intentional passthroughs, flagged for the
   follow-ups.
4. **Behavior change on the two read helpers.** `is_stale`/`read_fact_count_trend` now
   validate the schema (READ handle) and thus **fail closed on a newer-schema store**
   instead of reading blindly. This is unreachable via the CLI (both callers run
   `_check_store_schema` first) and untouched by tests (schema-1 stores). I judged this
   an acceptable, correct-direction change rather than something to suppress — flag if
   you'd rather they stay non-guarding until the CLI conversion.

**Known risks / tradeoffs.**
- No new tests: this is a behavior-preserving internal refactor with strong existing
  coverage (`test_curator`, `test_curate_budget`, `test_distill`, `test_cross_agent`,
  `test_curate_to_recommend` — all pass, coverage floor held). The one behavior delta
  (focus area 4) is an unreachable-in-practice edge; say if you want it pinned.
- Two idempotent schema checks in `curate` (the WRITE `open_store` + `ensure_tree`'s
  internal `ensure_store_metadata`). Negligible; both are no-ops when `store.toml`
  already exists.

**How to verify.**
- `git diff main...HEAD`
- `uv run pytest tests/test_curator.py tests/test_curate_budget.py tests/test_distill.py tests/test_cross_agent.py tests/test_curate_to_recommend.py -q` (all pass)
- `uv run python scripts/ci.py` — full gate green (ruff/format/mypy/pytest + coverage floor).

**Out of scope.** `curator/distill.py` and `core/linkify.py` (their own follow-ups);
MCP/D24, recommender, CLI/doctor D26 (later step-3 modules); removing the root-taking
`store.py`/`projects.py` functions (step 4); the CI AST check (step 5). `store.read_doc`/
`write_doc` are format primitives, intentionally not on the handle.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

No findings. I reviewed `git diff main...HEAD` against the prior
`curator/engine.py` control flow and the ADR-0015 handle boundary. The store
access refactor preserves the existing D9/D22 consume/error/partial semantics:
raws still come from the same `handle.list_raw(project)` path that later feeds
`handle.mark_consumed(doc.file_path)`, distillation keeps `file_path` when it
substitutes bodies, and the deferred `distill_docs(root, ...)` /
`linkify.linkify(handle.root, ...)` edges are scoped follow-ups rather than
accidental unguarded store mutations in this patch.

Verification:
- `uv run pytest tests/test_curator.py tests/test_curate_budget.py tests/test_distill.py tests/test_cross_agent.py tests/test_curate_to_recommend.py -q`
- `uv run python scripts/ci.py` (ruff, format check, mypy, pytest + coverage:
  1122 passed, 1 skipped)

**Verdict:** approve
