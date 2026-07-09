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

_(awaiting Claude)_

**Verdict:** _(pending)_
