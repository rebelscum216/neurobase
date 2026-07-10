---
slug: phase-8-workstream-d-miner
status: awaiting-review
author: claude
reviewer: codex
branch: phase-8-workstream-d-miner
diff: git diff main...HEAD
created: 2026-07-10
---

# Review: Phase 8 workstream D — miner

## Brief  _(Author — Claude)_

**Intent.** Implement Phase 8 workstream D (spec §12.5, execution plan
`docs/notes/2026-07-09-phase-8-recommender-plan.md`): the miner — `mine(root,
brain) -> list[dict]`, an injectable-`Brain` step that reasons over the
read-only corpus (workstream C, already on `main`) and returns durable-behavior
*candidates*. It never writes; the ranker/proposal store (workstream E)
recomputes counts from evidence and does all writing. Workstreams A/B/C are
merged.

**Scope.** Branch `phase-8-workstream-d-miner`, `git diff main...HEAD`. Two files:
- `src/neurobase/recommender/miner.py` (new) — `mine()`, `_validate_candidate`,
  `_build_payload`, `_system_prompt`, evidence/ledger helpers.
- `tests/test_miner.py` (new) — the three named workstream-D tests + extras.

**Design (mirrors the curator, spec §2).** `mine` builds a system prompt +
JSON user payload from `corpus.load_corpus`, calls `brain.plan_json` (reusing
`brain/base.py:parse_plan_json` unchanged — so the envelope is a JSON object
`{"candidates": [...]}`, not a bare array, exactly as ADR-0007 notes), and
returns the structurally-valid candidates. Candidates are returned as **plain
dicts** (spec's literal `-> list[dict]`), normalized to the §12.5 field shape;
self-reported `occurrences`/`projects`/`agents` are carried but flagged
advisory-only (the ranker recomputes them from `evidence`).

**Fail-soft (spec §12.5, Invariants).**
- Unparseable response *or* a genuine `BrainError` (timeout/exhausted retries)
  ⇒ `mine()` returns `[]`, so `recommend run` (F) leaves `proposals/` untouched
  — the same broad `except BrainError` the curator uses. (An unparseable answer
  reaches `mine` already as a `BrainError`, since `plan_json` runs the parse
  inside its retry wrapper.) A cleanly-parsed non-`{"candidates": [...]}`
  response also degrades to `[]`.
- A structurally invalid candidate (not an object, missing/blank `slug`/`draft`,
  slug failing `SLUG_RE`, disallowed `type`/`candidate_type`) is skipped with a
  logged warning; the rest of the batch survives.

**Focus areas.**
- **The three named tests genuinely exercise behavior?** `test_unparseable_*`
  (BrainError → `[]`), `test_invalid_candidates_skipped_with_warnings` (6 defect
  variants dropped, 6 warnings, 1 keeper survives), `test_rejected_near_
  duplicate_summary_reaches_prompt` (a ledger-rejected proposal's body + per-type
  count land in the captured user payload; system prompt tells the model to
  avoid it).
- **Near-dup usage at prompt-build time.** §12.5 says "§12.4's near-duplicate
  function selects which [rejected snippets]." Since candidates don't exist yet
  when the prompt is built, I read "selects which ones" as *dedupe the rejected
  snippets against each other* (`corpus.is_near_duplicate`) so near-identical
  rejections collapse to one representative rather than bloating the prompt.
  Is that the right reading, or did you expect selection against corpus content?
- **Warnings via `logging`.** The named test requires "with warnings," so I
  used a module `logger.warning(...)` (testable via `caplog`) — the first use
  of `logging` in this repo (the curator's "skip + warn" comments never actually
  emit). Acceptable, or prefer a returned skip-tally like `corpus.skipped_
  projects`? (Spec pins the return type to `list[dict]`, so I kept skips in the
  log rather than the return value.)
- **Evidence normalization** (`_normalize_evidence`): malformed evidence items
  are dropped (round-tripped through `corpus.EvidenceRef`) but never fail the
  candidate — the ranker just counts fewer refs. Reasonable, given §12.5 lists
  only slug/draft/type/candidate_type as skip conditions?

**Known risks / tradeoffs.** The prompt text is mine to write (spec gives
requirements, not wording) — worth a read for whether it meets every §12.5 MUST
(role, `min_occurrences` gate, secret ban, ledger-avoidance, JSON-only envelope).
`min_occurrences` is only *stated* to the model here; the deterministic gate is
the ranker's job (E), not enforced in D.

**How to verify.**
- `uv run python scripts/ci.py` — full gate; green locally, 416 passed.
- `uv run pytest tests/test_miner.py -v`.

**Out of scope** (later slices): the ranker + proposal store (E) — including the
deterministic threshold gate, evidence-derived breadth, and all proposal writes;
`recommend` CLI (F); emitters (G); metrics (H). The miner deliberately does not
write, rank, or gate — it proposes candidates as plain data.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _pending_
