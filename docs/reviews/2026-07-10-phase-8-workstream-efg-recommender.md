---
slug: phase-8-workstream-efg-recommender
status: awaiting-review
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

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _(pending)_
