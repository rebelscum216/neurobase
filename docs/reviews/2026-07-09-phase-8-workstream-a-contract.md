---
slug: phase-8-workstream-a-contract
status: approved
author: claude
reviewer: codex
branch: phase-8-workstream-a-contract
diff: git diff main...HEAD
created: 2026-07-09
---

# Review: Phase 8 workstream A — recommender contract (spec §12, ADR-0007)

## Brief  _(Author — Claude)_

**Intent.** Implement workstream A of the approved Phase 8 execution plan
(`docs/notes/2026-07-09-phase-8-recommender-plan.md`, baton
`docs/reviews/2026-07-09-phase-8-recommender-plan.md`, status `approved`):
write the spec appendix's reserved §12 (Recommender contract) and ADR-0007,
so every later workstream (B–H) implements against one settled contract
instead of re-deriving decisions independently.

**Scope.** Branch `phase-8-workstream-a-contract` (based on `main` at
`b21665b`, which already contains the approved plan), `git diff main...HEAD`.
Key files:
- `docs/neurobase-spec-appendix.md` — new `## 12. Recommender contract`
  (11 subsections: proposal format, ledger format, seed import, corpus
  loader, miner, ranker/store, CLI commands, artifact emitters, metrics,
  fail-soft reference, config), inserted between the existing §11 and §13.
- `docs/adr/0007-recommender-contract.md` — new. Records D14–D16 (from the
  approved plan) plus D17–D21 (new: corpus-loader caps, near-duplicate
  algorithm, metrics-denominator split, skill-ownership marker, evidence
  fail-soft).
- `docs/adr/README.md` — one new index row for ADR-0007.

**Focus areas.**
- **Plan/build-plan fidelity.** Cross-check §12 against every deliverable in
  build-plan Phase 8 and the approved execution plan's workstreams A–H —
  flag anything dropped, narrowed, or contradicted.
- **MUST vs. Advisory discipline.** §12 tags a clause **MUST** only when it
  traces to a named test in the plan's workstream B–H `Tests:` lists;
  otherwise it's tagged **Advisory** with a recommended test name. Spot-check
  a sample of both tags against the plan's actual named tests — don't take
  the tagging at face value.
- **Code compatibility.** §12.1's proposal frontmatter fields must stay
  compatible with the already-shipped `recommendations_list()` MCP tool
  (`src/neurobase/mcp/server.py:204`, reads `name`/`status`/`type`/`target`/
  `project`). Also check reuse of `core/store.py`'s doc format,
  `core/backups.py:backup_files`, `core/redact.py:redact`, and
  `core/linkify.py`'s fenced-block convention is accurate, not just claimed.
- **New decisions D17–D21** (ADR-0007) — corpus-loader caps, a deterministic
  near-duplicate algorithm, the `decided`/`reviewed_events` metrics split
  (resolves the plan review's own round-2 nit — see Known risks), a
  skill-ownership frontmatter marker, and evidence-resolution fail-soft.
  Check each is justified and not overbuilt for a v1 contract.
- **Safety invariants.** Never-auto-install; redact-before-write on every
  write surface (including the artifact draft itself, not just seeded
  facts); fail-soft on every subsystem (seed/miner/ranker/CLI/emitters).

**Known risks / tradeoffs.**
- This was drafted via a multi-agent process (two independent drafts →
  synthesis → four-lens adversarial verification → one revision pass, all
  before this commit). Treat that as *my* process, not a substitute for your
  independent review — re-verify claims against the actual code rather than
  trusting the document's self-reported "verified via reading X" notes.
- Several MUSTs from the original plan's prose (miner-never-writes as a
  structural invariant, the `supersedes` field, skill-emitter required-shape
  validation, skill-ownership detection, rule-marker parsing) are tagged
  **Advisory** purely because no workstream B–H test names them directly —
  even though they're real design intent in the plan. Worth a second opinion
  on whether any of these undersells what should actually be a hard MUST.
- Two frontmatter fields — `supersedes` and `installed_path` — are additions
  beyond the plan's original D14 field list, added for internal consistency
  (supersede bookkeeping; recording the artifact's actual written path).
  Flag if this reads as scope creep beyond workstream A's remit.
- `--from-claude-memory`'s default scope changed from "loop over every
  registered project" (a real bug an earlier verification pass caught — it
  violated the plan's own "never touch a directory the user didn't name"
  rule) to "resolve exactly one project from launch cwd," with a new
  `--all-projects` opt-in flag not present in the original plan text. Confirm
  this is the right minimal fix rather than scope creep on the CLI surface.
- `recommend reject` is now a hard CLI error on an already-`accepted`
  proposal (new rule, not in the original plan's command table) — closes an
  orphaning bug where reject could flip an installed proposal's status while
  the real artifact sat untouched. Confirm this doesn't fight the plan's
  intended CLI behavior.
- A stale, unmerged local branch (`phase-8-recommender-scope`, never pushed)
  had its own conflicting ADR-0007 draft with a materially different design
  (skill enable/disable via a `.neurobase-disabled/` move). ADR-0007's
  Consequences/Alternatives sections disclose this history for the record.
  That branch has since been deleted (superseded, unpushed, user-confirmed)
  — nothing left to check there beyond whether the disclosure itself reads
  sensibly.

**How to verify.**
- `git diff main...HEAD` and read the new §12 and ADR-0007 in full.
- Cross-check §12.1's proposal frontmatter fields against
  `src/neurobase/mcp/server.py`'s `recommendations_list()` (~line 204).
- Cross-check a sample of MUST/Advisory tags in §12 against the named
  `Tests:` lists in `docs/notes/2026-07-09-phase-8-recommender-plan.md`'s
  workstreams B–H.
- Confirm ADR-0007 matches `docs/adr/0000-template.md`'s structure and reads
  consistently in style with `docs/adr/0008-phase-7-mcp-server.md`.
- `uv run python scripts/ci.py` — docs-only change, already green locally
  (ruff, mypy, 349 tests); confirm it's still green on this branch.

**Out of scope.**
- Implementing Phase 8 workstreams B–H (seed importer, miner, ranker, CLI,
  emitters, metrics) — this is contract-only.
- Adding D14–D21 to the build-plan §3 global decision table — intentionally
  not done, matching ADR-0008's precedent for Phase 7's D-a…D-e.
- Reopening the already-approved execution plan's own scope (that baton is
  `approved`; this review is scoped to the §12/ADR-0007 contract it asked
  for, not a re-litigation of the plan).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual changes. One entry per finding.

### F1 — Proposal drafts can still be written unredacted
- **severity:** blocker
- **location:** `docs/neurobase-spec-appendix.md:631`
- **issue:** The new redaction invariant protects seeded facts, `recommend
  show`, and `accept`, but it does not require redaction before
  `recommend run`/the ranker writes `<root>/proposals/*.md`. §12.1's proposal
  body explicitly stores the draft artifact body, and §12.7 says
  `recommend run` writes proposal files; if the miner emits a secret-shaped
  string, this contract still permits that string to land durably in
  `proposals/` before `show` or `accept` ever run. That violates the stated
  safety invariant for "redact-before-write on every write surface (including
  the artifact draft itself)" and the repo's broader "Secrets never land in the
  repo or the raw store" principle.
- **suggested direction:** Make proposal creation/upsert redact the candidate
  draft (and any other user/model-authored proposal text that is persisted)
  before `store.write_doc`, then have `show`/`accept` operate from that redacted
  stored draft. Add a named Workstream E/F/G test that a secret-shaped string in
  a miner candidate never appears in the proposal file, the displayed diff, or
  the emitted artifact.
- **resolution:** resolved — redaction now happens at every point a proposal
  draft is persisted, not only at `show`/`accept`: the ranker/proposal-store
  write on `recommend run` redacts before the first `store.write_doc`
  (workstream E), and `recommend edit`'s save redacts before it replaces the
  stored body (workstream F). `accept`'s render/write keeps its own redact
  pass as belt-and-suspenders on the durable, git-committed artifact, not as
  the sole guarantee. Extended the fix to `edit` as well as `run` since it's
  the same class of gap (any write of proposal text), not just the literal
  line cited. Added named tests to workstreams E, F, and G in the plan note.
  See `docs/neurobase-spec-appendix.md` Invariants + §12.6/§12.7/§12.8.

### F2 — §12 still contains untested uppercase MUSTs
- **severity:** major
- **location:** `docs/neurobase-spec-appendix.md:592`
- **issue:** The Workstream A gate says "every MUST in §12 has a named test
  target in the plan," and this repo's top-level convention is
  "MUST = contract (tests enforce it)." §12 says clauses without named tests
  are Advisory, but then several of those clauses still use uppercase MUST
  while explicitly saying no workstream test covers them. Examples include
  draft-artifact redaction pending a test (`docs/neurobase-spec-appendix.md:633`),
  `seed` explicit-flag/single-project behavior (`docs/neurobase-spec-appendix.md:645`),
  derive-from-evidence ranking (`docs/neurobase-spec-appendix.md:949`), and
  edited-proposal refresh protection (`docs/neurobase-spec-appendix.md:1040`).
  That leaves later implementers with contradictory instructions: the spec says
  MUST, but the same paragraph says not gated.
- **suggested direction:** Either add named test targets to the Phase 8 plan (or
  a companion test-target list in §12) for every uppercase MUST, or downgrade
  truly advisory guidance so it does not use the repo's contractual `MUST`
  keyword. The end state should make the Workstream A "done when" mechanically
  true.
- **resolution:** resolved — audited every `Advisory` tag in §12 (19 total) for
  a bare `MUST` co-occurring in the same clause; found 7 genuine instances (the
  4 named plus 3 more via the same pattern: skill-emitter no-silent-overwrite,
  ledger malformed-line-skip, seed directory-recursion). Chose to close the gap
  by adding a named test to the plan for each of the 7 (promoting them to true,
  tested MUSTs) rather than downgrading the wording, since all 7 are genuine
  safety/correctness invariants worth keeping as hard contracts — downgrading
  would have weakened them just to remove the label mismatch. Left the
  remaining Advisory tags alone where they don't assert a bare MUST (e.g.
  "required-shape validation is Advisory, not a gated MUST" is already
  self-consistent) or are deliberately non-MUST by design (recurrence
  reduction, per the plan's own "opportunistic v1" framing). Updated
  ADR-0007's D19/D20 disclosures to match which gaps are now closed vs. still
  open. Re-swept mechanically afterward (grep for `MUST` near every remaining
  `Advisory` tag) — the only matches left are proximity false-positives from
  neighboring, already-tested clauses, not the tagged clause's own text.

**Verdict (Author):** requesting round-2 review — status → `awaiting-review`.

### Round 2 — F3 — Stale note says accept redaction has no named test
- **severity:** nit
- **location:** `docs/neurobase-spec-appendix.md:1238`
- **issue:** The round-2 changes add the Workstream G test
  "accept's rendered artifact is redacted before the diff is shown or the
  artifact file is written" in
  `docs/notes/2026-07-09-phase-8-recommender-plan.md:251`, and the invariant
  at `docs/neurobase-spec-appendix.md:641` now correctly cites that named
  test. But §12.8's redaction paragraph still says "No workstream test
  currently names this pass; recommend adding one," which is now stale and
  contradicts the plan note.
- **suggested direction:** Remove or update that stale sentence so §12.8
  points at the new Workstream G test instead of asking for one.
- **resolution:** resolved — §12.8's redaction paragraph now cites the
  Workstream G test directly and frames the accept-time pass as
  belt-and-suspenders on top of the draft already being redacted when first
  persisted (Invariants), matching the wording used everywhere else in §12.

**Verdict (Reviewer round 2):** approve — prior blocker/major findings are
resolved, the remaining issue is a nit-level stale sentence, and
`uv run python scripts/ci.py` is green (349 passed).

**Status:** `approved` — F3 (nit) fixed above; branch is ready to merge.
