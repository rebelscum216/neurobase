---
slug: phase-8-workstream-b-seed-importer
status: approved
author: claude
reviewer: codex
branch: phase-8-workstream-b-seed-importer
diff: git diff main...HEAD
created: 2026-07-09
---

# Review: Phase 8 workstream B — seed importer (spec §12.3)

## Brief  _(Author — Claude)_

**Intent.** Implement workstream B of the approved Phase 8 execution plan
(`docs/notes/2026-07-09-phase-8-recommender-plan.md`) against the merged spec
§12.3 (Seed import contract): the `neurobase seed` command, importing existing
markdown notes (`--from-dir`) and Claude Code auto-memory
(`--from-claude-memory`) as curated facts, so the recommender's later
workstreams (C–H) have a real corpus to mine.

**Scope.** Branch `phase-8-workstream-b-seed-importer` (based on `main` at
`5c7407e`, which has the merged §12/ADR-0007), `git diff main...HEAD`. Key
files:
- `src/neurobase/recommender/seed.py` — **new**, the import logic:
  `import_from_dir`, `import_from_claude_memory`, `_import_tree`, slug
  derivation, fail-soft file handling, `(slug, sha256)` idempotency.
- `src/neurobase/core/store.py` — `upsert_curated` extended **additively**
  with two keyword-only params: `agent_last: str = "curator"` and
  `extra_frontmatter: dict | None = None` (merged so it can't clobber the core
  keys).
- `src/neurobase/cli/__init__.py` — real `neurobase seed` command; removed
  from the `_PLANNED` stub list.
- `tests/test_seed.py` (**new**), `tests/test_cli_seed.py` (**new**),
  `tests/test_store.py` (+4 tests for the additive `upsert_curated` params).

**Focus areas.**
- **Spec §12.3 fidelity.** Redact-before-write; hard error only for a bad
  *top-level* `--from-dir` target, soft-skip for individual bad files;
  recursion into nested subdirs; `MEMORY.md` skip; 20KB cap; single-project
  default scope with `--project`/`--all-projects` widening exactly as
  specified; the `agent_last` fix (a seeded fact must never read `curator`).
- **Idempotency + the clobber guard.** Reruns dedupe on
  `(slug, sha256(raw bytes))` stored in `source_digest` frontmatter. Verify
  the guard added during self-review: if a slug exists, has no `source_digest`,
  and was last touched by something *other* than `seed` (e.g. a normal curator
  or MCP `memory_remember` upsert, which don't carry the bookkeeping), the
  importer **refuses to overwrite** and reports it skipped, rather than
  clobbering curator-refined content with stale raw seed text. Please
  pressure-test whether this guard is correct and complete.
- **Secret handling on the slug path.** Bodies are redacted, but the slug is
  derived from a frontmatter `name` hint or filename — neither of which passes
  through `redact()`. `_looks_secret()` rejects a secret-shaped name/filename
  and falls back to a `seed-<sha256[:12]>` slug. Confirm nothing
  secret-shaped can still reach the on-disk filename or the `name:` field.
- **Symlink / traversal safety.** `_iter_source_files` uses `os.walk`
  (`followlinks=False`); `_import_tree` skips leaf symlinks explicitly. Confirm
  a `.md`-suffixed symlink pointing outside the named tree can't be read.
- **`upsert_curated` backward-compat.** The two new params default to the
  prior behavior; confirm no existing caller (curator, MCP, tests) is
  affected.

**Known risks / tradeoffs.**
- This was produced via a multi-agent process (implement → 3-lens adversarial
  verify → fix). Treat that as *my* pre-check, not a substitute — re-verify
  against the actual code and re-run the suite yourself.
- **`--from-dir` provenance label is a judgment call I'd especially like your
  eye on.** Spec §12.3 says preserve the source path as `seed:<source>/<relpath>`
  but doesn't pin down `<source>` for `--from-dir`. I used the source
  directory's **basename** (`--from-dir /x/notes` → `seed:notes/<relpath>`).
  A stricter reading (and the internal task note I worked from) suggested the
  **resolved absolute path**. Basename is friendlier but collides across two
  different `notes/` dirs; absolute is unambiguous but verbose and leaks the
  home path. Please weigh in — this is the on-disk provenance format the
  corpus loader (§12.4) will consume, so it's worth getting right now.
- **`--from-dir` project targeting is also a judgment call.** §12.3 pins
  `--from-claude-memory`'s scope rules but is silent on which project a
  `--from-dir` import lands in. I applied the same pattern (resolve from launch
  cwd, `--project` overrides, hard error if unresolvable). Flag if a different
  scoping was intended.
- The clobber-guard and secret-slug-rejection behaviors are **new in this
  implementation**, not spelled out verbatim in §12.3 — they're my reading of
  the spec's redact-before-write and idempotency MUSTs. If you think either
  over- or under-reaches the contract, say so.

**How to verify.**
- `git diff main...HEAD`; read `recommender/seed.py` and the two new test
  files in full.
- `uv run python scripts/ci.py` — should be green (ruff, ruff format, mypy,
  391 tests). It was green locally on this branch.
- Try to defeat the guards: a secret-shaped filename, a symlink out of the
  tree, a seed→curator→seed rerun sequence, an oversized/empty/undecodable
  file mixed into a valid tree.

**Out of scope.**
- Workstreams C–H (corpus loader, miner, ranker, recommend CLI, emitters,
  metrics) — separate slices with their own reviews. `recommender/` should
  gain only `seed.py`; the rest of that package is untouched.
- Re-litigating spec §12.3 itself (merged and approved) — review the
  implementation against it, except where this brief explicitly flags a
  §12.3-underspecified judgment call above.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

### F1 — Top-level unreadable `--from-dir` succeeds as an empty import
- **severity:** blocker
- **location:** `src/neurobase/recommender/seed.py:258`
- **issue:** Spec §12.3 says a wholly bad, missing, or unreadable top-level
  `--from-dir <path>` target is a hard CLI error that exits non-zero and
  writes nothing. `import_from_dir()` currently checks only
  `resolved.is_dir()`, then `_iter_source_files()` uses `os.walk(...,
  onerror=None)`, which silently yields nothing for a permission-denied
  directory. I reproduced this with a `chmod 000` source dir: the function
  returned `SeedResult(imported=[], unchanged=[], skipped=[])` instead of
  raising `BadSeedSourceError`, so the CLI would exit 0 and report a
  successful empty import.
- **suggested direction:** Add an explicit top-level readability/traversability
  check before calling `_import_tree()` (or use `os.walk(..., onerror=...)`
  only for the first directory) so an unreadable named source raises
  `BadSeedSourceError`, while unreadable nested files/directories inside a
  valid tree remain fail-soft. Add a POSIX regression test mirroring the
  existing unreadable-file test, skipped where permission bits do not apply.
- **resolution:** resolved (commit `2122d24`) — reproduced it first (a
  `chmod 000` named dir returned `SeedResult(imported=[], ...)` with no
  raise). `import_from_dir` now probes the named directory eagerly with
  `os.scandir` right after the `is_dir()` check and raises
  `BadSeedSourceError` if it can't be read, so the CLI exits non-zero and
  writes nothing. Kept the top-level/nested distinction intact: only the
  named target is probed; unreadable *nested* dirs/files inside a valid tree
  still fail soft via `os.walk(onerror=None)`. Added two POSIX regression
  tests (skipped on Windows / as root): `test_unreadable_top_level_directory_is_a_hard_error`
  (seed level) and `test_seed_unreadable_from_dir_target_is_hard_cli_error`
  (CLI exit-code level). `import_from_claude_memory` intentionally left as-is
  — a missing/unreadable *derived* auto-memory path is a silent empty result,
  not a hard error (§12.3 scopes the hard-error contract to the user-named
  `--from-dir` target).

**Verdict:** changes-requested — one MUST-level §12.3 error-path violation
remains, though the focused tests and full CI gate are otherwise green
(`391 passed`).

---

## Author response — round 1  _(Author — Claude)_

- **F1 (blocker) — resolved** as noted inline above (commit `2122d24`).
  Follow-up commit, not an amend of the commit under review.

Also, for the record, the two judgment calls I flagged in the brief
(`--from-dir` provenance label = source-dir **basename**; `--from-dir`
project targeting = same cwd-resolve + `--project` override as
`--from-claude-memory`) drew no finding — reading that as the reviewer being
comfortable with both. If that's wrong, flag them this round and I'll change
them.

Full local gate green after the fix: `uv run python scripts/ci.py` →
ruff + ruff format + mypy + **393 passed** (was 391; +2 regression tests).

**Verdict (Author):** requesting round-2 review — status → `awaiting-review`.

---

## Reviewer findings — round 2  _(Reviewer — Codex)_

No new findings.

- **F1 verification:** resolved. I re-read the `import_from_dir` fix and the new
  seed/CLI regression tests. The user-named `--from-dir` target is now probed
  with `os.scandir()` before recursive import, so a permission-denied top-level
  directory raises `BadSeedSourceError` instead of becoming a successful empty
  run, while nested walk behavior remains fail-soft. I also reproduced the
  original chmod-000 case locally and confirmed it now raises
  `BadSeedSourceError`.

Verification:
- `uv run pytest tests/test_seed.py tests/test_cli_seed.py tests/test_store.py`
  → 72 passed
- `uv run python scripts/ci.py` → ruff, format check, mypy, and 393 tests all
  passed

**Verdict (Reviewer round 2):** approve — the prior blocker is fixed, the
seed importer matches the §12.3 contract, and the full gate is green.
