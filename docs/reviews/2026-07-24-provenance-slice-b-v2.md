---
slug: provenance-slice-b-v2
status: awaiting-review
author: claude
reviewer: codex
branch: feat/provenance-slice-b-v2
diff: git diff main...feat/provenance-slice-b-v2
created: 2026-07-24
round: 1
---

# Review: provenance Slice B (v2) — `from_raw` validation + curator fold journal

## Brief  _(Author — Claude)_

**Intent.** Two provenance guarantees for the curator pass, so the graph reader
(Slice A, not in this branch) has something trustworthy to read:

- **B1 — `from_raw` is validated against the batch.** The model returns
  `from_raw` filenames per upsert; those become the fact's permanent
  `provenance` record. Nothing checked them, so a hallucinated or echoed-from-a-
  lineage-block filename was written into a durable record as if it were a real
  edge. Now `from_raw` is intersected with the raw filenames actually shown to
  the model *this batch*; names outside it are dropped before the write and
  counted as `dropped_from_raw` in the pass summary.
- **B2 — a per-pass fold journal.** The pass summary records counts, not
  identities, so after raw retention expires there is no record of *which*
  session a fact came from or *what* superseded it. A `fold` object is now
  appended to the journal record: `consumed` (file + session_id + agent +
  captured_at, captured at consumption time), `edges` (landed slug → validated
  raw names), `superseded` (old slug → the slug that superseded it), and
  `tombstoned`.

**This is a re-derivation, not the original branch.** An earlier
`feat/provenance-slice-b` carried the same B1/B2 *decision* through six Codex
rounds to APPROVED, but against the pre-`StoreHandle` store API. Rather than
port a 132-line divergence, the decision was re-implemented on current `main`.
**That approval does not carry** — ADR-0022's header says so explicitly, and
`docs/reviews/2026-07-20-provenance-slice-b.md` was deliberately not brought
over. This is round 1 of a fresh review.

**Scope.** Branch `feat/provenance-slice-b-v2`, `git diff
main...feat/provenance-slice-b-v2` — two commits over `main` (`2e12450`):
`d8777d9` (code + tests), `1d15079` (docs). Key files:

- `src/neurobase/curator/engine.py` — the substance. New frozen `_ApplyResult`;
  `_apply_upserts` takes the batch's raw filename set and returns
  upserted/superseded_by/edges/dropped instead of a 2-tuple; `_log_pass` gains a
  keyword-only `fold=`; the `curate` loop accumulates the four fold lists across
  committed batches and reconciles them at pass end.
- `src/neurobase/core/store.py` — `CURATOR_LOG` moved here from `engine` (the
  future graph reader shares the name; `engine.CURATOR_LOG` is now an alias);
  `soft_delete_curated` no longer lets a `FileNotFoundError` on the unlink fail
  the call, and documents why it never deletes a tombstone path;
  `upsert_curated` documents why it does *not* clear a same-slug tombstone.
- `tests/test_curator.py` (+332), `tests/test_store.py` (+150),
  `tests/test_curate_budget.py` (+60) — +20 tests.
- `docs/adr/0022-curator-fold-journal.md` (new, `Proposed`), `docs/known-gaps.md`
  (G4), `docs/neurobase-spec-appendix.md` (§1 placement rule, §2 steps 4/9, the
  fold contract), `docs/how-it-works.md`.

**Focus areas.** In priority order:

1. **The port surface — this is the code that is genuinely new.** The B1/B2
   logic survived six rounds; the *wiring against `StoreHandle`* did not exist
   before and has never been reviewed. Please read
   `_apply_upserts`, `_log_pass`, and the `curate` loop as fresh code:
   - `_apply_upserts` changed shape (arg + return type). Are all its callers and
     the step-4/5/6 bookkeeping consistent with the new `_ApplyResult` fields?
     Note `superseded_by` is a `dict[old → new]`, which collapses two upserts in
     one batch superseding the same old slug — `setdefault` keeps the first.
     Intended, but worth a second opinion.
   - Step 4's rewrite: the old code counted soft-deletes via a `sum(...)` over
     `dict.fromkeys(superseded_slugs)`; the new loop iterates the dict and
     appends to `fold_superseded` only where `_safe_soft_delete` returned true.
     Is `superseded_count` still exactly what it was, for every path?
   - `handle.list_curated` / `handle.mark_consumed` / `handle.upsert_curated`
     usage — does anything here bypass the store chokepoint or use a READ handle
     where a WRITE handle is required?
2. **The pass-end reconciliation block (D22).** A slug soft-deleted by an early
   batch and re-upserted by a later one is active at pass end, so the pass did
   not remove it. The block uses the pass-end active set as the oracle, drops the
   false fold entries and decrements the matching counters, preserving
   `count == len(fold list)`. Please check the counters cannot go negative, that
   `final_active` is a sound substitute for the later `len(handle.list_curated(project))`
   in `active_facts` (prune/synth touch only `.tombstones/` and `nodes/`), and
   that the "journal-only repair" claim holds — no disk state is changed here.
3. **The fold must never reach the printed summary.** It carries raw
   `session_id`s. It is passed as a separate `_log_pass` kwarg and merged into
   the journal record only. Confirm no path leaks it into the returned summary
   dict (which the CLI prints as JSON).
4. **The tombstone non-deletion rules.** Both new comment blocks in `store.py`
   encode a concurrency argument: without a project lock (G4), no writer can
   prove the tombstone at `.tombstones/<slug>.md` is still the one it wrote, so
   deleting it can destroy a fact. Is the `contextlib.suppress(FileNotFoundError)`
   on the unlink correct — i.e. is "active file already gone" really always a
   racing soft-delete that completed the move, and never a case where the caller
   should have seen an error?
5. **`ONE_RAW_PER_BATCH` 1300 → 1380** in `tests/test_curate_budget.py`. B1 adds
   a sentence to `PLAN_SYSTEM`, which moves the plan-payload floor. Re-measured
   on this tree: no-facts one-raw floor 1327, with-a-fact floor 1364, no-facts
   two-raw ceiling 1393 ⇒ valid window [1364, 1392]. The point of the comment is
   that the upper bound alone is insufficient: a value in 1327..1363 passes every
   budget test that never commits a fact, then silently truncates the one that
   does. Please sanity-check the reasoning and the chosen value.

**Known risks / tradeoffs.**

- **Documented residue, deliberately not cleaned.** A resurrected slug's old
  tombstone stays on disk. Readers are unaffected (spec §1: `curated/` is
  authoritative, and a `.tombstones/` reader MUST exclude slugs present in
  `curated/`), and `prune_tombstones` collects it after the grace period. The
  alternative — deleting it — is exactly the bug class that produced two P1s on
  the v1 branch: a shared tombstone path deleted without ownership can destroy a
  fact outright. **If you think a tombstone should be reclaimed here, that is a
  decision for Andrew, not a fix to apply.**
- **G4 is the honest scope limit.** Several invariants here hold for a single
  mutating process, not across concurrent processes. That is written down as G4
  rather than papered over — three rounds were burned on the v1 branch trying to
  claim an invariant ("never both") the filesystem cannot provide.
- **ADR-0022 is `Proposed`, not `Accepted`.** Deliberate; accepted ADRs are
  immutable in this repo.
- **One inherited test pair was vacuous.** Mutation testing found that deleting
  the fold's `batch_count` gate broke nothing: both `fold_absent` tests return
  through earlier `_log_pass` calls that never take a fold.
  `test_no_fold_when_the_budget_stops_the_pass_before_any_batch_commits` covers
  the one path that reaches the pass-end log with `batch_count == 0`. If you can
  find another green-but-vacuous test here, that is a finding worth raising.

**How to verify.**

```sh
git rev-parse HEAD                     # expect the tip SHA named in the prompt
git diff main...feat/provenance-slice-b-v2
make ci                                # 5 stages; last run: 1349 passed, 1 skipped, 91.90% cov
uv run pytest tests/test_curator.py tests/test_store.py tests/test_curate_budget.py
uv run python scripts/check_store_chokepoint.py
```

**Out of scope.**

- The retired `feat/provenance-slice-b` @ `abac1f4` and its baton
  `docs/reviews/2026-07-20-provenance-slice-b.md` — kept as a content record
  only. Do not diff against it or treat its approval as carrying.
- Provenance **Slice A** (`core/graph.py`, the read side) — a later branch. The
  fold format is the contract it will read, so format critiques are in scope;
  the reader is not.
- `feat/store-write-lock-adr` / the G4 lock itself — a separate branch, still to
  be re-derived. G4's *existence* is in scope (is it documented honestly?); its
  implementation is not.
- The web UI, recommender, and anything else already on `main`.

---

## Reviewer findings  _(Reviewer — Codex)_

> _Awaiting round 1._
