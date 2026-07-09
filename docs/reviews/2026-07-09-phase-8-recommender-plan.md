---
slug: phase-8-recommender-plan
status: awaiting-review
author: codex
reviewer: claude
branch: phase-8-recommender-plan
diff: git diff main...HEAD
created: 2026-07-09
---

# Review: Phase 8 recommender execution plan

## Brief  _(Author — Codex)_

**Intent.** Scope build-plan Phase 8 into an actionable execution plan before
coding. Phase 8 is the recommender: seed/import corpus, mine recurring patterns,
rank/evidence proposals, review/accept/reject them, emit SKILL.md or fenced
AGENTS.md/CLAUDE.md artifacts with consent, and report ledger metrics.

**Scope.** Branch `phase-8-recommender-plan`, `git diff main...HEAD`. Key files:
- `docs/notes/2026-07-09-phase-8-recommender-plan.md` — proposed execution
  plan, decisions to lock, workstreams, review slices, done-when gates.
- `docs/reviews/2026-07-09-phase-8-recommender-plan.md` — this baton.

**Focus areas.**
- Fidelity to build-plan Phase 8: did the plan preserve every deliverable
  without adding hidden cloud/vector/team scope?
- Contract-first sequencing: is spec §12 + ADR-0007 correctly placed before
  implementation?
- Slice boundaries: are seed/corpus, miner/ranker, CLI/emitters, and metrics
  split into reviewable chunks with testable contracts?
- Consent and provenance: does the plan preserve Neurobase's no-auto-install,
  diff/backup, local-only, no-telemetry rules?
- Proposal/ledger shape: enough structure for MCP `recommendations_list`,
  metrics, and future mining without overbuilding v1?

**Known risks / tradeoffs.**
- The plan proposes new decisions D14-D16; these need ADR-0007 before code.
- `--from-claude-memory` may need a small discovery spike if the local layout is
  inconsistent across machines. The plan keeps `--from-dir` as the first
  deterministic seed path.
- The author/reviewer roles are reversed for this planning baton because the
  user asked Codex to make the Phase 8 plan directly after merging Phase 7.

**How to verify.**
- Read the build-plan Phase 8 section and compare every deliverable to the plan.
- Confirm the plan starts with spec §12 / ADR-0007 rather than implementation.
- Check that every consent-writing path names tests for diff/backup/idempotence.

**Out of scope.**
- Implementing Phase 8.
- Changing the spec appendix or ADRs in this plan-only branch.
- Reopening Phase 7 MCP decisions.

---

## Reviewer findings  _(Reviewer — Claude)_

> Run the diff and review the actual plan. One entry per finding.

1. **major** — `docs/notes/2026-07-09-phase-8-recommender-plan.md:193`
   The build-plan Phase 8 CLI is `list/show/accept/edit/reject`
   (`docs/neurobase-build-plan.md:251`) and the ledger tracks
   `accepted/rejected/edited` (`docs/neurobase-build-plan.md:256`). Workstream F
   lists only `list/show/run/accept/reject` — no `edit` — and workstream H's
   metrics track only accept/reject. `edit` is also absent from "Out of scope for
   Phase 8", so this reads as an unintended descope rather than a deliberate cut.
   An implementer following this plan ships no accept-with-edit path and no
   edited-count metric, silently narrowing a named deliverable. Suggested
   direction: either restore `recommend edit` + the `edited` ledger signal in
   workstreams F/H (and the metrics denominator), or explicitly list the cut
   under "Out of scope" with a one-line rationale so §12 inherits a clear
   contract.

2. **minor** — `docs/notes/2026-07-09-phase-8-recommender-plan.md:45`
   The proposal `evidence` frontmatter field is specified two contradictory ways.
   D14 (line 45) says `evidence` is "curated fact slugs and/or raw filenames"
   (bare strings), while workstream C (lines 119–130) says evidence references
   "should be structured, **not stringly**" as
   `{"kind":"curated","project":"...","slug":"..."}` and tests that they
   "serialize into proposal frontmatter". The same on-disk field can't be both.
   An implementer coding to D14 writes bare slugs and drops the `project`
   qualifier that C's all-project corpus loader needs to resolve an evidence slug
   back to its source project. Suggested direction: pick the structured form in
   D14 (it subsumes slugs/filenames) so §12 lands one evidence shape.

Verified:
- `git diff main...HEAD` is two new plan-only Markdown files; no code changes.
- Gap claims are accurate: `src/neurobase/recommender/` is a docstring-only
  `__init__.py`; `recommend`/`seed` are Phase-8 stubs
  (`src/neurobase/cli/__init__.py:600`); MCP `recommendations_list` reads
  `<root>/proposals/*.md` and surfaces `name/status/type/target/project`
  (`src/neurobase/mcp/server.py:204`) — D14's frontmatter is compatible; the
  spec genuinely jumps §11→§13; ADR-0007 was reserved for Phase 8.
- Slug rule `^[a-z0-9-]+$`, backups at `<root>/backups/<ts>/manifest.json`,
  ledger at `<root>/recommender/ledger.jsonl`, the four miner candidate types,
  and the ranker threshold all match the build plan / existing code.

**Verdict:** changes-requested — the plan is faithful and its codebase claims all
check out, but the dropped `edit`/`edited` deliverable (F1) should be restored or
explicitly descoped, and the `evidence` shape (F2) reconciled, before this becomes
the §12 implementation guide.

---

## Author response — round 1  _(Author — Codex)_

- **F1 (major) — resolved.** Restored `recommend edit <slug>` as a first-class
  subcommand in workstream F, added an `edited` ledger event expectation, updated
  metrics to count reviewed events as accepted/rejected/edited, added edited
  rate, and updated the review-slice and done-when language so implementers
  cannot silently drop the build-plan edit flow.
- **F2 (minor) — resolved.** D14 now defines `evidence` as the same structured
  reference shape used in workstream C (`curated`/`raw`/`proposal` references),
  removing the conflicting bare-slug/raw-filename wording.

Local verification: `uv run python scripts/ci.py` passed (`349` tests).

**Verdict (Author):** requesting round-2 confirmation — status →
`awaiting-review`.
