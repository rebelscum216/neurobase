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

**Verdict:** approve | changes-requested — _one-line rationale._
