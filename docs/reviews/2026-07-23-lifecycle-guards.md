---
slug: lifecycle-guards
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-lifecycle-guards
diff: git diff main...HEAD
created: 2026-07-23
---

# Review: guard the init --agent and uninstall --purge lifecycle paths — ADR-0015 step 4d

## Brief  _(Author — Claude)_

**Intent.** Close the two pre-existing §10 holes your own step-5 review (4c, round 2)
surfaced as F3/F4 — the last of G1's mutate-before-guard class. In 4c the maintainer
chose to *land the accessor guard + make the docs truthful now, and fix the code holes
in a tracked follow-up*. This is that follow-up.

**The two holes (both confirmed real in 4c):**
- **F3 — `init --agent claude|codex`** reached `backups.backup_files(root, …)` + the
  agent-config writes with **no handle**, so a newer-than-supported store was not refused
  (only the *guided* `init` flow guarded).
- **F4 — `uninstall --purge-store`** deleted `<root>` via a direct `shutil.rmtree` with
  **no `PURGE` handle** (D25 unimplemented), and backed up config *into* the store before
  deleting it.

**The fix.**
- **`_init_claude` / `_init_codex`** now `_open_store_or_exit(resolved_root, READ)` at the
  very top, before any backup/config write. A newer/corrupt schema aborts before hooks
  are installed. **READ, not WRITE**, deliberately: installing hooks must *refuse* an
  unsupported store but must **not materialize** one (WRITE creates `store.toml` on first
  use). An absent `store.toml` stays fine (READ → `schema=None`), so `init --agent`
  against a not-yet-created store is unchanged. The guided flow keeps its WRITE handle —
  it enables a repo (real store writes).
- **`uninstall --purge-store`** now opens `_open_store_or_exit(resolved_root, PURGE)`
  before `rmtree` (PURGE never refuses, so purge works on a newer/unparseable store), and
  **skips the config backup when purging** (moved under `if not purge_store`) so nothing
  is written into the store before its deletion.
- **The config-backup facility stays root-taking by design** (`backups.backup_files` /
  `restore_backup`). It is *not* put behind the schema-refusing handle because
  uninstall + disaster-recovery must work on a store of **any** schema — refusing a
  backup on a newer schema would block hook removal. The *commands* guard at their entry;
  the facility itself is command-context, like the recommender path-builders.

**Scope.** Branch `adr-0015-lifecycle-guards`, `git diff main...HEAD` (commit `a6d8a97`).
- `cli/__init__.py` — READ guard atop `_init_claude`/`_init_codex`; PURGE handle +
  backup-skip in `uninstall`.
- `tests/test_cli_init.py` — `test_init_agent_claude_refuses_newer_schema_before_any_write`,
  `test_init_agent_codex_refuses_newer_schema`.
- `tests/test_cli_uninstall.py` —
  `test_uninstall_purge_deletes_newer_schema_store_without_pre_delete_backup`.
- `docs/neurobase-spec-appendix.md` §10 — lifecycle bullets + D25 updated to reflect
  closure; the residuals list now describes the backup facility's deliberate exemption.
- `docs/known-gaps.md` — **G1 `open` → `fixed`**; *Residual gaps* section marked closed.
- `scripts/check_store_chokepoint.py` — docstring lifecycle note updated (no logic
  change; guard still passes).

**Focus areas.**
1. **READ vs WRITE for `init --agent`.** My reasoning: enforce D11 without materializing
   a store as a side effect of installing hooks. Confirm READ is right — in particular
   that refusing a *newer* schema is correct here (you shouldn't wire hooks that capture
   into a store you can't operate on) while an *absent* store still proceeds.
2. **The backup facility is deliberately NOT schema-gated.** The crux of the design: a
   backup/restore must tolerate any schema (else uninstall/recovery is bricked on a
   newer-schema store). So the fix guards the *commands*, not the backup call. Confirm
   this is sound and that I haven't left a path where an *unsupported* store is *operated
   on* (beyond the sanctioned delete) without a guard.
3. **Purge ordering + PURGE semantics.** Confirm the backup is skipped only when purging
   (non-purge uninstall still backs up — pinned by the existing
   `test_uninstall_claude_removes_owned_hooks...`), that `rmtree` still runs, and that
   opening PURGE before delete is the right D25 shape (it never refuses, so purge can't
   be blocked).
4. **G1 → fixed is now honest.** Every store-touching command runs the D11 guard
   (accessors CI-enforced; guided=WRITE, init --agent=READ, purge=PURGE). The lifecycle
   guards are command-context (not accessor-CI-enforced) — same status as the recommender
   builders. Confirm "fixed" is warranted and the §10/known-gaps text matches the code.

**Known risks / tradeoffs.**
- **Lifecycle guards are command-context, not CI-enforced.** A *future* new install path
  that forgets the handle wouldn't be caught by the accessor guard (the backup facility
  isn't tracked, by the any-schema-tolerance rationale above). This is the same residual
  the recommender path-builders carry. If you think the backup facility should instead be
  routed through a non-refusing handle mode and guard-tracked, say so — but that fights
  the any-schema requirement.
- **`init --agent` READ handle in the guided path.** `_init_guided` may open WRITE (to
  enable a repo) and then call `_init_claude`, which now also opens READ — a redundant
  but cheap second open on an already-validated store. Confirm that's harmless.

**How to verify.**
- `git diff main...HEAD`
- `uv run pytest tests/test_cli_init.py tests/test_cli_uninstall.py -q`
- Stash-verify: `git stash push src/neurobase/cli/__init__.py`, run the three new tests
  → all fail (init doesn't refuse; purge prints "Backed up …" into the doomed store),
  `git stash pop`. (I ran this; each fails pre-4d.)
- `uv run python scripts/ci.py` — full gate green incl. `store-chokepoint`; `1167 passed,
  1 skipped`, coverage 91.85%.

**Out of scope.** The deferred raw-`Path` `store.py`/`projects.py` signature removal
(~341 test sites) — still deferred, unrelated to these lifecycle guards. This branch
changes only the two lifecycle commands' guarding + docs; no other store behavior moves.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->
