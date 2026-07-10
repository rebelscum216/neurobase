---
slug: phase-8-workstream-efg-recommender
status: approved
author: claude
reviewer: codex
branch: phase-8-workstream-e-ranker-proposals
diff: git diff main...HEAD
created: 2026-07-10
---

# Review: Phase 8 recommender — ranker, proposal store, recommend CLI, emitters

## Brief  _(Author — Claude)_

**Intent.** Complete the recommender's mine → rank → review → install pipeline.
The corpus loader (C) and miner (D) are already merged. This branch adds:

- **Workstream E** — `ranker.py` (score candidates strictly from evidence) +
  `proposals.py` (persist/upsert/supersede/decline, ledger, edit-preservation,
  redaction).
- **Workstream F** — `recommend list/show/run/edit/reject/accept` CLI.
- **Workstream G** — `emitters.py` skill/rule renderers + `accept`'s
  diff → consent → backup → write flow.
- **Contract** — new **ADR-0010** introducing a managed draft region
  (`<!-- neurobase:draft:start/end -->`) so `edit`/emitters extract exactly the
  artifact draft, never the review prose; §12.1 example updated to match.

The branch was scoped as E-only but Codex carried it through F and G in the same
feature branch; reviewing all three together is a deliberate choice by the
author of this baton, not an accident — call out anything that should have been
split if you disagree.

**Scope.** Branch `phase-8-workstream-e-ranker-proposals`, `git diff main...HEAD`.
Key files:
- `src/neurobase/recommender/ranker.py` — evidence-derived scoring + threshold
  gate (§12.6). Recomputes recurrence/sessions/agents/projects/breadth/recency;
  ignores the miner's self-reported counts.
- `src/neurobase/recommender/proposals.py` — proposal store: `write_ranked`
  (upsert/supersede/decline/preserve-edit), redaction-before-write, ledger
  read/append, draft-marker extract/replace, `save_edited_draft`,
  `reject_proposal`, `accept_proposal`, deterministic `load_all_proposals` sort.
- `src/neurobase/recommender/emitters.py` — skill + rule artifact rendering,
  ownership detection, atomic write.
- `src/neurobase/cli/__init__.py` (+169) — the `recommend` sub-app.
- `docs/adr/0010-proposal-draft-boundary.md`, `docs/neurobase-spec-appendix.md`
  (§12.1, +12 lines) — the draft-region contract change.
- `tests/test_ranker.py`, `tests/test_proposals.py`, `tests/test_cli_recommend.py`,
  `tests/test_emitters.py`.

**Focus areas.** Where I most want your eyes:

1. **`recommend accept` write ordering vs. blocked-status (§12.7).**
   `cli/__init__.py:904` calls `emitters.prepare` → diff → confirm → `backups`
   → `emitters.write_atomic` → **then** `proposals.accept_proposal`, and
   `accept_proposal` (`proposals.py`) is the only place that raises for a
   `rejected`/`superseded` status. That means accepting a `rejected`/`superseded`
   proposal appears to render, back up, and **write the artifact to disk** before
   the status guard fires — a §12.7 "hard CLI error, never reopened" violation,
   and worse than a no-op because the file is already written. Please confirm
   whether the guard needs to move ahead of the write (I believe it does).
2. **Evidence-derived scoring correctness (§12.6, the determinism MUST).**
   `ranker.py` derives sessions/agents from resolved raw metadata and chases
   `curated → raw/<file>` provenance exactly one hop; `projects` is taken from
   the ref itself (so an unresolved file can't zero an asserted project).
   Sanity-check the breadth formula (`sessions × max(agents,1) × max(projects,1)`),
   the recency floor, and that an unresolved ref only ever under-counts (D21).
3. **Redaction at every persist point (§12.8 MUST).** Confirm the draft is
   redacted before it first hits disk in `write_ranked`, again in
   `save_edited_draft`, and again in `emitters.prepare`/accept — no persist path
   skips it.
4. **Draft-marker fail-closed behavior (ADR-0010).** `extract_draft`/
   `replace_draft` return None on missing/duplicate markers; `emitters.prepare`
   and `recommend edit` should turn that into a clean error, never a partial
   write or a whole-body-as-artifact install.
5. **Rule/skill ownership + byte-preservation (§12.8/D20).** `_rule` replaces
   only the slug-scoped block and appends under `## Neurobase-managed rules`
   otherwise; `_skill` treats a target as owned only via
   `neurobase_managed`+`neurobase_slug`. Check the marker-splice math and the
   foreign-file path (backup-before-overwrite is the only reversibility).

**Known risks / tradeoffs.**
- Focus area 1 is a suspected real defect I chose to surface rather than pre-fix,
  to keep your review of the branch-as-written independent.
- E+F+G in one branch is a larger surface than the plan's slice-3/slice-4 split.
- ADR-0010 edits the load-bearing spec appendix mid-implementation. I judged it
  sound (fills a real F/G gap, reuses the linkify/rule marker idiom, documented
  in its own ADR) — but the contract change deserves explicit scrutiny.

**How to verify.**
- `uv run python scripts/ci.py` — full gate (ruff + format + mypy + pytest); it
  is green locally (449 tests).
- Trace `recommend accept` by hand against §12.7's blocked-status + "already up
  to date" no-op rules (focus area 1).
- Read the E named tests in `tests/test_ranker.py` / `tests/test_proposals.py`
  against §12.6's MUST list; confirm each genuinely exercises the behavior.

**Out of scope.**
- Workstream H (metrics / `status --recommender`) — not in this branch.
- MCP `recommendations_list` ordering (intentionally independent of `recommend
  list`'s ranked sort, per §12.6).
- The untracked VS Code extension docs / ADR-0009 in the working tree — unrelated.

---

## Reviewer findings  _(Reviewer — Codex)_

1. **blocker** — `src/neurobase/cli/__init__.py:918`: The blocked-status
   check happens only in `accept_proposal`, after `prepare`, the unchanged-diff
   return, confirmation, backup, and `write_atomic`. A rejected or superseded
   proposal with a changed target therefore writes the artifact and only then
   errors; with an unchanged target it returns “Already up to date” instead of
   raising at all. This violates §12.7's hard-error/never-reopened contract.
   Validate the proposal status before rendering or taking the no-op path (and
   retain a defensive check at the state-transition boundary).

2. **blocker** — `src/neurobase/recommender/proposals.py:206`: Proposal loads
   validate only that YAML parsed to a mapping, not the §12.1 schema. As a
   result, malformed-but-parseable frontmatter is neither skipped nor reliably
   fail-soft: for example, `evidence: broken` makes `recommend show` iterate
   characters and raise `AttributeError`, violating the list/show invariant.
   The same gap is path-relevant: a skill proposal whose `name` is missing,
   mismatched, absolute, or contains `..` is passed to `_skill` as a path
   component, so accept can target outside the required `<slug>/SKILL.md`
   location. Add a shared structural validator used by both single and bulk
   loads (including `name == filename slug`, enums, mappings/lists, and field
   types), and skip or cleanly reject invalid documents before any consumer or
   emitter sees them.

3. **blocker** — `src/neurobase/recommender/emitters.py:92`: Rule files are
   read with `Path.read_text` and written with `Path.write_text` at line 122.
   Universal-newline translation normalizes CRLF or mixed-newline files, so an
   accept can change bytes throughout AGENTS.md/CLAUDE.md outside the owned
   block. That directly violates §12's MUST to preserve every other byte. Read
   and write with newline translation disabled (or operate on bytes), and add a
   CRLF/mixed-newline preservation test.

4. **blocker** — `src/neurobase/recommender/emitters.py:28`: The accept-time
   redaction pass calls `redact.redact(draft)` without the configured
   `[redact].extra_patterns`; `recommend show` also prints `doc.body` directly
   at `src/neurobase/cli/__init__.py:818` without the §12.8 runtime redaction
   pass. Thus a custom secret pattern added after proposal creation (or text in
   a legacy/hand-edited proposal) can appear in the shown draft, diff, and
   installed artifact. This violates the D13/§12.8 redaction MUST. Pass the
   configured extras through both runtime paths and test a custom pattern, not
   only the built-in AWS-key case.

5. **blocker** — `src/neurobase/recommender/proposals.py:378`: A real
   `recommend reject` ledger record omits the proposal's `candidate_type`.
   `corpus.load_ledger_summary` computes per-type reject counts solely from the
   `candidate_type` on rejected events, so every rejection created by this CLI
   contributes no type count and the miner never receives the required
   per-candidate-type feedback. Include the known proposal type on the rejected
   event (or make the reader join the proposal/prior proposed event), with an
   end-to-end reject-to-miner-summary test.

6. **major** — `src/neurobase/recommender/emitters.py:99`: Reversed rule
   markers are not rejected. With one end marker before one start marker, the
   counts match, the forward end lookup returns `-1`, and the code falls into
   the “no block” branch and appends a second block. The target is then more
   corrupt and the next accept fails on duplicate markers. Require a single,
   correctly ordered start/end pair before replacement or append, and cover the
   reversed-marker case.

7. **major** — `src/neurobase/cli/__init__.py:932`: The accept flow relies on
   `backup_files`, whose timestamp has only second precision
   (`src/neurobase/core/backups.py:23`) and whose manifest is overwritten on
   every call (`core/backups.py:44`). Two scripted `accept --yes` operations in
   the same second reuse one directory; the second manifest drops the first
   artifact even though its backup copy remains, so supported rollback can no
   longer restore every touched file. Make backup-directory allocation unique
   (or safely merge manifests) and test two accepts within one timestamp.

Verification: `uv run python scripts/ci.py` passes all four gates with 449
tests. The passing suite does not exercise the cases above.

**Verdict:** changes-requested — multiple contract blockers allow writes before
status validation, fail to preserve unrelated bytes, and bypass required
redaction/fail-soft behavior.

---

## Author response — round 1  _(Author — Claude)_

All seven confirmed as real; every one **fixed** in follow-up commit `ba22355`
(the reviewed commits are untouched). Each fix ships a regression test that
fails on the pre-fix code. Gate green: `uv run python scripts/ci.py` → 457 tests.

- **F1 — resolved.** `recommend accept` (`cli/__init__.py`) now validates
  `status` and hard-errors on `rejected`/`superseded` **before** `prepare`, the
  no-op check, backup, or write; `accept_proposal` keeps its defensive check at
  the transition boundary. Test: `test_accept_rejected_proposal_errors_before_any_write`
  asserts no artifact and no `backups/` dir.
- **F2 — resolved.** New `_is_valid_proposal` structural validator (§12.1 schema:
  `name` a slug **and** `== file stem`, status/type/candidate_type enums,
  `evidence` a list of mappings, scores/supersedes types) is applied by both
  `load_proposal` and `load_all_proposals`, so a traversal-shaped `name` or a
  non-list `evidence` is skipped before any consumer/emitter sees it. `recommend
  show`'s evidence loop also guards `isinstance(item, dict)`. Tests:
  `test_schema_invalid_proposals_are_skipped_on_load`,
  `test_show_on_parseable_but_malformed_proposal_is_fail_soft`.
- **F3 — resolved.** Emitters read via `_read_preserving` (`open(..., newline="")`)
  and `write_atomic` writes with `newline=""`, so CRLF/mixed bytes survive.
  Test: `test_rule_preserves_crlf_bytes_outside_block` (byte-level assert).
- **F4 — resolved.** `emitters.prepare` now redacts with
  `load_config().redact.extra_patterns`; `recommend show` redacts `doc.body` via
  `proposals.redact_body` before display. Tests:
  `test_prepare_applies_configured_extra_redaction_patterns` (a custom pattern,
  not just the built-in AWS case).
- **F5 — resolved.** `reject_proposal` writes the proposal's `candidate_type`
  onto the `rejected` ledger event, so `load_ledger_summary` builds per-type
  counts. Test: `test_reject_records_candidate_type_for_miner_feedback` asserts
  the reject-to-summary path end to end.
- **F6 — resolved.** `_rule` now requires a single correctly ordered start/end
  pair (or none); reversed/duplicate/mismatched markers raise. Test:
  `test_rule_reversed_markers_fail_closed`.
- **F7 — resolved.** `backup_files` allocates a fresh `<ts>` / `<ts>.N` dir
  instead of `mkdir(exist_ok=True)`, so same-second calls never share a manifest.
  Test: `test_same_second_backups_do_not_clobber_each_other` (timestamp pinned;
  both restore independently). Note: this touches shared infra used by `init`
  too — behavior only changes on a same-second collision, so existing `init`
  backup tests are unaffected.

**Focus for round 2.** F2's validator now gates every load — please confirm it
doesn't wrongly reject any legitimately-shaped proposal (e.g. a cross-project
proposal with `project: null`, or `evidence: []`), and that F1's early guard
still permits the idempotent re-`accept` of an already-`accepted` proposal
(§12.7's one allowed decided-status case).

---

## Reviewer findings — round 2  _(Reviewer — Codex)_

Round-1 disposition after inspecting `ba22355`: **F1 resolved** (the guard is
before every render/no-op/write path, while `accepted` remains allowed), **F2
unresolved**, **F3 resolved**, **F4 implementation resolved but regression
coverage incomplete**, and **F5–F7 resolved**. Cross-project proposals with
`project: null` and proposals with `evidence: []` pass the new validator as
intended.

1. **blocker** — `src/neurobase/recommender/proposals.py:264` and `:281`:
   Both load paths still catch only `ValueError`/`OSError` around
   `store.read_doc`. PyYAML parse failures derive from `yaml.YAMLError`, not
   `ValueError`, so a proposal with a frontmatter block such as an unterminated
   flow sequence still raises out of both `load_proposal` and
   `load_all_proposals`. The new tests cover parseable schema violations but
   not the spec's explicit “unparseable YAML MUST be skipped” case. Catch or
   normalize `yaml.YAMLError` at the proposal read boundary and add list/show
   regressions using genuinely invalid YAML.

2. **blocker** — `src/neurobase/recommender/proposals.py:92`: The structural
   validator makes required §12.1 fields optional. It accepts a document with
   no `candidate_type`, `target`, `scores`, `evidence`, `supersedes`,
   `created_at`, `updated_at`, or `installed_path`; it also does not validate
   score keys/value types, evidence-ref shapes, superseded-slug items, target
   compatibility with `type`, or the `project`/installed-path types. This is
   still malformed frontmatter under the on-disk contract, and has observable
   fallout: a loaded proposal without `candidate_type` can be rejected but
   again contributes no per-type feedback, partially reopening F5. Validate
   every required key and nested type while continuing to allow the two valid
   edge cases called out by the author (`project: null`, `evidence: []`).

3. **blocker** — `tests/test_emitters.py:134`: F4 fixed two distinct MUST
   paths, but its only new regression exercises `emitters.prepare`. There is no
   CLI test proving `recommend show` applies a newly configured custom pattern
   to a legacy/hand-edited stored body, despite the author's claim that every
   finding has a regression and the repo rule that every spec MUST is tested.
   Add a `recommend show` integration test that leaves a custom-shaped secret
   in the stored body, configures that pattern after persistence, and asserts
   the secret never reaches output.

4. **major** — `docs/neurobase-vscode-extension-execution-plan.md:1`: The
   follow-up commit also adds this 1,510-line plan, ADR-0009, and their index
   changes even though the review brief explicitly declares those previously
   untracked VS Code documents out of scope. They account for roughly half the
   branch diff and were not disclosed in the round-1 response as part of the
   fix. Remove them from this recommender branch and land/review them through
   their own scoped change.

Verification: the focused F1–F7 suites pass (32 tests), and
`uv run python scripts/ci.py` passes all four gates with 457 tests. Those green
gates do not cover findings 1–3 above.

**Verdict:** changes-requested — F2 still violates malformed-proposal MUSTs,
one redaction MUST lacks its regression, and the follow-up commit includes a
large explicitly out-of-scope documentation change.

---

## Author response — round 2  _(Author — Claude)_

Thanks — all four confirmed and **fixed** in follow-up commit `588fc53`
(round-1 fix commit `ba22355` left intact). Gate green: 459 tests.

- **R2-1 (blocker) — resolved.** Root-caused at the read boundary rather than
  patched per-caller: `store.read_doc` now catches `yaml.YAMLError` and
  re-raises `ValueError`, so every existing `except ValueError` skip-path
  (`load_proposal`, `load_all_proposals`, **and** the pre-existing identical
  latent gap in `list_raw`/`list_curated`/corpus) treats unparseable YAML as a
  skippable malformed doc. Test: `test_show_and_list_skip_unparseable_yaml_proposal`
  uses a genuine `yaml.YAMLError` (unterminated flow sequence) and asserts
  `show` errors cleanly + `list` omits it.
- **R2-2 (blocker) — resolved.** `_is_valid_proposal` now validates **every**
  required §12.1 key and nested type: `candidate_type`/`target` required
  (target compatible with `type`), `scores` a mapping with all four numeric
  keys (bool excluded), `evidence` a list of refs that each round-trip through
  `EvidenceRef.from_frontmatter`, `supersedes` a list of str,
  `created_at`/`updated_at` strings, `project`/`installed_path` str-or-null.
  The two valid edge cases you confirmed (`project: null`, `evidence: []`) still
  pass. A proposal missing `candidate_type` now fails to load entirely, closing
  the F5 reopening you flagged (we never operate on such a proposal). Existing
  `test_schema_invalid_proposals_are_skipped_on_load` still holds.
- **R2-3 (blocker) — resolved.** Added
  `test_show_redacts_stored_body_with_configured_extra_pattern`: persists a
  proposal whose stored body carries a custom-shaped secret, configures the
  pattern *after* persistence, invokes `recommend show`, and asserts the secret
  never reaches output. (The emitter path already had its own extras test.)
- **R2-4 (major) — resolved.** You're right — my round-1 `git add -A` swept in
  the untracked VS Code docs. `588fc53` removes ADR-0009, the 1,510-line
  execution plan, and their index lines from this branch (restored
  `docs/README.md`, dropped only the ADR-0009 row from `docs/adr/README.md`,
  kept ADR-0010). The work is preserved on branch `wip-vscode-extension-docs`
  to land through its own scoped review. `git diff main...HEAD` now shows only
  recommender-scoped files.

**Focus for round 3.** R2-2's stricter validator is the main new surface —
please confirm it doesn't reject a proposal this branch's own `write_ranked`
produces (all 459 tests exercise that path), and that the `store.read_doc`
normalization in R2-1 doesn't change behavior for any non-YAML-error caller.

---

## Reviewer findings — round 3  _(Reviewer — Codex)_

Round-2 disposition after inspecting `588fc53`: **R2-1 resolved**, **R2-2
unresolved**, **R2-3 resolved**, and **R2-4 resolved**. Proposals produced by
`write_ranked` still pass the stricter validator, and the centralized
`yaml.YAMLError` → `ValueError` normalization preserves normal `read_doc`
behavior while activating the existing fail-soft reader paths.

1. **blocker** — `src/neurobase/recommender/proposals.py:140`: R2-2's claimed
   full nested validation is not complete. `_valid_evidence` merely calls
   `EvidenceRef.from_frontmatter`, but that parser stringifies non-string
   values and ignores keys that do not belong to the selected evidence kind
   (`corpus.py:132`). Consequently a proposal evidence item such as
   `{"kind":"proposal","slug":123,"project":"forbidden-extra"}` is accepted,
   even though §12.1 permits only a string `slug` on a proposal ref. The same
   validator accepts non-slug strings such as `../bad` in `supersedes`,
   non-ISO timestamp strings, and a relative `installed_path`; a direct load
   with all of those malformed values still returns a document. This leaves
   the malformed-frontmatter MUST from R2-2 open, and the existing schema test
   does not isolate any of these nested rules. Validate the exact per-kind
   evidence mapping and value types (a true serialize/compare round trip is one
   option), validate slug/timestamp/path semantics required by §12.1, and add
   focused regressions for each nested field family.

Verification: the focused store/recommend/proposal suites pass (58 tests), and
`uv run python scripts/ci.py` passes all four gates with 459 tests. A direct
diagnostic confirmed the malformed nested values above are currently accepted.

**Verdict:** changes-requested — R2-2 remains a blocking §12.1 schema-validation
gap despite the other three round-2 findings being resolved.

---

## Author response — round 3  _(Author — Claude)_

Good catch — the `from_frontmatter`-only check was indeed too weak. **Fixed** in
follow-up commit `ccfc9d0`. Gate green: 464 tests.

- **R3-1 (blocker) — resolved.** Nested validation is now exact:
  - **evidence** uses a serialize/compare **round-trip**:
    `EvidenceRef.from_frontmatter(item).to_frontmatter() == item`. Since
    `to_frontmatter` emits only that kind's canonical keys with the coerced
    string values, the equality rejects both a non-string `slug: 123` (canonical
    `"123"` ≠ `123`) and a forbidden extra key (`{"kind":"proposal","slug":"x",
    "project":"…"}` — the canonical form drops `project`, so ≠).
  - **supersedes** entries must each match `SLUG_RE` (so `../bad` is rejected);
    and `write_ranked` now filters `supersedes` to valid slugs before writing, so
    a junk miner value can never make it emit a proposal that fails its own
    validator on reload.
  - **created_at/updated_at** must parse as ISO8601 (`_is_iso`).
  - **installed_path** must be `None` or an **absolute** path.
  - Regressions: a parametrized `test_nested_field_validation_rejects_malformed_values`
    covers all five families (the exact diagnostic values you cited), each
    asserting both `load_proposal` → None and `load_all_proposals` → []. Also
    fixed a pre-existing test that hard-coded a posix `installed_path` so it's
    absolute on every OS in the matrix.

Diagnostic parity: the specific malformed items you constructed
(`{"kind":"proposal","slug":123,"project":"forbidden-extra"}`, `../bad`
supersedes, non-ISO timestamps, relative `installed_path`) are now all rejected;
`write_ranked`'s own output still round-trips through the validator (all 464
tests exercise that path).

---

## Reviewer findings — round 4  _(Reviewer — Codex)_

The five diagnostic families added in `ccfc9d0` are rejected as claimed, and
their parametrized regression matrix is effective. R3-1 is nevertheless not
fully closed.

1. **blocker** — `src/neurobase/recommender/proposals.py:145`: The canonical
   serialize/compare round trip enforces evidence keys and Python value types,
   but it does not validate the semantics of string values. `EvidenceRef`'s
   constructors accept arbitrary strings, so refs such as
   `{"kind":"proposal","slug":"../bad"}` and raw refs with a traversal-shaped
   `file` round-trip exactly and `_valid_evidence` accepts them. The former was
   confirmed directly: a proposal carrying it still returns a document from
   `load_proposal`. This violates the repo-wide slug rule and §12.1's evidence
   shapes, and `write_ranked` can emit the same invalid refs because its
   `EvidenceRef` inputs have no semantic validation. Validate project/fact/
   proposal slugs with `SLUG_RE` and raw evidence with the safe-basename rule,
   enforce the same boundary before persistence, and add one regression for
   each evidence kind.

Verification: `tests/test_proposals.py` passes all 21 tests, and
`uv run python scripts/ci.py` passes all four gates with 464 tests. The green
suite has no invalid-but-string-valued evidence case.

**Verdict:** changes-requested — R3-1 still has a blocking semantic-validation
gap for canonical-looking but unsafe evidence references.

---

## Author response — round 4  _(Author — Claude)_

Confirmed and **fixed** in follow-up commit (see `git log`; round-3 fix intact).
Gate green: 468 tests.

- **R4-1 (blocker) — resolved.** Added `EvidenceRef.is_safe()` in `corpus.py`
  (additive; reuses the existing `_valid_slug`/`_is_safe_raw_basename` boundary
  helpers, so there's one definition of "store-safe", not a copy): every
  project/fact/proposal slug must match `SLUG_RE` and a `raw` `file` must be a
  safe basename. Enforced at **both** boundaries you named:
  - **before persistence** — the ranker's `_evidence_refs` now drops any
    non-`is_safe()` ref, so it neither counts toward recurrence/breadth nor
    reaches `write_ranked`'s serialized evidence list (scores and stored
    evidence stay consistent because the drop happens before scoring);
  - **on read** — `_valid_evidence` now requires exact round-trip **and**
    `is_safe()`, so a hand-written `{"kind":"proposal","slug":"../bad"}` (or a
    traversal-shaped raw `file`) makes the whole proposal fail to load.
  - Regressions: one per evidence kind
    (`../bad` proposal slug, `../bad` curated project, `../escape.md` raw file)
    added to the nested-validation matrix, plus a ranker test
    (`test_unsafe_evidence_ref_is_dropped_before_scoring`) proving the
    before-persistence drop.

Diagnostic parity: the exact refs you constructed
(`{"kind":"proposal","slug":"../bad"}`, traversal-shaped raw `file`) now return
`None` from `load_proposal` and are dropped by the ranker.

---

## Reviewer findings — round 5  _(Reviewer — Codex)_

R4-1 is resolved. `EvidenceRef.is_safe()` reuses the canonical slug and raw
basename boundaries for all three evidence kinds; the ranker drops unsafe refs
before recurrence/breadth calculation or persistence, and proposal loading
requires both exact shape and safe values. The per-kind load regressions and
drop-before-scoring ranker regression exercise the previously missing cases.

No new findings.

Verification: the focused corpus/ranker/proposal suites pass all 47 tests, and
`uv run python scripts/ci.py` passes all four gates with 468 tests.

**Verdict:** approve — the evidence semantic-validation gap is closed and all
prior findings are resolved.
