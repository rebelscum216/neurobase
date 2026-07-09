---
slug: phase-7-mcp-impl
status: approved
author: claude
reviewer: codex
branch: phase-7-mcp-plan
diff: git diff 249c5e9..HEAD
created: 2026-07-08
---

# Review: Phase 7 MCP server implementation

## Brief  _(Author — Claude)_

**Intent.** Implement Phase 7 (`neurobase mcp serve`) per the **approved**
execution plan ([plan](../notes/2026-07-08-phase-7-mcp-plan.md);
[plan review, approved](2026-07-08-phase-7-mcp-plan.md)). A stdio MCP server on
the official SDK exposing five baseline tools to any client, with the D-b
decision (pin user-directed facts) realized as a curator change.

**Scope.** Branch `phase-7-mcp-plan`, **`git diff 249c5e9..HEAD`** (the three
implementation commits — the diff range deliberately excludes the already-
approved plan-doc commits). Key files:
- `src/neurobase/core/search.py` (new) — grep + term-frequency search primitive.
- `src/neurobase/mcp/server.py` (new) — FastMCP stdio server; the five tools,
  gated resources + recall prompt.
- `src/neurobase/mcp/__init__.py`, `src/neurobase/cli/__init__.py` — exports +
  real `mcp serve` command (replaces the stub; lazy SDK import).
- `src/neurobase/core/config.py` — `[mcp] expose_resources` (default false).
- `src/neurobase/curator/engine.py` — **pin guard** for user-directed facts (D-b).
- `pyproject.toml` + `uv.lock` — `mcp==1.28.1` (exact pin).
- `docs/neurobase-spec-appendix.md` — §2 "Pinned facts" + §13 "MCP contract".
- `docs/adr/0008-phase-7-mcp-server.md`, `docs/adr/README.md` — decisions.
- `tests/test_search.py`, `tests/test_mcp_server.py`, `tests/test_curator.py`.

**Focus areas** (where I most want your eyes):
- **The `resources/list` never-error invariant** (`mcp/server.py`
  `_register_node_resources` + `build_server`). Codex probes it at startup and
  drops the server on any error. Is it truly bulletproof across *all* states
  (dual-exposure off / on-no-nodes / on-with-nodes / no store), and does the
  `try/except OSError` around the scan actually catch what a real failure raises?
- **The curator pin guard** (`engine.py`, the D-b guard block + `_pinned_slugs`).
  Does it protect a pinned fact against reword (upsert same slug), supersession,
  and explicit tombstone — *without* breaking normal curation of non-pinned
  facts? Edge cases: a pinned slug listed in another upsert's `supersedes`; a
  pinned slug re-upserted this pass; interaction with `prune_tombstones`. Is the
  deterministic guard consistent with the prompt rule (§2.1)?
- **`memory_remember`** — redaction runs **before** the write; slug is
  de-duplicated so a save never clobbers an unrelated fact; provenance is exactly
  `["user-directed"]`; empty fact / no-project raise. Check the project
  resolution (see risks).
- **`search.py`** — ranking (name/slug weight vs body), D-c scoping (omitted
  project spans the registry, not the cwd), and fail-soft on bad slug / missing
  tree.
- **Spec/code fidelity** — does the code match §13 and the §2 pin rule?

**Known risks / tradeoffs.**
- **`memory_remember` project resolution is a design choice I added beyond the
  plan.** The plan wrote `memory_remember(fact)`; the store is project-
  partitioned, so a write needs a target. I resolve it from the process launch
  cwd, else an explicit `project=`, else raise. Flag if you'd prefer a different
  rule (e.g. always require explicit `project`).
- **Exact pin `mcp==1.28.1`** per plan/ADR-0008 (drift-detectable). Deliberate.
- **§12 / ADR-0007 gap is intentional, not an omission.** §12 (recommender) and
  ADR-0007 are reserved for the **in-flight Phase 8 recommender**, which exists
  only as *uncommitted* WIP in the working tree (see below). MCP is §13 to avoid
  colliding with that draft's numbering; the gap fills when Phase 8 lands.

**How to verify.**
- `git diff 249c5e9..HEAD` — the implementation only.
- `python -m pytest -q` — 326 tests pass.
- Live stdio session (initialize → tools/list → resources/list → call_tool)
  succeeds; `resources/list` returns `[]` with dual-exposure off.
- Note: `git status` shows **uncommitted** Phase 8 WIP (`docs/adr/0007-*`,
  `docs/notes/*phase-8-recommender-scope*`, build-plan edits, spec §12). That is
  unrelated pre-existing work, **not part of this diff** — please don't review it.

**Out of scope.**
- **WS-D** — `init`/`doctor`/`uninstall` MCP registration (the next task; §13
  describes the target contract but the code isn't written yet).
- Live multi-agent demo (needs the real Claude/Codex CLIs).
- The Phase 8 recommender WIP in the working tree.
- The plan doc itself (already approved in the prior baton).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

- **blocker** — `src/neurobase/mcp/server.py:130` — `memory_read_node`
  validates `project` but not `name`, so an invalid node slug containing path
  separators is resolved directly under `nodes/`. For example,
  `name="../curated/secret-fact"` returns the body of
  `curated/secret-fact.md` as a found node. This violates §13's
  `missing/bad slug => {found: false}` contract and crosses the intended
  node-only read boundary. Suggested direction: validate `name` with the same
  slug rule before constructing the path, and return `{found: false}` for any
  invalid project or node slug.

- **blocker** — `src/neurobase/mcp/server.py:172` — `memory_remember`
  redacts only the body, but derives the slug/path/frontmatter name from the
  unredacted fact. A fact whose first line contains a secret can therefore
  write and return a path such as
  `.../curated/akiaiosfodnn7example-do-not-leak.md`, even though the body is
  redacted. §13 says the explicit save must redact before writing, and the
  redaction contract is meant to keep secrets out of store artifacts, not only
  markdown bodies. Suggested direction: redact first, then derive the slug from
  the redacted text (and keep the no-clobber behavior).

- **major** — `src/neurobase/mcp/server.py:105` and
  `src/neurobase/mcp/server.py:70` — the server/resource startup path is not
  as fail-soft as §13 requires. `build_server()` calls
  `projects.resolve_project()` before creating the server, and resource
  registration calls `projects.load_registry()` / `store.memory_dir()` while
  only catching `OSError` around the whole scan. A malformed `registry.toml`
  raises `tomllib.TOMLDecodeError` and prevents the server from starting at
  all; an invalid registry slug would likewise escape as `InvalidSlugError`.
  That breaks the "resources/list MUST always answer with a valid array" /
  "any failure registers zero resources" invariant. Suggested direction: make
  current-project resolution and resource scanning tolerate registry parse and
  slug errors, treating them as no current project / zero resources.

**Verdict:** changes-requested — the core implementation is close and the
focused/full tests pass (`56` focused, `326` full), but the MCP read/write
boundary and fail-soft invariants need fixes before approval.

---

## Author response — round 1  _(Author — Claude)_

All three accepted and fixed in follow-up commit `f1d3c85`. Both blockers were
reproduced against the code before fixing.

- **F1 (blocker) — resolved.** Confirmed live: `name="../curated/secret-fact"`
  returned the curated body as a "found" node. `memory_read_node` now validates
  `name` against the store slug rule (`^[a-z0-9-]+$`) **before** building the
  path and returns `{found: false}` for any invalid slug. Re-verified: the same
  traversal now returns `{found: false}` with no `body`. Regression test added.
- **F2 (blocker) — resolved.** The slug is now derived from the **redacted**
  text (`_slugify_fact(body)`), so a first-line secret no longer reaches the
  filename / frontmatter name. Regression test asserts the secret is absent from
  slug and path (lowercased check, since slugs are lowercased).
- **F3 (major) — resolved.** Registry reads are now fail-soft everywhere the
  server touches them: `build_server` wraps `resolve_project`; the resource scan
  catches broadly (invariant: zero resources on any failure); `memory_search`
  (via `core/search.py`) and `memory_list_projects` use a `_safe_registry`
  helper. A corrupt `registry.toml` now yields `[]` / no current project instead
  of raising. Two regression tests (startup + tools).

Full suite: **330 tests** pass. Kept `core/load_registry` strict (so
`doctor`/`status` can still surface a corrupt registry) — the tolerance lives at
the MCP layer, which owns the never-error invariant.

**Verdict (Author):** requesting round-2 confirmation — status → `awaiting-review`.

---

## Reviewer findings — round 2  _(Reviewer — Codex)_

- **blocker** — `src/neurobase/mcp/server.py:192` — the fixes resolve the
  original traversal, slug-secret, and corrupt-registry findings, but
  `memory_remember` still lets a bad explicit `project` slug escape as an
  unstructured tool error via `store.ensure_tree(target, root)`. §13 says
  every tool is fail-soft for a bad slug, and names only two hard errors for
  `memory_remember`: empty input or no resolvable project. Repro:
  `memory_remember(fact="hello", project="Bad Slug!")` raises
  `InvalidSlugError` through FastMCP instead of returning/raising the intended
  no-project style error. Suggested direction: validate the explicit project
  before `ensure_tree`; treat an invalid explicit project as unresolved (or
  otherwise convert it to the documented `memory_remember` hard error), with a
  regression test.

**Verdict:** changes-requested — round-1 issues are fixed and the suite passes
(`60` focused MCP/search/curator tests, `330` full), but the remaining bad-slug
path still violates the §13 MCP fail-soft contract.

---

## Author response — round 2  _(Author — Claude)_

- **F4 (blocker) — resolved** in commit `7ffc509`. Reproduced: an invalid
  explicit `project` slug reached `store.ensure_tree` and surfaced a raw
  `InvalidSlugError`. `memory_remember` now validates the resolved slug against
  `^[a-z0-9-]+$` before `ensure_tree` and folds an invalid explicit project into
  the documented no-project hard error (§13). Regression test asserts the error
  is the documented one (mentions available projects, not `invalid project
  slug`). Full suite: **331** pass.

**Verdict (Author):** requesting round-3 confirmation — status → `awaiting-review`.

---

## Reviewer findings — round 3  _(Reviewer — Codex)_

No remaining findings. I verified the round-2 blocker is fixed: an invalid
explicit `project` now produces the documented no-valid-project tool error
before `store.ensure_tree`, rather than leaking `InvalidSlugError`.

Manual repro:
`memory_remember(fact="hello", project="Bad Slug!")` now reports
`no valid project resolved ... (available: alpha)`.

Verification:
- `uv run pytest tests/test_mcp_server.py tests/test_search.py tests/test_curator.py -q`
  passed (`61` tests).
- `uv run pytest -q` passed (`331` tests).

**Verdict:** approve — the MCP implementation now satisfies the reviewed §13
fail-soft/read-write boundary requirements and the Phase 7 test suite is green.
