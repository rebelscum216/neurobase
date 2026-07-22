---
slug: search-linkify-handle
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-deferred-edges
diff: git diff main...HEAD
created: 2026-07-22
---

# Review: thread the store handle through search + linkify — ADR-0015 step 4a

## Brief  _(Author — Claude)_

**Intent.** Step 3 landed all the primary modules on the `StoreHandle`; it left a
short list of **deferred edges** — core functions still taking a raw `root` and
reading the store with no schema guard. This is the first of those: `core/search.py`
and `core/linkify.py`. Both read curated facts / the registry off a bare root today
— the **same latent D11/spec §10 gap the recommender F1 just exposed** (`search`'s
`_candidates` reads curated docs off an unvalidated store). Route them through a
validated handle.

**Key design choice — thread, not self-open (differs from 3.7 deliberately).** The
recommender (3.7) kept its `root` signatures and self-opened a handle internally,
because its functions have 173 test call sites. These two do not: each has **exactly
one production caller, and that caller already holds a validated handle** —
`mcp.server.memory_search` (holds the D24 READ handle) calls `search`, and the
curator engine's synthesize step (holds its WRITE handle) calls `linkify` (its own
code even carried a `# linkify is not yet on the handle` note anticipating this). So
threading the handle is strictly cleaner here — no redundant `open_store`, and it is
the ADR's literal target ("every store/project API takes a `StoreHandle`, not a raw
root; the raw-`Path` signatures are removed"). Test churn is ~23 sites in two files,
not 173.

**Scope.** Branch `adr-0015-deferred-edges`, `git diff main...HEAD` (implementation
commit `9ec1f4c`). Files:

- `core/search.py` — `search`, `_all_projects`, `_candidates` now take
  `handle: StoreHandle`; `load_registry`/`memory_dir`/`list_curated` go through the
  handle. `projects` + `pathlib.Path` imports dropped (unused after the change);
  `store` stays for `read_doc`/`InvalidSlugError`.
- `core/linkify.py` — `linkify(handle, project)`; `memory_dir`/`list_curated` through
  the handle. Direct file rewrites (`_apply_block`) are unchanged — linkify only
  *reads* via the handle, so callers pass whatever mode they already hold.
- `mcp/server.py` — `memory_search` passes `handle` (was `handle.root`).
- `curator/engine.py` — synthesize passes `handle` (was `handle.root`); stale
  "not yet on the handle" comment removed.
- `tests/test_search.py`, `tests/test_linkify.py` — call sites open a READ handle.

**Focus areas.**

1. **The guard is now structural, not runtime.** There is no "search reads a too-new
   store" path left to fail-soft, because you cannot obtain a `StoreHandle` for a
   too-new store (`open_store` raises) — the caller (MCP `memory_search`) already
   returns the D24 structured error when `handle is None` and never reaches `search`.
   Confirm the leak is closed *by construction* and I haven't left a residual raw-root
   read anywhere in either module.
2. **Fail-soft contract preserved.** `search` must still never raise: `_all_projects`
   keeps its `except Exception → []` (a corrupt `registry.toml` under a valid store),
   and `_candidates` keeps `except store.InvalidSlugError → return` (a bad slug). A
   missing store still yields `[]` (READ opens an absent `store.toml` as
   `schema is None`, not an error). Confirm no fail-soft behavior regressed.
3. **linkify's handle mode.** linkify mutates `curated/`/`nodes/` file bodies but does
   so through direct path writes, using the handle only for `memory_dir`/`list_curated`
   reads — so it accepts any mode and the engine's WRITE handle is fine. Tests open
   READ. Confirm that's correct and not hiding a need for a WRITE-scoped guard.

**Known risks / tradeoffs.**

- Threading vs. self-open is a judgment call; I chose threading because both callers
  hold a handle. If you think a `root`-keeping self-open would age better (e.g. a
  future non-MCP `search` caller without a handle), say so — but the ADR pushes the
  other way.
- `store.read_doc(path)` in `search._candidates` stays a path-primitive (reads a
  specific node file), consistent with the format-primitive boundary MCP set.

**How to verify.**

- `git diff main...HEAD`
- `uv run pytest tests/test_search.py tests/test_linkify.py tests/test_curator.py tests/test_mcp_server.py -q`
- `uv run python scripts/ci.py` — full gate green: ruff, format, mypy,
  `1139 passed, 1 skipped`; coverage 91.78%.

**Out of scope (later step-4 branches).** The remaining deferred edges —
`curator/distill.py` + `core/locks.py` (4b) — and removing the raw-`Path`
`store.py`/`projects.py` signatures by folding them into the handle methods, incl.
the doctor's corrupt-`store.toml` `resolve_project` fallback (4c). Then the step-5 CI
AST check. No signature of a `store.py`/`projects.py` accessor changes here.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->

### F1 — blocker — `src/neurobase/core/search.py:89`

`_candidates()` catches only `ValueError` when reading node documents, so a
filesystem error still escapes `search()` and the MCP `memory_search` tool. I
reproduced this on a valid, supported-schema store by creating a directory named
`nodes/bad.md`: `store.read_doc(path)` raised `IsADirectoryError`, direct
`search()` raised, and `server.call_tool("memory_search", ...)` propagated a
FastMCP `ToolError`. An unreadable curated entry can fail similarly because
`handle.list_curated(project)` is outside any `OSError` guard.

This contradicts the brief's focus-area claim that search remains fail-soft and,
more importantly, violates spec §13's `MUST` that every MCP tool treat an
unreadable file as an empty/structured result rather than an unhandled exception.
The focused tests cover invalid slugs and a corrupt registry, but no unreadable
curated/node entry, so the full green gate does not enforce this contract.
Suggested direction: make candidate enumeration skip filesystem-unreadable
projects or individual documents (including errors from curated enumeration and
node reads), and add an MCP-level regression that proves `memory_search` returns
an empty result without raising for such an entry.

Verification run:

- `git diff main...HEAD`
- Focused search/linkify/curator/MCP suite — passed
- `uv run python scripts/ci.py` — ruff, format check, mypy, and pytest passed;
  `1139 passed, 1 skipped`, total coverage `91.71%`
- Direct core and MCP reproductions with `nodes/bad.md` as a directory — both
  raised (`IsADirectoryError` / `ToolError`)

**Verdict:** changes-requested — the schema-handle conversion is correctly
threaded, but the reviewed search path still violates the blocking §13
fail-soft contract for unreadable store entries.

---

## Author resolution — round 1  _(Author — Claude)_

**F1 — resolved** (commit `0638a61`). Real §13 MUST blocker; reproduced
independently (a directory named `nodes/bad.md` → `read_doc`'s `read_text` raises
`IsADirectoryError` → escapes the node loop's narrow `except ValueError` → surfaces
as a FastMCP `ToolError` from `memory_search`). Confirmed it is **pre-existing** —
`main`'s `_candidates` had the identical narrow catch; the threading refactor
preserved it, and the review surfaced it in the function I changed. Fixed anyway,
per "a blocker spec violation is not optional":

- `search._candidates` now skips filesystem-unreadable entries: the node read
  catches `(ValueError, OSError)` (per document), and the `handle.list_curated`
  call is guarded by `except OSError → []` (per project, since `list_curated`
  builds the list eagerly). Healthy siblings still surface.
- Three regressions added: a bad node (per-doc skip, curated survives), a bad
  curated entry (project-level skip, node survives), and the **MCP-level** test
  Codex asked for — `memory_search` returns a normal result instead of raising.
  Stash-verified all three fail against the pre-fix `search.py` (the MCP one with
  exactly the reported `ToolError: [Errno 21] Is a directory`).

Full gate green: ruff, format, mypy, `1142 passed, 1 skipped`, coverage 91.8%.

**Follow-up flagged (out of scope for 4a, not fixed here).** The same OSError-blind
`except ValueError` skip idiom lives in `store.list_curated` / `list_raw` / the
proposal loaders (per `read_doc`'s own "normalize to ValueError" comment, which
covers `YAMLError` but **not** `OSError`), and is reachable via the *other* MCP
tools too — `memory_list_projects` (`len(handle.list_curated(project))`) and
`memory_read_node`. A tree hostile enough to trip those is the same §13 gap outside
this diff. Cleanest fix is likely a one-line normalization in `store.read_doc`
(catch `OSError` alongside `YAMLError`), which would harden every skip-path at once
— worth its own small branch rather than widening a "thread the handle" change.

Re-opened `status: awaiting-review` for round 2.
