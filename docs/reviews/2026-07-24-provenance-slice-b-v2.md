---
slug: provenance-slice-b-v2
status: approved
author: claude
reviewer: codex
branch: feat/provenance-slice-b-v2
diff: git diff main...feat/provenance-slice-b-v2
created: 2026-07-24
round: 3
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

**Round 1 verdict: CHANGES REQUESTED** — 3× P2, 1× P3, no P0/P1. Port surface,
leakage, tombstone reasoning, counters, and the budget recalibration all cleared
in Codex's non-blocking notes.

### P2-DOCS-PLAN-ACCURACY-001 — fold gate ≠ steps-7/8 gate
- **severity:** major (P2)
- **location:** `docs/adr/0022-curator-fold-journal.md:149`, `docs/how-it-works.md:909`
- **issue:** ADR + how-it-works say the fold's `batch_count > 0` gate is "the same
  condition that already gates steps 7–8." It is not: on the zero-commit
  budget-stop path (`tests/test_curate_budget.py:490`), prune (`engine.py:541`)
  and synthesis (`engine.py:559`) run unconditionally while only the fold
  (`engine.py:610`) is `batch_count`-gated.
- **suggested direction:** pick one truth and make docs+code agree. Either
  document that the fold has a *stricter* commit gate than steps 7–8 (if the code
  is intended), or commit-gate steps 7–8 too. Add a regression test on the
  first-call budget stop asserting the chosen prune/synth behavior alongside fold
  absence.
- **resolution:** resolved — **docs-only** (Andrew's call: the code stays). All
  THREE inaccurate places fixed, including the inline comment at `engine.py:536`
  the finding did not cite. ADR-0022 now states the fold gate is deliberately
  *stricter* and gives the reasoning: prune/synthesis are idempotent housekeeping
  over state already on disk (the property `--resynth` relies on), whereas an
  empty fold would assert a pass that consumed nothing, where silence is the
  honest record. `test_no_fold_when_the_budget_stops_the_pass_before_any_batch_commits`
  now pins both halves — and had to be strengthened first: asserting
  `"pruned_tombstones" in record` would have passed either way with no tombstone
  present, so it now seeds an expired tombstone with `tombstone_grace_days=0` to
  make prune observable. **Mutation-verified twice:** gating prune on
  `batch_count` → `assert 0 == 1`; gating `_synthesize` → node file absent.

### P2-TEST-GAP-001 — hallucinated-name test doesn't protect fold edges
- **severity:** major (P2)
- **location:** `tests/test_curator.py:593`
- **issue:** `test_hallucinated_from_raw_dropped_and_counted` asserts curated
  frontmatter + `dropped_from_raw` but never reads `fold.edges`. A mutation
  keeping `provenance` on `validated` while feeding the raw `from_raw` into
  `_ApplyResult.edges` (`engine.py:236`) would put ghost filenames into the
  trusted journal and stay green. The separate fold-edge test uses only valid
  names, so it can't catch it.
- **suggested direction:** read the journal in the adversarial test and assert
  `fold.edges == {"fact-a": ["r1.md"]}` with no ghost name anywhere in the edge
  map; keep the existing frontmatter/dropped-count assertions.
- **resolution:** resolved. `test_hallucinated_from_raw_dropped_and_counted` now
  reads the journal and asserts `fold.edges == {"fact-a": ["r1.md"]}`, plus a
  flattened check that no ghost name appears under any slug. The frontmatter and
  `dropped_from_raw` assertions are kept — distinct outputs. **Mutation-verified:**
  accumulating the raw `from_raw` into `_ApplyResult.edges` while leaving
  `provenance` validated produced `{'fact-a': ['r1.md', 'ghost-1.md',
  'ghost-2.md']}` — and got past both pre-existing assertions first, exactly as
  the finding predicted.

### P2-TEST-GAP-002 — "only applied soft-deletes" lacks a false-apply probe
- **severity:** major (P2)
- **location:** `tests/test_curator.py:687`, `:714`
- **issue:** the excluded supersession target is skipped via re-upsert, never via
  a *failed* `_safe_soft_delete`; the explicit-tombstone test exercises only a
  successful delete. Coverage confirms the false branches at `engine.py:444` and
  `:455` are uncovered. A refactor that appends/counts a removal *before*
  checking `_safe_soft_delete` would journal a delete that never happened and
  stay green.
- **suggested direction:** add missing-target probes for both `superseded` and
  explicit `tombstoned` paths, asserting the fold list is empty and the matching
  count is zero. Optional invalid-slug variant.
- **resolution:** resolved. Three probes added:
  `test_fold_superseded_omits_a_target_that_does_not_exist`,
  `test_fold_tombstoned_omits_a_target_that_does_not_exist`, and the invalid-slug
  variant `test_fold_tombstoned_omits_an_invalid_slug`. Each asserts the fold list
  is empty AND the matching summary count is zero. **Mutation-verified:** hoisting
  both append/increment pairs out of their `if _safe_soft_delete(...)` branches
  failed all three — and **every pre-existing test in `test_curator.py` still
  passed**, confirming the gap was total. The existing re-upsert/pinned tests are
  untouched.

### P3-DOCS-PLAN-ACCURACY-002 — `final_active` comment overstates synthesis
- **severity:** nit (P3)
- **location:** `src/neurobase/curator/engine.py:586`
- **issue:** comment says prune/synthesis "touch only `.tombstones/` and
  `nodes/`, never `curated/`," but `_synthesize` → `linkify.linkify`
  (`engine.py:253`) rewrites active curated bodies. The cached set is still sound
  for membership/count (paths + status preserved); only the stated reason is
  wrong.
- **suggested direction:** reword to "prune does not alter `curated/` membership
  and synthesis/linkify preserves curated paths/status" rather than "never
  touches the directory."
- **resolution:** resolved. Comment reworded to say prune does not alter curated
  *membership* and synthesis preserves curated paths/active status, and it now
  names linkify's body rewrite explicitly so the stronger false claim cannot be
  re-derived. `final_active` is unchanged; no extra `list_curated` call.

**Verdict:** changes-requested — two mutation-surviving test gaps on the new
provenance contract plus a doc/code equivalence that isn't true.

---

## Round 2 — 2026-07-24 (tip `35556c6`)

**Verdict: CHANGES REQUESTED** — `P2-TEST-GAP-001`, `P2-TEST-GAP-002` and
`P3-DOCS-PLAN-ACCURACY-002` all confirmed **fixed**, with Codex verifying the
named mutations are killed and that the previously-missed engine branches no
longer appear in the coverage report. No new finding IDs opened; no regression
from the fix commit (`git diff 62d26fb..35556c6 -- src/` confirmed comments
only). Codex also independently validated the strengthened budget-stop test.

`P2-DOCS-PLAN-ACCURACY-001` came back **partially fixed** — two more same-gate
claims survived the round-1 fix:

- `docs/neurobase-spec-appendix.md:274` — the **normative spec**, which this repo
  treats as law.
- `src/neurobase/curator/engine.py:615` — the comment sitting *nearest the
  executable fold gate*.

**The lesson, recorded because it cost a round.** Round 1 fixed the three
*instances* of the false claim that were in front of me (two cited, one found
next to them) instead of eliminating the *claim*. A re-raised finding ID means
the previous fix hit the reviewer's reproduction, not the rule. The correct move
— done this round — was a repository-wide search for the equivalence assertion,
then fixing every hit and re-running the search as the verification step.

**Resolution (round 2):** both remaining claims corrected in the same docs-only
spirit; the spec now states the fold gate is stricter and why, and the engine
comment cross-references the prune-call comment rather than restating the gate.
Verified by re-running the repo-wide sweep: the only surviving matches are the
ADR sentence that *denies* the equivalence and this baton quoting the finding.
`git diff -- src/` contains no non-comment line. `make ci` green: **1352 passed,
1 skipped, 91.95% cov**, all 5 stages.


---

## Round 3 — 2026-07-24 (tip `735a651`) — ✅ APPROVED

**Verdict: APPROVED**, 0 open findings. All four IDs closed. Codex ran its own
repository-wide search (not just the diff) and found no surviving assertion that
the fold gate and the steps 7–8 gate are equivalent; it confirmed the corrected
spec/engine prose matches the executable flow, that the `src/` diff changes
comments only, and that the budget-stop regression still pins both halves.
`make ci` re-run independently: **1352 passed, 1 skipped, 91.95% cov**, 5/5
stages. Worktree clean before and after.

## Outcome

Three rounds. The B1/B2 decision transferred intact from the retired v1 branch;
**every finding raised in this relay was against the re-derivation, not the
decision** — which is exactly what the re-derive call (option (b)) predicted, and
a reasonable argument that re-deriving beat porting the 132-line divergence.

Findings by round: R1 four (3× P2, 1× P3) → R2 three fixed + one partially →
R3 approved.

**Two durable lessons from this relay:**

1. **A re-raised finding ID means the fix hit the reviewer's reproduction, not
   the rule.** `P2-DOCS-PLAN-ACCURACY-001` cost an extra round because round 1
   corrected the three *instances* of a false claim that were in front of it
   instead of eliminating the *claim*. The two that survived were the two that
   mattered most: the normative spec, and the comment nearest the executable
   gate. The fix that worked was a repo-wide search for the assertion, with a
   re-run of that search as the verification step. (This lesson was already on
   record from the store-lock relay and was re-learned anyway — the tell is the
   ID coming back, and it should trigger a search, not another edit.)
2. **Assume a test is vacuous until a mutation proves otherwise — including the
   test you just wrote to close a finding.** Codex found two vacuous inherited
   tests (`P2-TEST-GAP-001`, `-002`). The implementer then wrote a *third* while
   fixing `P2-DOCS-PLAN-ACCURACY-001`: `assert "pruned_tombstones" in record`
   passes whether or not prune runs, because that test had no tombstone to
   prune. Caught pre-commit only by asking "what mutation would kill this?" —
   which is the habit, not the checklist. Fix: seed an expired tombstone and set
   `tombstone_grace_days=0` so prune becomes observable.

**Ready to merge** at `735a651`, subject to Andrew's call on ADR-0022's status
(deliberately left `Proposed` — accepted ADRs are immutable in this repo).
