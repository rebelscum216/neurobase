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

### F1 — Evidence/proposal path helpers allow escaping their store directories
- **severity:** major
- **location:** `src/neurobase/recommender/corpus.py:64`
- **issue:** The new canonical path/evidence helpers do not validate proposal
  slugs or evidence path components before joining them onto store paths.
  That violates the §12.1 proposal-slug boundary (`<slug>` matches
  `^[a-z0-9-]+$`) and leaves later readers/writers to build on unsafe paths.
  I reproduced `proposal_path(Path("/tmp/root"), "../escape")` returning
  `/tmp/root/proposals/../escape.md`; more importantly,
  `resolve_evidence(root, EvidenceRef.raw("alpha", "/etc/passwd"))` returns a
  `ResolvedEvidence(..., status="resolved", path=PosixPath("/etc/passwd"))`
  on macOS because an absolute `file` value discards the intended
  `raw/` prefix. The same class of issue applies to curated/proposal slugs
  containing path separators. Today this is "only" a read-side resolver, but
  Workstreams E/F are explicitly meant to import `proposal_path`, and
  `recommend show` will resolve/read evidence; accepting this boundary now
  makes future write/read paths path-traversal-prone.
- **suggested direction:** Validate all proposal/evidence identifiers at the
  helper boundary: proposal/curated slugs should match `store.SLUG_RE`, raw
  evidence `file` should be a basename (no absolute path, no separators, no
  `..`, probably `*.md`), and invalid evidence should resolve to
  `UNRESOLVED` rather than returning an escaped path. Add regression tests for
  `proposal_path("../x")` and raw/curated/proposal evidence refs with absolute
  or parent-traversal components.
- **resolution:** **resolved** (follow-up commit) — real defect, agreed and
  fixed at the helper boundary, matching the store's own slug discipline:
  - `proposal_path` now validates `slug` against `store.SLUG_RE` and raises
    `store.InvalidSlugError` on a bad slug (so a writer can never build a path
    escaping `proposals/`).
  - `resolve_evidence` validates before joining: a `raw` `file` must pass
    `_is_safe_raw_basename` (bare `*.md`, no `/` or `\`, no `.`/`..`, no
    absolute path — closing the "absolute component discards the `raw/` prefix"
    escape), and curated slugs must pass `_valid_slug`; an invalid identifier
    resolves to `UNRESOLVED` (path `None`), never an escaped path, per D21's
    fail-soft contract.
  - `_rejected_bodies` skips any invalid ledger slug so an untrusted ledger line
    can't make the fail-soft reader raise through `proposal_path`.
  - Regression tests added: `test_path_helpers_and_resolution_reject_traversal`
    (proposal_path raises on `../escape`/`a/b`/`..`; raw `/etc/passwd`,
    `../../../etc/passwd`, `sub/nested.md`, `..\win.md`, curated/proposal
    traversal slugs all resolve UNRESOLVED, including a real on-disk absolute
    file) and `test_ledger_reader_skips_traversal_slug`. Verified the reviewer's
    two exact reproductions now return raise / UNRESOLVED. Full gate green
    (`408 passed`).

**Verdict:** changes-requested — full CI is green (`406 passed`), but the
new path/evidence helpers need store-boundary validation before later
recommender workstreams build on them.

---

## Author resolution round 1  _(Author — Claude)_

F1 (major) resolved in follow-up commit — store-boundary validation added to
`proposal_path` / `resolve_evidence` / `_rejected_bodies`, with regression tests
and the two reviewer reproductions confirmed closed. `uv run python scripts/ci.py`
green (`408 passed`). Re-requesting review (`status: awaiting-review`).
