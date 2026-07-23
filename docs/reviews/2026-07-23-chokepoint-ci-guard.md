---
slug: chokepoint-ci-guard
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-chokepoint-ci-guard
diff: git diff main...HEAD
created: 2026-07-23
---

# Review: the ADR-0015 step-5 store-chokepoint CI guard — 4c

## Brief  _(Author — Claude)_

**Intent.** Close out ADR-0015. Steps 3/4a/4b converted **every** production caller
onto `open_store(...)` + `StoreHandle` methods — a census this session confirms
production has exactly **one** remaining raw-root store/registry call: `doctor`'s
corrupt-`store.toml` fallback (`cli/diagnostics.py`). So 4c is no longer "convert
callers"; it is **landing the enforcement that keeps them converted** (the ADR's
step 5) + resolving the doctor fallback.

**Two maintainer-directed decisions shaped the scope** (asked and answered before
implementing — please review the implementation against them, not relitigate them):

1. **Doctor fallback → keep the registry resolver root-taking.** `registry.toml`
   parseability is a *separate* concern from the store-schema chokepoint (ADR-0015's
   own F1 carve-out): resolving a project when `store.toml` is corrupt and no handle
   can open is legitimate. So `projects.resolve_project(root, cwd)` (and
   `store.store_toml_path(root)` for the report label) stay, called only from
   `diagnostics.py`, and are **allow-listed by (file, name)** in the guard.
2. **Fold now, defer literal removal.** The ADR's "remove the raw-`Path` signatures"
   would force a **~341-site test-helper rewrite** (`store.memory_dir(p, root)` →
   `handle.memory_dir(p)` across the suite) for no production-safety gain — production
   is already clean. Instead the raw-root functions **stay** as the low-level
   implementation the handle methods delegate to (and the tests' store-setup helpers),
   and the **CI guard, scoped to `src/`,** forbids production from reaching around the
   chokepoint. This is slightly weaker than the ADR's literal "remove not deprecate"
   (noted in *Known risks*); the guard is what makes production omission impossible in
   the meantime.

**Scope.** Branch `adr-0015-chokepoint-ci-guard`, `git diff main...HEAD` (commit
`cbfc418`). Files:

- `scripts/check_store_chokepoint.py` **(new)** — the AST guard. Walks
  `src/neurobase/**`, exempts `core/store.py`, `core/store_handle.py`,
  `core/projects.py`. Flags: calls to the raw-root store path-builders / tree ops
  (`memory_dir`, `ensure_tree`, `ensure_store_metadata`, `store_toml_path`, `raw_path`,
  `write_raw`, `list_raw`, `mark_consumed`, `upsert_curated`, `list_curated`,
  `soft_delete_curated`, `prune_tombstones`, `write_node`, `rebuild_index`) and the
  registry accessors (`load_registry`, `register_project`, `resolve_project`), whether
  reached as `store.x`/`projects.x` or directly imported; plus the
  `"store.toml"`/`"registry.toml"` filename literals. `check_source(relpath, source)`
  is the testable seam.
- `scripts/ci.py` — new first-class `store-chokepoint` gate (after mypy, before pytest).
- `src/neurobase/cli/diagnostics.py` — `_project_check` docstring + call-site comment
  documenting the one sanctioned raw-root fallback and its allow-list. **No behavior
  change** — the fallback logic is unchanged.
- `tests/test_store_chokepoint_check.py` **(new)** — 13 tests over the real guard.
- `docs/known-gaps.md` — **G1 → fixed** (kept fixed by this guard); Resolution added.
- `docs/neurobase-spec-appendix.md` — §10 gains the `open_store` chokepoint + mode
  table, the `uninstall --purge-store` exemption (D25, which the ADR says *must* be
  written into §10), and the enforcement rule. This is the ADR's "spec appendix is the
  law" fold-in, deferred through steps 1–4b and done here as ADR-0015 concludes.

**Focus areas.**

1. **False-positive design (the crux).** The guard keys on **function calls +
   filename literals**, deliberately **not** path fragments (`/ "memory"`,
   `/ "nodes"`, `/ "projects"`). This is what keeps two legitimate patterns clean:
   (a) appending a subdir to a *handle-derived* path
   (`handle.memory_dir(p) / "nodes"`, pervasive), and (b) `recommender/seed.py:76`'s
   `~/.claude/projects/<x>/memory` — the *Claude app's* store, a different filesystem
   whose `projects`/`memory` fragments are coincidental. Both are pinned by tests.
   Confirm the keying is right and I haven't left a real hand-rolled store-path
   construction form unmatched (e.g. a bare-root path build that uses neither a
   forbidden call nor the filename literals).
2. **The allow-list can't leak.** It is `(relpath, name)` — `resolve_project` from
   `cli/__init__.py` is still a violation; only `cli/diagnostics.py` is exempt for the
   two doctor calls. `test_allowlist_is_scoped_to_diagnostics` pins this. Confirm the
   exception is exactly the sanctioned doctor fallback and nothing wider.
3. **The guard enforces a *true* invariant.** `test_current_src_tree_has_no_violations`
   asserts the whole shipped tree passes — so the guard isn't aspirational. I also
   end-to-end verified the *script* (not just `check_source`) fails CI on a live
   production violation (a scratch `store.memory_dir(p, root)` under `src/` → exit 1),
   then removed it. Confirm the guard would actually block a regression, not just pass.
4. **Spec §10 accuracy.** The new §10 subsection is the law for the shipped handle —
   confirm the mode table (READ/WRITE/DOCTOR/MIGRATE/PURGE) and the D24/D25/D26 +
   registry-fail-soft statements match the actual `store_handle.py` behavior.

**Known risks / tradeoffs.**

- **Deferred removal is weaker than the ADR's literal "remove not deprecate."** The
  raw-root `store.py`/`projects.py` signatures still exist (tests + internal use). A
  *test* could still call them (intended); a future *production* bypass is caught by
  the guard, not the type system. This was the maintainer's explicit scoping call to
  avoid a 341-site churn with no production-safety gain. If you think the residual risk
  warrants the removal anyway, say so — but it's a large, separate change.
- **The guard is name/literal-based, so it is defeatable by obfuscation**
  (`getattr(store, "memory_dir")`, aliasing through a third module, `globals()`
  tricks). It is a guardrail against the *accidental* re-introduction G1 was about, not
  a sandbox against adversarial code. Acceptable for its purpose; flag if you disagree.
- **`mark_consumed` is in the forbidden set** though it takes a *path*, not a root —
  because the within-store guard (`_require_within_store`, the step-2 F1 confused-deputy
  fix) lives on `handle.mark_consumed`, so production must go through the handle. No
  production caller uses `store.mark_consumed` today; confirm that's the right call.

**How to verify.**

- `git diff main...HEAD`
- `uv run python scripts/check_store_chokepoint.py` — prints OK, exit 0.
- `uv run pytest tests/test_store_chokepoint_check.py -q` — 13 pass.
- Regression proof: add `store.memory_dir(p, root)` to any `src/neurobase/*.py`, rerun
  the guard → exit 1 naming the file:line; remove it → OK.
- `uv run python scripts/ci.py` — full gate green incl. the new `store-chokepoint`
  check; `1161 passed, 1 skipped`, coverage 91.84%.

**Out of scope.** The literal removal of the raw-`Path` `store.py`/`projects.py`
signatures (deferred by decision, above). No production store/registry *behavior*
changes here — this is a CI guard + docs + one documented-but-unchanged doctor
fallback. ADR-0015 is otherwise complete (steps 1–4b landed on `main`).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->
