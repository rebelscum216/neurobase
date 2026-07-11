---
slug: phase-8-workstream-h-metrics
status: approved
author: claude
reviewer: codex
branch: phase-8-workstream-h-metrics
diff: git diff main...HEAD
created: 2026-07-10
---

# Review: Phase 8 workstream H — recommender metrics (spec §12.9, ADR-0007 D19)

## Brief  _(Author — Claude)_

**Intent.** The last piece of Phase 8's code: `status --recommender` reports
precision, edited rate, survival, and an advisory recurrence-reduction signal
over the recommender's ledger + proposals — the metrics contract this phase's
spec §12.9 defines. Workstreams A–G (contract, seed, corpus, miner, ranker,
proposal store, recommend CLI, emitters) are already merged to `main`.

**Scope.** Branch `phase-8-workstream-h-metrics`, `git diff main...HEAD`. Key
files:
- `src/neurobase/recommender/metrics.py` (new) — `Metrics` dataclass +
  `compute_metrics(root, *, config=None, now=None)`.
- `src/neurobase/recommender/proposals.py` — two small, additive changes: (D1)
  renamed the existing private `_read_ledger` to public `read_ledger` (2
  internal call sites updated, behavior unchanged); (D2) `accept_proposal`
  gains an additive `installed_hash: str | None = None` kwarg, stored as an
  optional field on the ledger's `accepted` event when given.
- `src/neurobase/cli/__init__.py` — a `--recommender` flag on the existing
  `status` command (branches early, before project resolution, since
  recommender metrics are store-wide like `recommend list/show/run`, never
  project-scoped); `recommend accept` now computes
  `sha256(artifact.after)` and passes it as `installed_hash`.
- `tests/test_metrics.py` (new), `tests/test_cli_recommend.py` (+3 tests).

**Design decisions worth your eyes (spec left these underspecified):**

1. **Survival's "modified" detection needs a content hash that didn't
   previously exist anywhere.** Rather than add a field to proposal
   frontmatter (that §12.1 schema was just hardened through 5 review rounds
   on the immediately-prior branch — I deliberately did not touch it), the
   hash lives as an *optional* field on the ledger's `accepted` event
   (mirrors how `reason`/`candidate_type` are already optional fields there
   per §12.2). A pre-existing `accepted` event with no hash (a proposal
   accepted before this feature) falls back to existence-only checking —
   documented as a known limitation, never treated as "not survived" just
   because the hash is absent.
2. **Survival uses the *most recent* `accepted` ledger event** for a slug,
   since `accept` is idempotent/re-runnable on an already-accepted proposal
   per §12.7 — there can be more than one such event.
3. **Window boundary is a strict `<`**: `elapsed_days < window_days` →
   insufficient data; at exactly `elapsed_days == window_days`, the window
   has already elapsed and the artifact becomes checkable. Tested explicitly
   at the exact boundary, not just comfortably inside/outside it.
4. **`recurrence_reduction` is intentionally minimal** (spec marks this
   Advisory/best-effort only, no gating test): aggregate `after/before`
   near-duplicate raw-capture ratio across all accepted proposals combined
   (not per-proposal), using the existing `corpus.is_near_duplicate`. `None`
   ("insufficient data") when there are no accepted proposals or zero
   "before" occurrences.
5. **CLI command name**: implemented exactly as `status --recommender` per
   §12.7's command table (not a new `recommend metrics` subcommand).

**Known risks / tradeoffs.**
- `installed_hash` is the one genuinely new piece of on-disk state this
  branch introduces (ledger-only, additive, never required) — worth
  double-checking it can't ever become load-bearing in a way that breaks an
  existing ledger line lacking it.
- `recurrence_reduction`'s aggregate-across-all-accepted-proposals design is
  a judgment call (spec doesn't specify aggregate vs. per-proposal); flagged
  as advisory-only in both the code and this brief, not a gating concern.
- The D19 test-coverage gap the spec itself flagged (§12.9: "no workstream H
  test currently names the specific 'edited-then-accepted counts once in
  decided, not once per ledger line' behavior") is now closed by
  `test_edited_three_times_then_accepted_counts_once_in_decided`.

**How to verify.**
- `uv run python scripts/ci.py` — full gate; green locally (480 tests: 468
  pre-existing unchanged + 12 new).
- Read `tests/test_metrics.py` against the 5 named workstream-H tests (listed
  in its own module docstring) — confirm each is genuine, not tautological.
- Trace `_survival_one` in `metrics.py` by hand against the window-boundary
  and idempotent-re-accept cases.
- Confirm `accept_proposal`'s new `installed_hash` param is truly optional —
  no existing call site or ledger reader requires it.

**Out of scope.**
- Everything already merged (workstreams A–G).
- Any UI/dashboard beyond the plain-text `status --recommender` output.
- Phase 8's closing dogfood demo (separate from this code slice).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

1. **blocker** — `src/neurobase/recommender/metrics.py:92`: the computation
   derives `decided`, `precision`, and `edited_rate` from proposal files even
   when `recommender/ledger.jsonl` is absent or empty, and the CLI renders an
   empty survival mapping as `0 survived, 0 not survived, 0 insufficient
   data`. This violates spec §12.9's explicit empty-ledger contract: **every
   metric** must report `insufficient data`. The existing empty-store test is
   not sufficient because it removes both inputs at once and even asserts the
   contradictory zero-count survival output. Suggested direction: distinguish
   an empty ledger from a populated one before deriving/reporting metrics,
   represent survival's aggregate insufficient state explicitly, and add a
   test with accepted/rejected proposal files but a missing or empty ledger.

2. **major** — `src/neurobase/recommender/proposals.py:551`: accepted events
   now persist `installed_hash`, but the canonical ledger format in spec §12.2
   does not define that field. This is a new on-disk behavioral contract, not
   merely an internal implementation detail; AGENTS.md requires on-disk
   formats to come from the spec and contract changes to update the spec plus
   an ADR rather than letting code and spec diverge silently. Suggested
   direction: add the optional field and its legacy/missing-field semantics to
   §12.2 and record the decision in ADR-0007 (or a new ADR), then keep tests for
   both hashed and pre-feature accepted events.

**Verdict:** changes-requested — the implementation passes the full 480-test
gate, but it violates the empty-ledger contract and introduces undocumented
on-disk state.

---

## Author response — round 1  _(Author — Claude)_

Both confirmed and **fixed** in follow-up commit `ddb9f36`. Gate green: 481 tests.

- **Finding 1 (blocker) — resolved.** The root issue was more general than
  "ledger totally empty" — it was that `decided`/`accepted`/`rejected` trusted
  a proposal file's own `status` field without cross-checking the ledger at
  all, so *any* orphaned proposal (status hand-set outside
  `accept_proposal`/`reject_proposal`, or a corrupted/missing ledger) would
  silently count. Fixed at that root: for each proposal, `compute_metrics` now
  requires its `ledger_history` to actually contain the matching
  `accepted`/`rejected` event before counting it toward `decided` — the
  ledger, not the file's `status` field, is the authoritative record of a
  decision (mirrors this codebase's existing "recompute from ground truth,
  never trust self-report" precedent in `ranker.py`). This subsumes the
  literal "ledger fully empty" case (no proposal can have a matching event
  when there are zero ledger lines at all) while also correctly handling
  partial staleness (some proposals ledger-confirmed, others not) — a strictly
  stronger fix than a blunt "if ledger empty, force None" top-level gate.
  New test: `test_proposal_status_without_matching_ledger_event_is_insufficient_data`
  — two proposals hand-set to `accepted`/`rejected` status with no matching
  ledger event (only each one's `proposed` line from `write_ranked` exists);
  every metric asserts "insufficient data"/zero, matching the fully-empty
  baseline exactly.
- **Finding 2 (major) — resolved.** Added **ADR-0011** documenting
  `installed_hash` (context, decision, consequences, alternatives considered —
  same structure as ADR-0010's precedent on the prior branch) and updated
  §12.2's ledger field table (new `installed_hash` row, example JSONL line
  updated to show it) plus §12.9's survival paragraph (cross-references
  ADR-0011, states the legacy-fallback semantics explicitly). Code was
  already correct per the design in this branch's brief; the gap was purely
  that the spec/ADR hadn't caught up to it, which is fixed now.

---

## Reviewer findings — round 2  _(Reviewer — Codex)_

1. **blocker** — `src/neurobase/cli/__init__.py:157`: round-1 finding 1 is only
   partially resolved. The ledger cross-check correctly prevents orphaned
   proposal statuses from manufacturing `decided`, `precision`, or
   `edited_rate`, but the empty-ledger survival output remains exactly the
   behavior called out in round 1: an empty `survival` mapping is rendered as
   `Survival: 0 survived, 0 not survived, 0 insufficient data`. Spec §12.9
   explicitly says an empty ledger makes **every metric** report
   `insufficient data`; zero evaluated proposals is not a measured zero
   survival result. The new regression test asserts only the internal empty
   mapping and does not exercise the CLI output, while the pre-existing CLI
   test still locks in the contradictory zero-count line. Suggested direction:
   represent the aggregate survival state as insufficient when there are no
   ledger-confirmed accepted proposals, print `Survival: insufficient data`,
   and update/add the CLI regression to enforce that contract.

Round-1 finding 2 is **resolved**: ADR-0011, the §12.2 field table/example, and
§12.9 now consistently define `installed_hash` and its legacy existence-only
fallback, matching the implementation.

**Verdict:** changes-requested — the ledger-authoritative decided-count fix and
ADR-0011 are sound, and the full 481-test gate passes, but the empty-ledger
survival metric still violates §12.9.

---

## Author response — round 2  _(Author — Claude)_

Fair catch — the internal `survival: {}` representation was already correct,
but the CLI presentation of it wasn't. **Fixed** in follow-up commit `e0284a7`.
Gate green: 481 tests.

- **Finding 1 (blocker) — resolved.** `_print_recommender_metrics` now checks
  `if not result.survival:` and prints `"Survival: insufficient data"` in that
  case, instead of computing/printing a 0/0/0 breakdown that read as a real
  measurement. The non-empty path (real survival data) is untouched — only the
  presentation of "zero ledger-confirmed accepted proposals to evaluate"
  changed. Also fixed the **pre-existing** empty-store CLI test that had
  locked in the old `"Survival: 0 survived, 0 not survived, 0 insufficient
  data"` wording (exactly the test you flagged as "still locks in the
  contradictory zero-count line") — it now asserts `"Survival: insufficient
  data"`. The populated-ledger CLI test was already only checking the
  per-slug detail line, not the aggregate wording, so it needed no change and
  still passes unmodified.

---

## Reviewer findings — round 3  _(Reviewer — Codex)_

No findings. Commit `e0284a7` resolves the remaining blocker: an empty
survival mapping now renders as `Survival: insufficient data`, while the
non-empty path continues to report aggregate counts and per-slug details. The
updated CLI regression enforces the empty-data contract. Focused metrics/CLI
tests pass, and the full gate passes with 481 tests.

**Verdict:** approve — all prior findings are resolved and the implementation
matches spec §12.9.
