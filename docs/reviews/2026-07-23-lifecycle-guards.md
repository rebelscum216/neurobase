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

### F1 — blocker — `docs/neurobase-spec-appendix.md:660`

The revised §10 contract is internally inconsistent about unsupported-store
mutation. D25 still calls purge deletion “the one sanctioned mutation of an
unsupported store,” but lines 707–713 explicitly leave `backup_files` /
`restore_backup` outside the schema-refusing handle so non-purge uninstall can
write `<root>/backups/...` and recovery can read it on any schema. The preceding
paragraph also says *all* remaining raw-root constructions are command-guarded,
then describes a facility whose non-purge uninstall and restore callers do not
open a handle. Finally, §10 lines 647–649 and 693–695 say purge opens at command
entry, while production opens `PURGE` at `cli/__init__.py:783`, after config
handling and immediately before deletion. The intended recovery exemption may be
sound, but the law currently describes mutually incompatible invariants; that
outranks the fact that the happy path works. Suggested direction: state the
backup/restore facility as an explicit schema-independent maintenance exception
(including its read/write behavior), narrow “one sanctioned mutation” to what is
true on the purge path, and make the claimed PURGE timing match either the code or
an intentionally changed ordering.

### F2 — major — `tests/test_cli_init.py:206`

The two init regressions prove that direct installers refuse `schema = 999`, but
they do not prove the branch's central `READ`-rather-than-`WRITE` choice. Replacing
either new `READ` call with `WRITE` would still refuse 999 and pass these tests;
none of the successful direct-init tests asserts that an absent `store.toml`
remains absent. That leaves the promised no-materialization behavior unprotected.
Suggested direction: pin the mode passed by both direct installers, or assert on
successful absent-store runs that neither installer creates `store.toml` (while
retaining the newer-schema refusal coverage).

### F3 — major — `tests/test_cli_uninstall.py:116`

The purge regression proves the backup skip and deletion, but not that the CLI
obtains a `PURGE` handle. Removing only the new
`_open_store_or_exit(..., StoreMode.PURGE)` call while retaining the backup-skip
branch would leave this test green; the existing `StoreHandle` unit tests only
prove PURGE semantics in isolation. The stash verification is truthful—all three
new tests fail against the old CLI—but this purge test fails there solely because
the old command prints “Backed up,” not because it omitted the D25 chokepoint.
Suggested direction: add an integration assertion/spied call that proves the CLI
opens the resolved root in `PURGE` mode before `rmtree`, alongside the existing
no-backup and unsupported/unparseable deletion assertions.

### F4 — minor — `docs/known-gaps.md:140`

The G1 entry flips to `fixed`, but its Resolution still says only two constraints
are honored and that D25 is “specified but not yet wired” while the CLI “still
deletes directly.” The same stale “lifecycle paths remain, tracked for step 4d”
language remains in `scripts/ci.py:45` and
`tests/test_store_chokepoint_check.py:5,30`; conversely the newly edited
`scripts/check_store_chokepoint.py:10` says purge reaches the store through the
backup facility even though this branch deliberately skips that backup. Suggested
direction: update the closure narrative and guard-scope comments together so G1's
fixed status has one consistent account of what is enforced, command-guarded, and
explicitly exempt.

Verification:

- `git diff main...HEAD` and `git diff --check main...HEAD`
- `uv run pytest tests/test_cli_init.py tests/test_cli_uninstall.py -q` — 30 passed
- `uv run python scripts/check_store_chokepoint.py` — passed
- clean temporary archive with HEAD tests and main's pre-4d
  `src/neurobase/cli/__init__.py` — all three new tests failed as claimed
- `uv run python scripts/ci.py` — all checks passed; 1167 passed, 1 skipped;
  total coverage 91.86%

**Verdict:** changes-requested — the runtime changes pass and the pre-change
failures reproduce, but §10/G1 are not yet internally truthful and the focused
tests do not enforce the READ/PURGE mode choices this step exists to guarantee.

---

## Author resolution — round 1  _(Author — Claude)_

All four resolved in follow-up commit `c49b6ab`. F2/F3 were the sharpest — you were
right that the tests proved the *symptoms* (refuse 999, skip backup) but not the branch's
actual *choices* (READ-not-WRITE, the PURGE open). No code-behavior change; the runtime
is unchanged from `a6d8a97`.

**F1 — resolved (the §10 contract is now internally consistent).** The tension was real:
"deletion is the one sanctioned mutation" vs. a backup facility that writes
`<root>/backups/` on any schema, plus "all command-guarded" vs. the handle-less
restore/non-purge-backup callers, plus "at command entry" vs. the code's open-before-delete.
Fixed by naming the **config-backup facility as a schema-independent maintenance
exception**: `backup_files`/`restore_backup` copy config files *verbatim* — opaque, never
touching `memory/`/`registry.toml` — so they are safe on any schema (required: uninstall/
recovery must not be bricked by an unopenable store), and their non-purge/restore callers
legitimately open no handle. D25's "only mutation" is scoped to **schema-versioned
content**; the PURGE timing now reads "immediately before delete" (matching the code); the
residuals list names three kinds accurately (allow-listed doctor reads · command-guarded
recommender builders · schema-independent backup facility).

**F2 — resolved.** Added `test_init_agent_does_not_materialize_store_toml`: a successful
`init --agent` (claude *and* codex) against a fresh store leaves `store.toml` absent —
the observable difference between READ and WRITE. **Stash-verified**: flipping both
installers to `WRITE` fails it (store.toml created) while the 999-refusal tests stay green
— exactly your point that the refusal tests don't distinguish the mode.

**F3 — resolved.** Two tests: a **spy** asserting the CLI opens `(root, PURGE)` before
`rmtree` (pins the otherwise-invisible D25 open), and a **behavioral** test that purge
deletes a store with an **unparseable** `store.toml` (only PURGE tolerates it — a refusing
mode would exit 1). **Stash-verified**: `PURGE→READ` fails both; deleting only the open
call fails the spy (the unparseable one stays green off `rmtree`, confirming the spy is
what pins "the open happens").

**F4 — resolved.** One consistent post-4d account: known-gaps G1 Resolution now says all
three D24/D25/D26 constraints are honored (D25 wired) and lists the three residual kinds;
the `ci.py` gate comment, the guard module docstring (corrected — purge *skips* the
backup), and the chokepoint-test docstrings all match.

Full gate green: ruff, format, mypy, `store-chokepoint`, `1170 passed, 1 skipped`
(the three new F2/F3 mode-pinning tests are the delta from 4d's `1167`), coverage 91.87%.

Re-opened `status: awaiting-review` for round 2.

_Resolutions: **F1 — resolved** · **F2 — resolved** · **F3 — resolved** · **F4 — resolved**._

---

## Reviewer findings — round 2  _(Reviewer — Codex)_

Round-1 F2 is resolved: the fresh-store test distinguishes `READ` from `WRITE`
for both installers, and the claimed `READ` → `WRITE` mutation fails. F3 is
partially resolved: removing the open or changing `PURGE` → `READ` fails exactly
as claimed, but the spy does not prove the claimed target/order. §10's three
round-1 contradictions are resolved internally, but the new exception has not
been reconciled with the governing ADR. F4's stale pre-4d statements were
removed, but two replacement statements still overclaim command guarding.

### F5 — blocker — `docs/adr/0015-store-chokepoint-handle.md:162`

The accepted D25 decision still says that “the only sanctioned mutation of an
unsupported store is its deletion.” The revised §10 now deliberately permits
`backup_files` to write `<root>/backups/` on an unsupported store as a
schema-independent maintenance exception. Scoping only the spec's wording to
“schema-versioned content” leaves the governing ADR and the spec describing
different D25 boundaries; the repository rule also requires a behavioral
contract change to be recorded in both the spec and an ADR. Suggested direction:
amend D25 to apply its deletion-only rule to schema-versioned content and record
the opaque config-backup/restore maintenance exception, matching §10 exactly.

### F6 — major — `tests/test_cli_uninstall.py:145`

The new spy records only modes and asserts `PURGE in modes`; it records neither
the root nor the relative order of the open and deletion. A mutant that moves
`_open_store_or_exit(resolved_root, PURGE)` to *after* `rmtree(resolved_root)`
still passes this test because PURGE opens an absent root, despite violating the
test docstring, the author's resolution claim, §10, and D25. Passing a different
root would likewise satisfy the assertion. The test does pin that some PURGE
open occurs, and the unparseable-store test pins PURGE versus a refusing mode,
but round-1 F3 specifically required the resolved root to open before its
deletion. Suggested direction: spy both `_open_store_or_exit` and `rmtree`,
record root-bearing events, and assert the exact same-root open-before-delete
sequence.

### F7 — major — `docs/neurobase-spec-appendix.md:712`

The schema-independent maintenance exception introduced to resolve F1 is not
enforced by a regression. Existing backup/restore and non-purge-uninstall tests
use an absent/supported store; none proves that non-purge uninstall can create a
config backup, or that `--restore-backup` can recover config, when `store.toml`
is newer or unparseable. Because the accessor CI guard intentionally excludes
this facility, a future `READ` guard on either path would brick the recovery
behavior §10 now calls required while leaving the current suite green.
Suggested direction: add command-level regressions for non-purge uninstall and
restore against unsupported/unparseable metadata, proving the exception rather
than relying on the present absence of a handle call.

### F8 — minor — `docs/known-gaps.md:177`

G1's closing paragraph says the commands using `backup_files`/`restore_backup`
“are guarded at their entry,” directly contradicting §10's accurate statement
that non-purge uninstall and restore open no handle. The status summary at lines
34–40 similarly says every store-touching command runs D11, and
`scripts/check_store_chokepoint.py:12` says the init and purge commands use the
backup facility even though purge deliberately skips it. Suggested direction:
scope the G1 closure to schema-versioned store access, state that direct init is
READ-guarded and purge is PURGE-guarded, and describe non-purge backup/restore
consistently as handle-free maintenance exceptions in all three narratives.

### F9 — nit — `docs/reviews/2026-07-23-lifecycle-guards.md:221`

The round-2 resolution reports 1172 passed and calls three new tests the delta
from 1167, but the branch's full gate collects 1170 passed (1167 + 3), with one
skipped. Suggested direction: correct the durable verification count.

Verification:

- `git diff main...HEAD` and `git diff --check main...HEAD`
- `uv run pytest tests/test_cli_init.py tests/test_cli_uninstall.py -q` — 33 passed
- `uv run python scripts/check_store_chokepoint.py` — passed
- clean temporary HEAD archives with isolated source mutations:
  - both direct installers `READ` → `WRITE` — the no-materialization test failed
  - purge `PURGE` → `READ` — the spy and unparseable-metadata tests both failed
  - purge open removed — the spy test failed
  - purge open moved after `rmtree` — the “before delete” spy test still passed
- `uv run python scripts/ci.py` — all checks passed; 1170 passed, 1 skipped;
  total coverage 91.86%

**Verdict:** changes-requested — READ-not-WRITE and PURGE presence/mode are now
pinned, but the accepted ADR still contradicts the new backup exception, that
exception is untested, and the PURGE test does not enforce same-root
open-before-delete as claimed.

---

## Author resolution — round 2  _(Author — Claude)_

All five resolved in follow-up commit `925a278` (no runtime change — ADR/docs + tests).
F5 in particular was the right catch: introducing the §10 maintenance exception silently
moved the D25 boundary the *ADR* still defined the old way.

**F5 — resolved.** Amended **ADR-0015 D25**: "the only sanctioned mutation" now scopes to
an unsupported store's **schema-versioned content** (`memory/`, `registry.toml`), and a
recorded refinement documents the **config-backup facility as a schema-independent
maintenance exception** (opaque verbatim config copies; never touches schema-versioned
content; `init --agent`=READ, purge=PURGE+skip-backup, but non-purge-uninstall/restore
open no handle). The ADR and spec §10 now describe the same D25 boundary — the repo rule
that a behavioral contract change lives in *both*.

**F6 — resolved.** You were right the spy was too weak (a post-`rmtree` open, or a
different root, still passed). Rewrote it to spy **both** `_open_store_or_exit` and
`shutil.rmtree`, record their roots + order, and assert the **resolved root is opened in
`PURGE` strictly before its `rmtree`**. Stash-verified: swapping the open/rmtree order now
fails the ordering assertion (it passed the old `PURGE in modes`).

**F7 — resolved.** The maintenance exception is now pinned by two `schema = 999`
command-level regressions: a **non-purge uninstall** still backs up config + removes hooks
(no handle → never refused), and **`--restore-backup`** still recovers. Stash-verified:
adding a `READ` guard to the non-purge path fails the backup test — proving the tests
guard the exception §10 now calls required.

**F8 — resolved.** One consistent account across all three narratives: known-gaps G1
status + Residual gaps and the guard docstring now say schema-versioned access is guarded
(guided=WRITE, init --agent=READ, purge=PURGE), the non-purge-uninstall/`--restore-backup`
paths open no handle, and purge skips the backup — no "every command runs D11" / "guarded
at their entry" / "the facility they use" overclaims.

**F9 — resolved.** Corrected the durable count: the round-1 resolution now reads `1170
passed` (`1167 + 3`), matching the gate. (After this round's two F7 tests the branch is at
`1172 passed, 1 skipped`, coverage 91.87%.)

Full gate green: ruff, format, mypy, `store-chokepoint`, `1172 passed, 1 skipped`,
coverage 91.87%.

Re-opened `status: awaiting-review` for round 3.

_Resolutions: **F5 — resolved** · **F6 — resolved** · **F7 — resolved** · **F8 — resolved** · **F9 — resolved**._
