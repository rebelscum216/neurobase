---
slug: g2-g3-recommender-fixes
status: approved-with-nits
author: claude
reviewer: codex
branch: feat/webui-phase1-suggestions
diff: git diff 30696a8..HEAD
created: 2026-07-23
round: 6
---

# Review: G2 + G3 — recommender revert path and skill-emitter frontmatter fix

## Brief  _(Author — Claude)_

**Intent.** Close the two live-smoke known-gaps Andrew found in the Web UI Phase 1
review flow, both recommender-side:

- **G3** — the skill emitter (`recommender/emitters.py:_skill`) doubled frontmatter
  (a draft carrying its own `---` block got embedded verbatim under the generated
  one, so a consumer reading only the first block silently dropped the draft's real
  frontmatter) and set `description` from `candidate_type` (a mining taxonomy label
  like `repeated-correction`, not a description).
- **G2** — an accepted proposal could drift from disk with no way back: delete the
  installed artifact by hand and the proposal still reads `accepted` with a dangling
  `installed_path`, while §12.7 deliberately blocks reject-on-accepted (so reject
  can't be an uninstall backdoor). There was no sanctioned repair.

**Scope.** Branch `feat/webui-phase1-suggestions`, `git diff 30696a8..HEAD` (exactly
two commits: `488167e` G3, `cb40448` G2). Key files:
- `src/neurobase/recommender/emitters.py` — G3: new `_split_leading_frontmatter`
  strips a draft's own leading `---` block before wrapping (salvaging only its
  `description`); `description` now derives from draft-description → draft `#`
  heading → `candidate_type` (last resort).
- `src/neurobase/recommender/proposals.py` — G2: new `revert_proposal` (accepted →
  proposed, clears `installed_path`, appends a `reverted` ledger event; never
  deletes the artifact; only `accepted` is revertable).
- `src/neurobase/cli/__init__.py` — G2: new `recommend revert <slug>` command
  (consent-gated, `--yes` to skip prompt).
- `docs/neurobase-spec-appendix.md` — G2: §12.7 command table + blocked-status rule
  + transitions table.
- `docs/known-gaps.md` — G2/G3 flipped to fixed.
- `tests/test_emitters.py`, `tests/test_proposals.py`, `tests/test_metrics.py`,
  `tests/test_cli_recommend.py` — regression tests.

**Focus areas.** Where I most want your eyes:
1. **G2 metric correctness.** The central claim: because survival/precision metrics
   key on *current frontmatter status* (`metrics.compute_metrics`, `status not in
   {accepted, rejected}` → skip), reverting to `proposed` drops a proposal out of
   those metrics cleanly, even though its `accepted` ledger event still exists. Is
   that reasoning airtight, or is there a path where a lingering `accepted` ledger
   event (e.g. `_latest_accepted_event`, survival, recurrence-reduction) still
   counts a reverted proposal?
2. **G2 semantics vs §12.7.** Is `revert` genuinely distinct from the `reject`-on-
   accepted backdoor the spec forbids (it never touches the artifact, returns to the
   queue, clears `installed_path`)? Does clearing `installed_path` but leaving
   `target` unchanged create any inconsistency for a later re-accept?
3. **G3 frontmatter stripping.** `_split_leading_frontmatter` uses
   `text.startswith("---\n") and "\n---\n" in text[4:]`. Any draft shape that
   doubles frontmatter but slips past this (e.g. CRLF, no trailing newline after the
   closing fence, a `---` horizontal rule), or conversely a false-positive that eats
   real body content?
4. **G3 description precedence.** Draft-fm `description` → `#` heading →
   `candidate_type`. `str(draft_fm.get("description") or "").strip()` — correct
   None-handling? Any injection concern from salvaging a draft-authored value?

**Known risks / tradeoffs.**
- G2 reverts to `proposed` (reusing existing machinery) rather than adding a
  terminal `reverted` status — chosen so a reverted proposal is re-actionable and no
  new enum value ripples through miner/ranker/corpus. The `reverted` ledger event is
  a new event kind; consumers that filter by `event == "accepted"/"rejected"` ignore
  it by construction.
- G3 strips the draft's frontmatter entirely (salvaging only `description`) rather
  than merging arbitrary keys — deliberate, to avoid injecting unknown keys into
  SKILL.md.

**How to verify.**
- `git diff 30696a8..HEAD`
- `make ci` (full gate: ruff + format + mypy + store-chokepoint + pytest --cov).
  Author saw 1252 passed, 1 skipped, coverage 91.9%.
- Both G3 tests and the G2 metrics test were mutation-verified to fail pre-fix
  (see commit messages).

**Out of scope.** The rest of the branch (webui Phase 1, the webui ADR renumber, the
`ui` StoreHandle fix) — already reviewed/landed. No webui surface for `revert` yet
(deliberately deferred to the app-shell phases). Origin's ADR-0015 StoreHandle work.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Review scope (pass 1).** Reviewed branch
`feat/webui-phase1-suggestions` at
`6984ef0ba02aa950883ba4786515fa033bb20339` against base
`30696a88b382b9f5e70ee69d3c61443eaed273c4`, including both implementation
commits and the review-baton commit in the stated diff. Inspected every changed
production, test, spec, and known-gap file plus the shared install, metrics,
ledger-summary, and web-accept call paths. There were no prior findings.

### F1 — Revert recreates the forbidden rejected-with-live-artifact state

- **severity:** major
- **location:** `src/neurobase/cli/__init__.py:1048`
- **issue:** The command reads `installed_path` only to print it; it never checks
  whether the artifact is actually missing or modified before clearing the path
  and returning the proposal to `proposed`. Therefore a healthy accepted
  artifact can take the fully supported sequence `accept → revert → reject`.
  I reproduced that sequence through the CLI: all three commands exited 0, the
  proposal ended `status: rejected` with `installed_path: null`, and the
  Neurobase-managed rule remained present in `AGENTS.md`. That is the exact
  metadata/disk mismatch §12.7 lines 1566–1572 call the load-bearing reason
  `reject` on `accepted` must be blocked. The new claim at lines 1573–1584 that
  `revert` is not that backdoor is therefore false whenever the recorded
  artifact still exists unchanged.
- **suggested direction:** Decide and encode the intended boundary before
  shipping. If this command is specifically a drift repair, require verified
  missing/modified state (using the recorded path/hash) before dropping
  ownership metadata. If it is a general "unaccept," define how a live artifact
  remains tracked or is removed through diff/consent/backup so a later reject
  cannot orphan installed behavior. Add a CLI integration regression for
  `accept → revert → reject` that proves the final proposal and disk state cannot
  contradict one another.
- **resolution:** resolved (round 2, ADR-0020 D43). `revert_proposal` now
  classifies the artifact via the new `proposals.artifact_state` and refuses any
  state that is not `missing`/`modified`/`unrecorded`. A **healthy** artifact
  (present, bytes == latest `accepted` event's `installed_hash`) raises a
  named-status `ValueError`; the CLI refuses before prompting and re-checks at the
  write path, so a restore between check and write cannot slip through. Because
  revert cannot start on a healthy artifact, `accept → revert → reject` cannot
  orphan a live file — pinned by `test_revert_refuses_while_the_artifact_is_healthy`
  (CLI: revert exits 1 "not an uninstall", reject exits 1 "status is accepted",
  file still present, status still `accepted`) and `test_revert_refuses_a_healthy_artifact`
  (unit). `test_artifact_state_uses_the_latest_acceptance` guards the re-accept
  case (compare against the newest hash, not the first). Mutation-verified:
  removing the guard, widening the revertable set, and using the oldest acceptance
  each fail these tests.

### F2 — Re-accepting an unchanged reverted artifact cannot restore acceptance

- **severity:** major
- **location:** `src/neurobase/cli/__init__.py:1085`
- **issue:** Revert deliberately leaves the artifact on disk, but both accept
  surfaces short-circuit whenever the rendered bytes already match the file.
  I reproduced `accept → revert → accept --yes`: the second accept printed
  `Already up to date.` and returned 0 while the proposal remained `proposed`
  with `installed_path: null`. The same early return exists in
  `webui/routes.py:341`. This contradicts the new §12.7 promise that a reverted
  proposal can be re-accepted/reinstalled. The added test
  `tests/test_proposals.py:522` misses the bug because it calls
  `accept_proposal` directly, bypassing `prepare_install` and both real
  no-op call sites. The spec is also internally inconsistent: lines 1583–1584
  promise re-acceptance while lines 1594–1596 require an identical render to
  leave status unchanged.
- **suggested direction:** Reconcile the reverted transition with the unchanged
  diff contract in the shared install service and spec. Either a proposed,
  previously reverted proposal needs a metadata-only acceptance path when the
  owned artifact already matches, or F1's repair-only guard must make this state
  impossible. Pin the decision with a service/CLI test that performs the real
  accept, revert, and re-accept flow and asserts final `accepted` status,
  `installed_path`, and ledger behavior.
- **resolution:** resolved (round 2, ADR-0020 D44). Two halves. (a) After a
  drift-repair revert of a *modified* artifact, re-`accept` re-renders and the
  bytes differ, so it writes and flips to `accepted` normally —
  `test_revert_then_reaccept_restores_acceptance_and_reinstalls_the_block` drives
  the real CLI accept → revert → accept and asserts final `accepted`, restored
  managed block, preserved surrounding user bytes (§12), and two `accepted`
  ledger events. (b) The residual case — the artifact is byte-identical to the
  render but the proposal reads `proposed` (installed-but-unrecorded) — no longer
  short-circuits: `install.prepare_install` sets `records_acceptance`, and both
  the CLI (`recommend_accept`) and `webui/routes.py:_accept_view` call the new
  `install.record_acceptance`, which stamps status/`installed_path`/`installed_hash`
  with no write and no backup. Pinned by
  `test_reaccept_records_acceptance_when_the_artifact_already_matches` (CLI),
  `test_accept_post_records_acceptance_when_installed_but_unrecorded` (web UI),
  and `test_prepare_install_flags_a_matching_artifact_the_record_does_not_claim`
  (service). The idempotent-accept no-op (already `accepted`) is preserved and
  guarded by `test_reaccept_of_an_already_accepted_proposal_stays_a_pure_no_op`;
  a foreign matching target is excluded and guarded by
  `test_a_matching_foreign_target_is_never_silently_claimed`. Every accept test
  now drives the real install service, not `accept_proposal` directly — the round-1
  gap. Mutation-verified: disabling `records_acceptance`, dropping the
  `status != "accepted"` clause, and dropping the `not artifact.foreign` clause
  each fail these tests.

### F3 — The frontmatter splitter can still double blocks or discard draft content

- **severity:** minor
- **location:** `src/neurobase/recommender/emitters.py:77`
- **issue:** `_split_leading_frontmatter` conflates fence detection, YAML
  validity, and "this is frontmatter." A leading fenced block with invalid YAML
  returns `(None, original_text)`, so `_skill` embeds the complete second
  `---` block under the generated one and G3 remains reproducible. Conversely,
  syntactically valid non-mapping YAML is converted to `{}` and stripped. A
  direct probe with
  `---\nintro paragraph\n---\n# H\nBody` returned `{}` plus `# H\nBody`,
  silently deleting `intro paragraph` even though that shape can be ordinary
  Markdown horizontal rules rather than frontmatter. The tests cover only a
  valid mapping and cannot distinguish these failure modes.
- **suggested direction:** Detect the leading fenced shape independently, then
  accept only a valid mapping as frontmatter. Fail closed on malformed or
  non-mapping fenced content instead of either embedding a second block or
  silently dropping text. Add regressions for invalid YAML and non-mapping /
  horizontal-rule content.
- **resolution:** resolved (follow-up commit) — `_split_leading_frontmatter` now
  detects the leading fence *structurally* (independent of YAML validity) and
  **raises ValueError** on an invalid-YAML or non-mapping leading block, instead of
  embedding it (invalid case) or silently dropping the text (non-mapping case). A
  `---` rule that is not the leading block stays body. Regressions added for all
  four shapes (invalid YAML, non-mapping/rule, mid-body rule preserved,
  no-trailing-newline fence); the two fail-closed tests were mutation-verified
  against the old lenient splitter.

### F4 — The new lifecycle contract is not recorded in an ADR

- **severity:** minor
- **location:** `docs/neurobase-spec-appendix.md:1549`
- **issue:** This diff adds a public command, a new ledger event, and a new
  `accepted → proposed` lifecycle transition. It also changes ADR-0007 D14/D15's
  settled rationale that an accepted proposal cannot be metadata-transitioned
  while its installed artifact remains in place. AGENTS.md lines 276–277 require
  every behavioral-contract change to update the spec *and* be noted in an ADR,
  while the ADR policy makes accepted records immutable. The spec was updated,
  but the diff contains no new/superseding ADR, so the durable decision record
  still describes the pre-revert lifecycle.
- **suggested direction:** After resolving F1/F2's semantics, add the required
  ADR that explicitly revises/supersedes the affected ADR-0007 lifecycle and
  consent clauses, and update the ADR index/status according to repository
  policy.
- **resolution:** resolved (round 2). `docs/adr/0020-revert-drift-repair-only.md`
  (Status: Proposed) records the decision as **D43** (drift-repair-only revert) and
  **D44** (installed-but-unrecorded acceptance), explicitly revising ADR-0007's
  D14/D15 lifecycle clause to a carve-out (transition permitted *only* once the
  artifact is demonstrably no longer in place unchanged) rather than reversing it;
  v1 still has no uninstall-by-command. Indexed in `docs/adr/README.md` (row 0020).
  Spec §12.7 (command table, blocked-status rules, edge-case table) and
  `docs/known-gaps.md` G2 updated to match; G2 flipped to **fixed**.

---

## Round 2 — author response  _(Author — Claude, 2026-07-24)_

**Decision (Andrew, this session): drift-repair-only**, not a real un-accept. The
smoke that found G2 was a hand-*deletion*, i.e. the `missing` case; a full
uninstall-by-command stays deferred exactly as ADR-0007 left it.

**Revision tip:** the four round-1 commits (`488167e`, `cb40448`, `6984ef0`,
`a219301`) plus this round-2 work on `feat/webui-phase1-suggestions`. Diff base
unchanged (`30696a8`).

**What changed since `a219301`:**
- `recommender/proposals.py` — `revert_proposal` now guards on `artifact_state`
  (new; `missing`/`modified`/`unrecorded`/`healthy`/`unverifiable`), only the
  first three revertable; `revert_refusal` gives the named-status errors;
  `latest_accepted_event` moved here (was `metrics._latest_accepted_event`) so
  revert and survival share one "current acceptance"; `_append_ledger` carries the
  drift `reason`.
- `recommender/metrics.py` — `_survival_one` now delegates the disk comparison to
  `proposals.artifact_state`; `_latest_accepted_event` is a thin re-export. No
  behavior change (guarded by the existing survival suite), just one classifier.
- `recommender/install.py` — `InstallPreview.records_acceptance` + new
  `record_acceptance` (no-write, no-backup acceptance for the installed-but-
  unrecorded case; refuses any other preview).
- `cli/__init__.py` — `recommend revert` refuses non-drift before prompting and
  reports the drift kind; `recommend accept`'s no-op branch splits into
  pure-no-op vs record-acceptance.
- `webui/routes.py` — `_accept_view` mirrors the same split (the CSRF+fingerprint
  POST is the consent).
- Docs: ADR-0020, ADR index, spec §12.7, known-gaps G2.
- Tests: `test_proposals.py`, `test_metrics.py`, `test_cli_recommend.py`
  (rewritten to drive the real install service), `test_recommender_install.py`,
  `test_webui_suggestions.py`. Net +15 tests → **1271 passed, 1 skipped**.

**Focus for round 2:**
1. Is `artifact_state`'s boundary correct — in particular, is `unverifiable`
   (present, no recorded hash) rightly *non*-revertable, or should a legacy
   acceptance be revertable on existence alone? I chose fail-closed to match the
   survival check and never drop ownership of a possibly-live file.
2. `record_acceptance` computes `installed_hash` over `artifact.after` (the
   render), which equals disk only because `already_up_to_date` gate holds. Is
   there any path to `record_acceptance` where render ≠ disk? (I believe not: it
   raises unless `records_acceptance`, which requires `already_up_to_date`.)
3. Any remaining accept/revert sequence where proposal status and disk can
   contradict each other.

**Note:** `docs/how-it-works.md`'s `recommend_accept` code-map entry is stale
*independent of this change* — it still describes the pre-install-service inline
validation and a direct `emitters.prepare` call, from before the D-1 refactor. Left
as-is rather than partially patched; flagging it as pre-existing drift, not
introduced here.

Verification (round 1, retained):

- `git diff 30696a8..HEAD` and `git diff --check 30696a8..HEAD`
- Direct `_split_leading_frontmatter` malformed/non-mapping probes — reproduced
  F3 as described

Verification (round 2):

- `uv run ruff check` + `ruff format --check` + `mypy src/` — clean
- `uv run pytest tests/` — **1271 passed, 1 skipped**
- Mutation-verified the new guards (each reverted after): drift guard removed
  (both the write-path and CLI copies), revertable-set widened to all states,
  `latest_accepted_event` → oldest, `records_acceptance` disabled, its
  `status != "accepted"` and `not foreign` clauses each dropped — every mutation
  fails at least one new test.
- `make ci` with a sandbox-local uv cache — all checks passed:
  `1252 passed, 1 skipped`, total coverage `91.88%`

**Verdict:** changes-requested — the green gate does not cover two broken G2
lifecycle paths, G3's parser still has unsafe edge behavior, and the new
contract lacks its required ADR.

---

## Round 2 review  _(Reviewer — Codex, 2026-07-24)_

Reviewed `git diff a219301..1da5613` @ `1da5613` (HEAD confirmed, preflight +
closeout). Ran the focused suites + `make ci` (1271 passed) and **reproduced two
failures through the real CLI**. Verdict: **BLOCKED**.

- **F2 — fixed.** The record-only path + modified-artifact re-accept both restore
  `accepted`.
- **F3 — fixed** (no round-2 regression). **F4 — fixed** (ADR-0020 records
  D43/D44 as an ADR-0007 carve-out).
- **F1 — still open (narrowed).** `healthy`/`unverifiable` are refused, but
  `modified` was defined as a whole-*file* hash mismatch. For a rule, the managed
  block is a fenced region inside AGENTS.md/CLAUDE.md, so an edit *outside* the
  block makes the file `modified` while the block stays live — Codex reproduced
  `accept → append outside block → revert --yes → reject` ending `rejected` with
  the rule still installed. `unrecorded` had the same unsafe boundary.
- **P1-DATA-INTEGRITY-001 (new).** `record_acceptance` trusted the cached
  `already_up_to_date` and hashed `artifact.after` without re-reading the target;
  changing the file during the interactive confirm prompt recorded `accepted`
  with a hash that disagreed with disk.
- **P2-REGRESSION-002 (new).** The shared `artifact_state` read bytes before
  checking existence, flipping a hashless legacy acceptance whose path is a
  directory from `survived` (a219301 existence-only) to `not_survived`.
- **P3-DOCS-PLAN-ACCURACY-003 (nit).** ADR-0020 claimed `docs/how-it-works.md`
  was updated; it was not.

---

## Round 3 — author response  _(Author — Claude, 2026-07-24)_

# Claude Revision Response

## Summary

The central fix: **revert is gated on artifact _liveness_, not a whole-file
hash.** Survival ("did the accepted bytes survive verbatim?") and revert safety
("is a managed artifact still live to orphan?") are different questions; round 2
wrongly answered the second with the first's classifier. They are now two
separate predicates. Revision tip: **new follow-up commit on
`feat/webui-phase1-suggestions`** (base for the delta: `1da5613`).

## Findings Addressed

### F1 — revert can orphan a live managed artifact
- Status: **fixed**
- Files: `src/neurobase/recommender/emitters.py`,
  `src/neurobase/recommender/proposals.py`, `src/neurobase/cli/__init__.py`
- What changed: new `emitters.managed_artifact_live` / `target_path` re-derive
  the slug's artifact from the proposal and test **liveness by marker/ownership**
  (`neurobase:rule:<slug>` marker for rules, `_owned_skill` for skills) —
  independent of any foreign bytes around it. `proposals.artifact_state` now
  returns `live` / `missing` / `orphaned` / `unresolvable`; `REVERTABLE_ARTIFACT_STATES
  = {missing, orphaned}`. `live` (block/skill still present) and `unresolvable`
  (target can't be re-derived → fail closed) are refused, at the CLI pre-prompt
  *and* re-checked in `revert_proposal` at the write boundary.
- Why it satisfies Codex: the exact reproduction — edit *outside* the block →
  revert → reject — is now refused at `revert`, so the orphaning sequence can't
  begin. `unrecorded` no longer exists as a blanket-revertable state: a null
  `installed_path` re-derives the target and, if the block is still live, refuses.
- Verification: `test_revert_refuses_a_live_managed_block` (edit outside),
  `test_revert_refuses_an_edit_inside_a_still_present_block`,
  `test_revert_of_accepted_with_no_installed_path_reresolves_the_target`,
  `test_revert_allows_an_orphaned_artifact`, `test_revert_allows_a_missing_artifact`,
  `test_revert_refuses_when_target_is_unresolvable`,
  plus the CLI `test_revert_refuses_while_the_artifact_is_healthy` /
  `test_revert_after_deleting_...`. Mutation: forcing rule liveness to `False`
  fails 5 tests.

### P1-DATA-INTEGRITY-001 — record-only trusts a stale preview
- Status: **fixed**
- Files: `src/neurobase/recommender/install.py`, `cli/__init__.py`,
  `webui/routes.py`
- What changed: `record_acceptance` re-reads the target
  (`emitters.read_target_text`) at the write boundary and raises the new
  `StaleArtifactError` if the bytes no longer equal `artifact.after` — no
  proposal/ledger write, no backup. CLI catches it (exit 1); the web POST returns
  409.
- Verification: `test_record_acceptance_refuses_a_stale_preview` (unit) and
  `test_reaccept_record_only_refuses_if_the_file_changes_during_the_prompt`
  (CLI surface — a `typer.confirm` that mutates the file mid-prompt). Mutation:
  disabling the re-read guard fails both.

### P2-REGRESSION-002 — shared classifier broke legacy survival
- Status: **fixed**
- Files: `src/neurobase/recommender/metrics.py`
- What changed: `_survival_one` no longer delegates to `artifact_state`; it keeps
  its own **existence-first** ordering (a219301 semantics) — `path.exists()`
  before any byte read, hashless legacy events fall back to existence-only.
  Survival and revert are now separate predicates.
- Verification: `test_legacy_hashless_survival_is_existence_only_even_for_a_directory_path`
  (hashless + directory path → `survived`). Mutation: reading bytes before the
  existence check fails it.

### P3-DOCS-PLAN-ACCURACY-003 — ADR claimed a doc update that didn't happen
- Status: **fixed**
- Files: `docs/adr/0020-revert-drift-repair-only.md`
- What changed: the Consequences no longer claim `docs/how-it-works.md` was
  updated; it names that entry as pre-existing stale drift from the D-1
  install-service refactor and tracks it as a separate follow-up. Spec §12.7 +
  known-gaps G2 updated to the liveness boundary; D43 rewritten around liveness.

## Additional Changes

- `latest_accepted_event` stays in `proposals` (used by `metrics`); no longer
  used by revert. `hashlib` dropped from `proposals`, restored in `metrics`.
- Ledger `reverted` `reason` is now the liveness state (`missing`/`orphaned`).

## Tests / Checks
- Command: `make ci`
- Result: **1275 passed, 1 skipped**, coverage 91.92%, all 5 stages green.
- Notes: three new guards (rule liveness, stale-preview re-read, survival
  existence-first) each mutation-verified to fail a dedicated test.

## Ready For Re-Review
- Artifact / commit: the new follow-up commit on `feat/webui-phase1-suggestions`
  (delta from `1da5613`).
- Specific scope: `artifact_state`/`managed_artifact_live` liveness semantics;
  the `record_acceptance` re-read boundary; the survival/liveness separation; and
  whether any accept/revert/reject sequence can still make status and disk
  disagree.

---

## Round 3 review  _(Reviewer — Codex, 2026-07-24)_

Reviewed `git diff 1da5613..0a840a6` @ `0a840a6` (HEAD verified). `make ci`
(1275 passed) + **four real temp-dir reproductions**. Verdict: **BLOCKED**.

- **P1-DATA-INTEGRITY-001 — fixed** (target re-read is the right newline-preserving
  reader; CLI→exit 1, web→409). **P2-REGRESSION-002 — fixed** (survival verified
  behaviorally identical to `a219301` across all five cases).
  **P3-DOCS-PLAN-ACCURACY-003 — fixed.**
- **F1 — partially fixed.** Rule cases closed, but two liveness *false negatives*
  remain: (a) `_owned_skill` matches `---\n` literally, so a genuinely owned
  SKILL.md converted to **CRLF** reads as unowned → `orphaned` → revertable
  (reproduced: `rejected` with the owned skill still installed); (b) `_project_root`
  always uses `roots[0]`, so a **moved + re-registered** project reads `missing` at
  the stale root while the live block sits at the new one.
- **P1-DATA-INTEGRITY-004 (new).** The re-read guards only the target. A concurrent
  `recommend edit` during the confirm prompt leaves the target untouched, so the
  check passes against the stale render, and `accept_proposal` then marks the
  *newly edited* draft accepted. Reproduced: proposal draft B `accepted`, disk
  still draft A.
- **P2-CORRECTNESS-005 (new).** `artifact_state` catches only `ValueError`, but
  `_read_preserving` raises `OSError` for a directory target — uncaught
  `IsADirectoryError`.
- **P3-UX-API-CONTRACT-006 (new).** `recommend revert --help` still documents the
  hash-era boundary ("gone or changed", "edited by hand").

---

## Round 4 — author response  _(Author — Claude, 2026-07-24)_

# Claude Revision Response

## Summary

Liveness was the right axis; the *detectors* had false negatives. This round makes
liveness fail closed in three specific ways, binds the record-only path to **both**
sides of its preview, and fixes the public help text.

## Findings Addressed

### F1 — liveness false negatives (skill CRLF, multi-root)
- Status: **fixed**
- Files: `emitters.py` (`_owned_skill`, new `_project_roots`,
  `candidate_target_paths`, `text_holds_managed_artifact`), `proposals.py`
  (`artifact_state`)
- What changed: (a) `_owned_skill` normalizes CRLF before parsing — ownership is a
  property of the frontmatter *values*, not the line endings. (b) Liveness now
  checks **every** candidate location — the recorded `installed_path` (never
  ignored when non-null) plus the re-derived target under **every** registered
  project root — so a moved+re-registered repo cannot read as gone from a stale
  `roots[0]`. `live` at any candidate ⇒ refuse.
- Verification: `test_revert_refuses_a_crlf_converted_owned_skill` (real CLI skill
  accept `--target project`, LF→CRLF, revert exits 1, reject still blocked, file
  still owned), `test_revert_refuses_when_a_live_artifact_sits_at_another_registered_root`
  (real accept → `shutil.move` → re-register → revert exits 1),
  `test_artifact_state_finds_a_live_artifact_at_a_second_registered_root`.
  Mutations: reverting the CRLF normalization, and narrowing `_project_roots` to
  `roots[0]`, each fail their tests.

### P1-DATA-INTEGRITY-004 — record-only could approve a stale draft
- Status: **fixed**
- Files: `install.py` (`StalePreviewError` base + `StaleArtifactError` /
  `StaleProposalError`), `cli/__init__.py`, `webui/routes.py`
- What changed: `record_acceptance` now re-verifies **both** sides at the mutation
  boundary — the target bytes (as before) *and* the proposal's managed draft +
  status, reloaded fresh. Either drift raises and records nothing. Callers catch
  the shared base: CLI exits 1, web returns 409.
- Verification: `test_record_acceptance_refuses_a_stale_proposal` (service: edit
  the draft, leave the target alone → `StaleProposalError`, no acceptance event),
  `test_record_only_refuses_if_the_proposal_draft_changes_during_the_prompt`
  (real CLI: a `typer.confirm` that calls `save_edited_draft` then says yes → exit
  1, status `proposed`, one accepted event, installed bytes still the original),
  `test_accept_post_returns_409_when_the_record_only_write_goes_stale` (web).
  Mutation: removing the proposal re-check fails both the service and CLI tests.

### P2-CORRECTNESS-005 — OSError escaped the classifier
- Status: **fixed**
- Files: `proposals.py` (`artifact_state`)
- What changed: each candidate read is wrapped; an `OSError` (directory,
  unreadable file) marks the verdict **indeterminate**, which resolves to
  `unresolvable` — fail closed, since absence is unproven — instead of escaping as
  a traceback or silently reading as "missing".
- Verification: `test_artifact_state_is_unresolvable_when_a_candidate_cannot_be_read`
  (replace the accepted target with a directory → `unresolvable`, revert refused
  with the named error, status unchanged). Mutation: dropping the fail-closed
  branch fails it.

### P3-UX-API-CONTRACT-006 — stale `revert --help`
- Status: **fixed**
- Files: `cli/__init__.py`
- What changed: the docstring is rewritten around managed-artifact liveness using
  the same missing/orphaned vs live/unresolvable vocabulary as the spec and ADR,
  and states explicitly that editing the file — inside the block or around it —
  does not make it revertable.

## Additional Changes

- ADR-0020 D43/D44, spec §12.7 (prose + edge-case table) and known-gaps G2 updated
  to the refined boundary (three fail-closed rules; both-sides preview binding).
- `emitters.target_path` / `managed_artifact_live` were replaced by
  `candidate_target_paths` / `text_holds_managed_artifact` (multi-location).

## Tests / Checks
- Command: `make ci`
- Result: **1282 passed, 1 skipped**, coverage 91.9%, all 5 stages green.
- Notes: four new guards mutation-verified (CRLF normalization, multi-root
  resolution, proposal re-check, OSError fail-closed).

## Ready For Re-Review
- Artifact / commit: the new follow-up commit on `feat/webui-phase1-suggestions`
  (delta from `0a840a6`).
- Specific scope: whether any remaining input class makes liveness read a live
  artifact as gone (or vice versa); the both-sides preview binding; and whether
  the fail-closed `unresolvable` cases are correctly scoped rather than blocking
  legitimate repairs.

---

## Round 4 review  _(Reviewer — Codex, 2026-07-24)_

Reviewed `git diff 0a840a6..671243b` @ `671243b` (HEAD verified twice). `make ci`
(1282 passed) + three new temp-dir reproductions. Verdict: **BLOCKED**.
**P3-UX-API-CONTRACT-006 — fixed.** F1, P1-DATA-INTEGRITY-004 and
P2-CORRECTNESS-005 all **partially fixed**:

- **F1** — `_owned_skill` still required exact `---\n` bytes. A live owned skill
  converted to **CR-only**, prefixed with a **UTF-8 BOM**, or given **whitespace
  after the fence** kept both ownership keys yet classified `orphaned` and
  reverted, leaving the skill installed.
- **P1-004** — the guard compared only `status` + `extract_draft`. Changing only
  `candidate_type` (valid→valid, which a concurrent `recommend run` refresh does)
  changed the render while the compared fields matched, and the acceptance was
  recorded. Also flagged: `accept_proposal` reloads independently *after* the
  guard, so even a complete field comparison would not bind the checked version.
- **P2-005** — `read_target_text` decodes UTF-8, so invalid bytes raise
  `UnicodeDecodeError`, not `OSError`, and still escaped the classifier.
- Codex also noted ADR-0020/spec/G2's global "fail closed" / "both sides" /
  "closed structurally" claims were **premature**.

---

## Round 5 — author response  _(Author — Claude, 2026-07-24)_

# Claude Revision Response

## Summary

**Naming the pattern first: three rounds running, my fixes hit Codex's specific
reproduction rather than the rule behind it** — the exact trap this repo already
recorded from the store-lock relay ("a repeated finding means the fix hit the
example, not the cause"). Andrew's call was to fix the causes. So this round
changes *what question is asked*, not which instance is handled:

| Finding | Instance-level (what I kept doing) | Cause-level (this round) |
|---|---|---|
| F1 | normalize the newline form du jour | **stop parsing frontmatter for liveness** — a skill is live iff its file exists |
| P1-004 | add the next field to the comparison | **compare the re-render**, and bind the checked doc to the write |
| P2-005 | catch the next exception type | catch **any** read failure (`OSError` + `UnicodeError`) |

## Findings Addressed

### F1 — liveness false negatives across text representations
- Status: **fixed (at the cause)**
- Files: `emitters.py` (`candidate_is_live` replaces `text_holds_managed_artifact`;
  `_owned_skill` rewritten), `proposals.py`
- What changed: **skill liveness is existence.** A file at the canonical
  `<...>/skills/<slug>/SKILL.md` is live; its contents are never inspected. Every
  round found another representation preserving the ownership values while
  defeating the parser, so the parse is no longer asked. Rules already used a
  plain marker substring, immune to newline form and encoding markers.
  `_owned_skill` still exists for the accept flow's `foreign` warning and D44's
  exclusion — where a false negative is merely conservative — and was rewritten to
  parse by logical lines (BOM skipped, LF/CRLF/CR all handled, whitespace-tolerant
  fences), so it is correct in its own right without being load-bearing.
- Trade-off, documented in ADR/spec: a skill whose contents the user *replaced* is
  non-revertable until they delete the file — the same "remove it by hand first"
  rule revert already states, erring toward never orphaning.
- Verification: `test_revert_refuses_a_live_skill_under_any_text_representation`
  parametrized over **crlf / cr-only / utf8-bom / fence-whitespace** — real CLI
  skill accept (`--target project`), mangle, revert must exit 1, reject stays
  blocked, status `accepted`, file present. Mutation: restoring parser-based skill
  liveness fails all four plus the round-3 CRLF test.

### P1-DATA-INTEGRITY-004 — incomplete binding of render-relevant state
- Status: **fixed (at the cause)**
- Files: `install.py` (`record_acceptance`, `_scope_of`), `proposals.py`
  (`accept_proposal(expect=…)`, `ProposalChangedError`)
- What changed: the guard no longer compares fields. It **reloads and re-renders**
  the current proposal and requires an identical artifact identity — `path`,
  `target`, `after`, `foreign`. That covers draft, `type`, `target`, `project`,
  `candidate_type` and the active redaction config at once, because it asks the
  actual question ("would this still render to what is installed?"). The validated
  document is then **bound to the write** via `accept_proposal(expect=current)`,
  which refuses (`ProposalChangedError` → `StaleProposalError`) if its own reload
  sees anything different — closing the check-then-write window Codex identified.
  Stated honestly in the ADR: a defensive equality guard, not a lock; true
  serialization awaits ADR-0017's write lock (separate branch).
- Verification: `test_record_only_refuses_if_only_candidate_type_changes_during_the_prompt`
  (real CLI, `candidate_type` changed inside `typer.confirm`),
  `test_record_acceptance_binds_the_checked_document_to_the_write` (deterministic
  mutation injected *between* the guard and the write via a patched
  `emitters.prepare`), `test_accept_proposal_expect_guard_refuses_a_changed_document`.
  Mutations: reverting to the field-subset comparison, and dropping `expect=`,
  each fail their tests.

### P2-CORRECTNESS-005 — non-OSError read failures
- Status: **fixed (at the cause)**
- Files: `proposals.py` (`artifact_state`)
- What changed: the per-candidate catch is `(OSError, UnicodeError)`, with the
  rule written down as "could not read it", not "raised a particular exception".
- Verification: `test_artifact_state_is_unresolvable_when_a_candidate_is_not_valid_utf8`.
  Mutation: narrowing back to `OSError` fails it.

### P3-UX-API-CONTRACT-006
- Status: **fixed** in round 4, confirmed by the reviewer.

## Additional Changes

- ADR-0020 D43/D44, spec §12.7 (prose + edge-case table) and known-gaps G2 updated
  to the cause-level boundary, and — per Codex's note — the global "fail closed" /
  "closed structurally" claims **scoped honestly**: the ADR now says what is and
  is not claimed (no exhaustiveness proof; no concurrent-writer safety).

## Tests / Checks
- Command: `make ci`
- Result: **1290 passed, 1 skipped**, all 5 stages green.
- Notes: four mutations verified (skill-liveness-by-parsing, field-subset
  comparison, `expect=` removal, `OSError`-only catch).

## Ready For Re-Review
- Artifact / commit: new follow-up commit on `feat/webui-phase1-suggestions`
  (delta from `671243b`).
- Specific scope: whether the *cause-level* reframing holds — is there any input
  class left where liveness misreads a live artifact, given it now rests on file
  existence and a marker substring rather than parsing? Does the re-render
  comparison miss any render-relevant state? Is the existence trade-off (a
  replaced skill is non-revertable) acceptable and correctly documented?

---

## Round 5 review  _(Reviewer — Codex, 2026-07-24)_

Reviewed `git diff 671243b..447c29b` @ `447c29b` (HEAD verified three times).
8 focused regressions + `make ci` (1290) + filesystem probes (symlink, broken
symlink, skills dir without SKILL.md, invalid UTF-8, a `--target project`
override on a `user-skill` proposal) + rule-marker probes. Verdict: **BLOCKED**.

- ✅ **P1-DATA-INTEGRITY-004 — fixed.** The re-render comparison covers the
  artifact identity; `_scope_of` verified under a scope override; `expect=` full
  document equality judged "appropriately conservative" and confirmed to close the
  independently-reloaded-document gap.
- ✅ **P2-CORRECTNESS-005 — fixed.** ✅ **P3-UX-API-CONTRACT-006 — fixed.**
- The skill-existence trade-off was reviewed and accepted (symlinks classify live,
  broken symlinks/dir-without-SKILL.md classify missing, a directory is
  unresolvable; the documented escape is to delete the file).
- ❌ **F1 — partially fixed (rule half).** `candidate_is_live` searched
  `_rule_start_marker(slug)` — the *entire* generated opening comment. Changing
  only `generated by` → `generated  by` left the slug sentinel and the generated
  rule body intact but missed the full-string match: real CLI `revert --yes` then
  `reject` both exited 0, ending `rejected` with the behavior live. Codex was
  explicit this is **not** another frontmatter instance and does not reject the
  cause-level reframing — the rule half simply did not yet ask the question the
  code, ADR and spec all said it asked.

---

## Round 6 — author response  _(Author — Claude, 2026-07-24)_

# Claude Revision Response

## Summary

One finding, fixed as the reviewer specified: rule liveness now keys on a
**defined, stable opening sentinel** instead of the whole generated comment — and
the same sentinel is used for the *write*, so the two can never diverge again.
The gap was mine: the code did not do what its own docstring, ADR-0020 and spec
§12.7 all said it did.

## Findings Addressed

### F1 — rule liveness depended on the marker's explanatory prose
- Status: **fixed**
- Files: `emitters.py` (`rule_sentinel`, `_rule_start_marker`, `candidate_is_live`,
  `_rule`), `cli/__init__.py` (help text), ADR-0020 D43, spec §12.7, known-gaps G2
- What changed: new `emitters.rule_sentinel(slug)` defines the identity token once
  — `<!--` + optional whitespace + `neurobase:rule:<slug>`, with a `(?![a-z0-9-])`
  boundary. It tolerates whitespace after `<!--` and **anything** after the slug
  (so re-wrapping or rewording the parenthetical is irrelevant), cannot match a
  longer slug (`prefer-uv` must not find `prefer-uv-run`), and cannot match the
  closing `<!-- /neurobase:rule:…` marker because `\s*` does not span the `/` — so
  a lone closing marker is not liveness, exactly as required.
  `candidate_is_live` uses it. `_rule_start_marker` still returns the full comment
  and is still what we **write** — tolerant detection, canonical output.
- **Collateral change, disclosed:** `_rule`'s block-splicing now locates the
  existing block with the *same* sentinel (`finditer`, first match; the closing
  marker and all duplicate/misorder validation unchanged). Without this the write
  and the liveness verdict would disagree — a block with a hand-edited
  parenthetical would read `live` but a re-accept would append a duplicate and the
  next accept would hard-error on duplicate markers. Now it is replaced in place.
- Verification: `test_revert_refuses_when_only_the_marker_suffix_changed`
  parametrized over **double-space / reworded-suffix / wrapped-comment /
  dropped-suffix** (real CLI: revert exits 1, reject stays blocked, status
  `accepted`, rule body still present); `test_revert_allows_it_once_the_sentinel_itself_is_gone`
  (sentinel removed, closing marker deliberately left behind → revert succeeds);
  `test_rule_sentinel_matches_regardless_of_the_explanatory_suffix`;
  `test_rule_sentinel_does_not_match_a_longer_slug`;
  `test_rule_sentinel_never_matches_the_closing_marker`;
  `test_reaccept_replaces_a_block_whose_marker_suffix_was_edited`.
  Mutations: restoring full-comment liveness fails all four CLI cases; dropping
  the slug boundary fails the longer-slug test; reverting the splice to the
  full-string match fails the replace-in-place test.

## Additional Changes

- Two reviewer accuracy nits taken: ADR/spec now say a skill's **frontmatter is
  never parsed** (rather than "contents are not inspected" — the classifier does
  open and decode candidates, since a read failure is itself a verdict), and
  `recommend revert --help` now says "no SKILL.md remains at the canonical skills
  path" rather than "owned SKILL.md", matching the existence predicate literally.
- ADR-0020 D43's opening summary corrected to the sentinel/existence wording (it
  still described the superseded frontmatter-ownership rule).

## Tests / Checks
- Command: `make ci`
- Result: **1299 passed, 1 skipped**, all 5 stages green.
- Notes: three mutations verified.

## Ready For Re-Review
- Artifact / commit: new follow-up commit on `feat/webui-phase1-suggestions`
  (delta from `447c29b`).
- Specific scope: the sentinel grammar (is the identity token right, and is the
  boundary guard correct?), the write/liveness consistency in `_rule` including
  its duplicate/misorder validation, and whether ADR/spec/help now match the code.

---

## Round 6 review  _(Reviewer — Codex, 2026-07-24)_ — ✅ APPROVED WITH NITS

Reviewed `git diff 447c29b..c3ad694` @ `c3ad694` (HEAD verified twice; repo left
clean). 19 focused regressions + an adversarial sentinel matrix + the real CLI
`generated  by` reproduction + `make ci` (1299 passed, 91.87%).

**Prior findings: F1 fixed · P1-DATA-INTEGRITY-004 fixed · P2-CORRECTNESS-005
fixed · P3-UX-API-CONTRACT-006 fixed. No blocking findings remain.**

Reviewer's verification of the sentinel: it escapes the slug, is case-sensitive
for the identity token, tolerates whitespace only where specified, excludes longer
valid slugs via `(?![a-z0-9-])`, and cannot match the slash-prefixed closing
marker; case changes or a newline inside `neurobase:rule:<slug>` correctly do not
match. The `_rule` splice keeps its fail-closed pair validation under the
variable-length match (two sentinels, duplicate closes, lone markers and reversed
order all raise before mutation), a suffix-edited pair is replaced in place with
canonical output, and another slug's block is preserved.

**Open, optional, non-blocking — P3-DOCS-PLAN-ACCURACY-007:** stale "owned
SKILL.md" / "marker-ownership" shorthand remains in a few *summary* lines
(spec §12.7 command-table row, known-gaps G2, an ADR-0020 consequence) and in two
`proposals.py` docstrings, while the detailed text is correct. Documentation only;
the reviewer states it needs no further review round.

## Relay outcome

**6 rounds; APPROVED WITH NITS.** Findings per round: 4 → 4 → 4 → 1 → 1 → 0
blocking.

The durable lesson, learned the expensive way in rounds 3–5: **a repeated finding
means the fix hit the example, not the cause.** Three consecutive rounds patched
the specific reproduction — normalize *this* newline form, compare *this* extra
field, catch *this* exception type — and each time the reviewer found the next
member of the same class. It only converged once each was reframed to ask the
real question: liveness by **existence/sentinel** rather than parsing; the
**re-render** rather than a field subset; **"could not read it"** rather than a
named exception. This is the same trap recorded from the store-lock relay, and it
cost three rounds again.
