---
slug: phase-8-workstream-c-corpus-loader
status: awaiting-review
author: claude
reviewer: codex
branch: phase-8-workstream-c-corpus-loader
diff: git diff main...HEAD
created: 2026-07-10
---

# Review: Phase 8 workstream C — corpus loader + evidence model

## Brief  _(Author — Claude)_

**Intent.** Implement Phase 8 workstream C (execution plan
`docs/notes/2026-07-09-phase-8-recommender-plan.md`, spec §12.4/§12.1, ADR-0007
D17/D18/D21): a pure, read-only corpus aggregator the miner (workstream D) will
later run over, plus the structured evidence model and the `[recommend]` config
table (this is the first code that needs it). Workstreams A (contract) and B
(seed importer) are already merged to `main`.

The loader gathers, across **every** registered project: (1) active curated
facts, uncapped; (2) recent raw captures, capped per project by
`raw_lookback_days=30` **and** `raw_cap_per_project=200`, whichever yields
fewer (ADR-0007 D17); (3) a fail-soft/empty ledger summary — per-`candidate_type`
reject counts + rejected proposal bodies for near-dup suppression (D16). The
ledger file itself doesn't exist until workstream F, so it's read fail-soft.

**Scope.** Branch `phase-8-workstream-c-corpus-loader`, `git diff main...HEAD`.
Key files:
- `src/neurobase/recommender/corpus.py` (new) — `load_corpus` + the
  `EvidenceRef`/`ResolvedEvidence`/`Corpus`/`LedgerSummary` dataclasses,
  `resolve_evidence` (D21), `jaccard_similarity`/`is_near_duplicate` (D18), and
  the canonical `proposals_dir`/`proposal_path`/`ledger_path` location helpers.
- `src/neurobase/core/config.py` — new `RecommendConfig` dataclass (mirrors
  `McpConfig`), all seven §12.11 keys, wired into `Config` + `load_config`.
- `tests/test_corpus.py` (new) — the four named workstream-C tests plus D21/D18/
  ledger-fail-soft coverage.
- `tests/test_config.py` — assert the `[recommend]` defaults + a partial override.

**Focus areas.**
- **Raw cap semantics** (`_load_raw`): does "filter by `raw_lookback_days`, then
  keep the most-recent `raw_cap_per_project`" correctly implement §12.4's
  "whichever yields fewer"? Is taking `captures[-N:]` off `list_raw`'s
  oldest-first order the right "most recent N"?
- **Project-level fail-soft** (`load_corpus`'s `except Exception`): does it match
  §12.4's "skip a missing or malformed project tree, never abort the pass"
  without swallowing bugs that should surface? Note the deliberate distinction
  in the test: an *invalid registry slug* raises → skipped-and-named in
  `skipped_projects`; a *merely missing* store tree yields empty without being
  counted as a skip.
- **Evidence serialization** (`EvidenceRef.to_frontmatter`): does it produce
  exactly §12.1's three shapes (only each kind's own keys, never a `None`), and
  round-trip through `store.write_doc`/`read_doc` block-style?
- **Evidence resolution** (`resolve_evidence`, D21): missing target →
  `unresolved` never raises; a tombstoned curated fact still resolves to its
  `.tombstones/` record.

**Known risks / tradeoffs (judgment calls flagged for review).**
- **Near-dup implemented now, not deferred to D.** §12.4 *defines* D18's Jaccard
  near-duplicate function but its named tests live in workstreams D/E. I built it
  here because it's the natural home (the ledger-summary step is its first
  consumer) and both D and E import one deterministic definition rather than
  reinventing it. Alternative was a bare stub deferred to D.
- **Return shape = dataclasses, not dicts.** Chose typed frozen dataclasses
  (house style: `SearchHit`, `Document`, `SeedResult`) over dicts so the miner/
  ranker get mypy-checked fields; `RawCapture` carries `agent`/`session_id` so
  the ranker (§12.6) recomputes breadth without re-opening raw files. `curated`/
  `raw` are flat lists with a `project` field (not a per-project map) since the
  miner iterates them into one prompt and each item self-describes as evidence.
- **Raws included regardless of `consumed`** (`list_raw(..., unconsumed_only=False)`).
  §12.4 says "recent raw captures" with no consumed filter; mining is historical
  pattern detection, so a curated-and-consumed raw is still corroborating
  evidence. Flagging in case the reviewer reads the intent differently.
- **`LedgerSummary` is reject-focused** (per-type reject counts + rejected
  bodies), matching ADR-0007 D16 / §12.5's miner-input framing. Accepted-artifact
  metrics (§12.9) are workstream H's concern and read the ledger their own way —
  not surfaced here.
- **`skipped_projects` added to `Corpus`** for observability (mirrors seed.py's
  "count the skips" over silent fail-soft) — small addition beyond the spec's
  return-shape silence.

**How to verify.**
- `uv run python scripts/ci.py` — full gate (ruff + format + mypy + pytest);
  green locally, 406 passed.
- `uv run pytest tests/test_corpus.py tests/test_config.py -v` — the workstream-C
  + config tests specifically.
- The four named tests: `test_all_project_registry_traversal`,
  `test_missing_and_bad_project_tree_skips`,
  `test_raw_cap_by_count_enforced` / `test_raw_cap_by_age_enforced`,
  `test_evidence_references_serialize_into_proposal_frontmatter`.

**Out of scope** (later workstreams / separate review slices, per the plan): the
miner (D), ranker + proposal store (E), `recommend` CLI + edit/accept/reject (F),
artifact emitters (G), metrics (H). No CLI wiring, no LLM calls, no writes to
`proposals/` — corpus.py + config + tests only. `emit_*`/`ranker`/`miner` don't
exist yet; the `proposals_dir`/`ledger_path` helpers are seeded here for those
workstreams to import but are otherwise unused by this slice.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _pending_
