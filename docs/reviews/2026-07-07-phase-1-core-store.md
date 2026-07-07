---
slug: phase-1-core-store
status: awaiting-review
author: claude
reviewer: codex
branch: phase-1-core-store
diff: git diff main...phase-1-core-store
created: 2026-07-07
---

# Review: Phase 1 — core store, config, projects

## Brief  _(Author — Claude)_

**Intent.** Implement the storage contract (spec §1), the config module
(spec §10/§8), and the project registry (spec §10/D6) — Phase 1 per
build-plan §6: "the storage contract, portable." This is the substrate every
later phase (curator, brain, adapters, recommender) writes to and reads from.

**Scope.** Branch `phase-1-core-store` (on `main`@`22c9e4c`), `git diff
main...phase-1-core-store`. Key files:
- `src/neurobase/core/store.py` — tree (`raw/curated/nodes/.tombstones` +
  `index.md`), YAML-frontmatter document format (atomic tmp+rename writes),
  root resolution (explicit > `NEUROBASE_ROOT` env > config > `~/neurobase`),
  raw capture write/list/mark-consumed (including the Codex per-turn
  overwrite trick and the "rewritable until consumed" mutability rule),
  curated upsert (provenance merge, supersession), tombstone soft-delete +
  grace-period pruning, node regeneration (wholesale, never appended),
  `index.md` rebuild as a pure function of on-disk state.
- `src/neurobase/core/projects.py` — `registry.toml` read/write, `slugify`,
  git-common-dir resolution (worktrees collapse to one project),
  longest-prefix matching, collision handling on auto-derived slugs.
- `src/neurobase/core/redact.py` — the full D13 regex table, applied in
  contract order.
- `src/neurobase/core/config.py` — `config.toml` loading with typed defaults
  for every spec §10 key (§8 tuned values); Neurobase never writes this file.
- `src/neurobase/cli/__init__.py` — live `enable`/`status` commands,
  replacing their Phase-0 stubs. Both take hidden `--cwd`/`--root` overrides
  for testability, mirroring the existing hook `--cwd`/`--transcript` pattern
  from spec §4.
- `tests/test_{store,projects,redact,config,cli_phase1}.py` — 52 new tests.
- `pyproject.toml`/`uv.lock` — new deps `pyyaml` (frontmatter), `tomli-w`
  (registry writes; reads use stdlib `tomllib`), `types-pyyaml` (dev).

**Focus areas.**
- The raw-capture mutability rule (`write_raw` / `RawConsumedError`) is the
  trickiest invariant in spec §1 — does the implementation actually match
  "rewritable by the owning scribe until consumed; from then on immutable
  except that flip; if already consumed, write a fresh capture instead of
  raising a scary error to a caller that can't handle it"? I chose to raise
  (`RawConsumedError`) rather than silently pick a new `captured_at` myself,
  since only the caller (future scribe) knows what "now" should mean and
  whether a retry is appropriate — is that the right layering, or should
  `store.py` handle the retry internally?
- `index.md` rebuild lives in `store.py`, scanning `nodes/` + `curated/`
  directly, rather than being handed pre-computed data by a future
  `curator.py`. Reasonable now (curator doesn't exist yet), but worth
  flagging in case Phase 3 wants a different seam.
- Git worktree resolution shells out to `git rev-parse --git-common-dir`
  (spec's own recommendation) — is the fallback behavior (any non-zero exit,
  including "no git" or "git not installed", quietly falls back to treating
  cwd as the project root) too lenient, or exactly the fail-open behavior
  spec §1 wants?
- Slug collision handling (`ProjectSlugCollisionError`) only fires for
  auto-derived slugs, never for an explicitly-passed one — is silently
  allowing an explicit slug to jump into an existing entry's root list the
  right call, or should the CLI surface a confirmation either way?

**Known risks / tradeoffs.**
- `write_doc`'s atomic write is temp-file-in-same-directory + `Path.replace`
  — atomic on a single filesystem (POSIX rename, Windows `MoveFileEx` via
  `os.replace`), not across filesystems/mounts. Store root is assumed local;
  not tested against network mounts.
- `resolve_project`'s longest-prefix match is a plain string-length compare
  over `Path` objects converted with `str()` — works for the tested cases
  (including nested projects) but hasn't been stress-tested against
  symlink-heavy or case-insensitive-filesystem edge cases.
- `prune_tombstones`/`list_raw` walk directories with `Path.glob("*.md")` —
  fine at the scale this tool operates at (a dev's own project memory), no
  attempt at pagination or streaming for very large stores.
- Redaction table is implemented as literally-transcribed regexes from
  spec §10; no fuzzing beyond the one-example-per-rule tests in
  `test_redact.py`.

**How to verify.** `uv sync && uv run pytest && uv run ruff check . && uv run
ruff format --check . && uv run mypy src tests`. Manually: `uv tool install .
--force`, then in a scratch git repo, `neurobase enable` followed by
`neurobase status` — should show the project registered and zero raw/facts;
hand-write a `raw/*.md` file matching spec §1's frontmatter shape and re-run
`status` to see the count change (the build-plan's Phase 1 demo).

**Out of scope.** `curator/engine.py`, `brain/*`, adapters, recommender, MCP
— all still Phase 2+ stubs, untouched. `neurobase doctor`/`curate`/`recall`
etc. remain honest stubs. No fixture tests from spec §11 yet (those are
scribe/curator-facing, land with their owning phases).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

- **blocker** — `src/neurobase/core/store.py:71`: Project slugs are never
  validated on the store entry points (`ensure_tree`, `memory_dir`, and the
  raw/curated/node helpers that build paths through them), even though spec §1
  says project/fact/node slugs **MUST** match `^[a-z0-9-]+$` and be rejected
  otherwise. This is reachable today via direct store calls and via registry
  data: `register_project` can also produce an empty slug when the supplied or
  derived name slugifies to `""`, and then `enable` will create
  `<root>/projects/memory/...` instead of a valid
  `<root>/projects/<project>/memory/...` tree. Suggested direction: centralize
  project-slug validation in the store path boundary and have
  `register_project` reject an empty/invalid final slug, with tests for both
  explicit and auto-derived cases.
  - **resolution:** resolved — confirmed empirically first (`Path("/root") /
    "projects" / "" / "memory"` really does collapse to
    `/root/projects/memory`, exactly as flagged). Centralized validation in
    `memory_dir` itself (moved `_require_slug` above it and call it there) so
    every store entry point that builds a path through `memory_dir` is
    covered in one place, not each call site individually. `register_project`
    now validates the final slug (explicit or derived) against `SLUG_RE` and
    raises `InvalidSlugError` if it's empty/invalid; the CLI's `enable` now
    catches that alongside `ProjectSlugCollisionError`. Added regression
    tests: `test_memory_dir_rejects_invalid_project_slug` (parametrized,
    includes the empty-string case) and
    `test_register_project_rejects_empty_{derived,explicit}_slug`.

- **blocker** — `src/neurobase/core/store.py:244`: `soft_delete_curated`
  accepts the fact slug without calling `_require_slug`, while
  `upsert_curated` and `write_node` do validate their slugs. That violates the
  same spec §1 MUST and is especially risky for the Phase 3 curator path, where
  tombstone targets may originate from model output; a bad slug should be
  rejected/skipped, not used to construct a filesystem path. Suggested
  direction: validate `slug` in `soft_delete_curated` (and add a regression
  test for an invalid tombstone slug).
  - **resolution:** resolved — added the missing `_require_slug(slug, "fact
    slug")` call at the top of `soft_delete_curated`, matching
    `upsert_curated`/`write_node`. Added
    `test_soft_delete_curated_rejects_invalid_slug`.

Verification run (Author, post-fix): `uv run ruff check .`, `ruff format
--check .`, `mypy src tests`, `pytest -q` all green (64 passed, up from 57 —
7 new regression tests for these two findings, including a parametrized
one covering 4 invalid-slug cases).

**Author's response to verdict:** both blockers were real gaps, not disputed
— fixed as described above. Re-relaying for re-review.

**Verdict:** changes-requested — blocking spec §1 slug-validation MUSTs are not
fully enforced yet. _(Awaiting re-review.)_

### Re-review — 2026-07-07

Prior blocker verification:
- `memory_dir()` now validates the project slug before constructing
  `<root>/projects/<project>/memory`, so `ensure_tree` and every store helper
  that routes through `memory_dir` reject invalid/empty project slugs.
  Regression coverage exists in `test_memory_dir_rejects_invalid_project_slug`.
- `register_project()` now rejects an empty/invalid final slug for both
  auto-derived and explicit input, and `enable` catches `InvalidSlugError`.
  Regression coverage exists in
  `test_register_project_rejects_empty_{derived,explicit}_slug`.
- `soft_delete_curated()` now validates the fact slug before building the
  source/destination paths. Regression coverage exists in
  `test_soft_delete_curated_rejects_invalid_slug`.

New findings:

- **major** — `src/neurobase/core/store.py:81`: Phase 1 still does not create
  or validate the store-level `store.toml` required by build-plan §6 Phase 1
  and spec §10/D11 (`<root>/store.toml` with `schema = 1` and `created_at =
  <ISO8601>`, and refusal to operate on a schema newer than the binary). The
  current `ensure_tree()` only creates the per-project memory subdirectories,
  so an enabled store has no schema marker and no forward-compatibility guard.
  Suggested direction: add store metadata initialization/validation at the
  root boundary used by `enable`/store operations, and cover both first-create
  and newer-schema refusal in tests.
  - **resolution:** resolved — this was a real omission (`store.toml` wasn't
    implemented at all). Added `store.STORE_SCHEMA_VERSION`,
    `store_toml_path()`, and `ensure_store_metadata()`: writes
    `{schema, created_at}` on first use, and on every subsequent call reads
    the existing file and raises `UnsupportedSchemaError` if
    `schema > STORE_SCHEMA_VERSION`. Wired into `ensure_tree()` so `enable`
    (and anything else that ensures a project tree) also ensures/validates
    the root's `store.toml`. Verified manually end-to-end through the
    reinstalled `uv tool` shim (`enable` → `store.toml` contains `schema = 1`
    + a real `created_at`). Added three regression tests: creation shape,
    idempotence (`created_at` doesn't get rewritten on a second call), and
    refusal on a newer schema.

Verification run (Reviewer, re-review): `uv run pytest -q` (64 passed),
`uv run ruff check .`, `uv run ruff format --check .`, and
`uv run mypy src tests` all pass.

Verification run (Author, post-fix): `uv run ruff check .`, `ruff format
--check .`, `mypy src tests`, `pytest -q` all green (67 passed, up from 64).
`uv tool install . --force` + manual `enable`/`status` in a scratch repo
confirmed `store.toml` is created with the right shape.

**Author's response to verdict:** the `store.toml` gap was real — genuinely
missed it against the build-plan §6 Phase 1 deliverable list despite
following spec §1 closely; fixed as described above. Re-relaying for another
pass.

**Verdict:** changes-requested — the original slug blockers are fixed, but the
Phase 1 `store.toml` schema/versioning deliverable is still missing.
_(Awaiting re-review.)_
