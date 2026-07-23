---
slug: g2-g3-recommender-fixes
status: awaiting-review
author: claude
reviewer: codex
branch: feat/webui-phase1-suggestions
diff: git diff 30696a8..HEAD
created: 2026-07-23
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

**Out of scope.** The rest of the branch (webui Phase 1, the ADR-0019 rebase, the
`ui` StoreHandle fix) — already reviewed/landed. No webui surface for `revert` yet
(deliberately deferred to the app-shell phases). Origin's ADR-0015 StoreHandle work.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(none yet)_

**Verdict:** _(pending)_
