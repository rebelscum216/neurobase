# Batch composition decides what a curate pass extracts (2026-08-07)

**Question.** The double-raw residual shipped with `neurobase wrap` was recorded as
"fires, but does not duplicate — at `batches: 1`, which is the untested condition"
(`notes/2026-08-01-memory-layering-ideas.md` §15.3). Force multi-batch and find out.

**Answer: the question was the wrong one.** Multi-batch does not duplicate. What it exposed
instead is [`G19`](../../known-gaps.md) — a plan batch was capped by **bytes** but not by
**raw count**, so a realistic backlog arrives as one batch and extracts **nothing**.

Harness: [`2026-08-07-g19-batch-crowding.py`](2026-08-07-g19-batch-crowding.py). Every run
copies the store and rebuilds the fixture first; the live store was never opened for write.
Brain `codex-cli 0.146.0-alpha.9.2`, `distill="off"` throughout.

---

## 1. Multi-batch does not duplicate

Three sessions in the live store each hold two raws (an `agent-authored` wrap digest and a
`SessionEnd` skim). Split every pair across separate plan batches:

| arm | batches | upserts | facts | duplicate/competing facts |
|---|---|---|---|---|
| control | 1 (all 6 raws) | 2 | 18 → 19 | none |
| multi | 4 (`[3,1,1,1]`) | 2 | 18 → 19 | **none** |

⭐ **Provenance accumulates across batches.** `upsert_curated` merges provenance
(prior + new, deduped) and the batch loop reloads curated facts between batches, so
successive same-slug upserts on one fact collect every raw. Later in §3 a single fact cites
**four** raws that were planned in four different batches. A *single* upsert cannot cite a
split pair — `from_raw` is validated against the current batch only — but that was never
the mechanism the merge relied on.

⛔ **The first attempt at this was VACUOUS and nearly reported as a result.** It returned
`status: ok`, `batches: 4`, `upserts: 0`, `21 → 21` facts, which reads as "no duplication".
It was a no-op: the facts those raws had produced were still on disk and already cited both
raws, so the planner correctly wrote nothing. **A no-op cannot distinguish "the mechanism
held" from "the mechanism never ran."** The fixture needed the derived facts deleted before
the fold had any work to do.

⚠️ **And one arm each was not enough.** With the fixture fixed, control and multi both
produced 2 upserts and 19 facts with **zero slugs in common**. At n=1 the run-to-run
variance swallowed the comparison entirely. Replication is what turned this into a result —
the same lesson the [2026-08-04 stance test](2026-08-04-authorial-stance.md) learned when
its headline effect failed to replicate.

## 2. Where the raws actually went (4 replicates per arm)

```
control (1 batch of 6)          multi (4 batches)
  hook-activation-quirks          hook-activation-quirks      <- f8083cb5 authored
  hook-cwd-resolution-diagnostics pr-ci-cost-discipline        <- 66b54a2e skim
  hook-activation-quirks          pr-count-ci-credit-policy    <- 66b54a2e skim
  unregistered-cwd-capture-...    pr-ci-credit-policy          <- 66b54a2e skim
```

Every control run drew on `f8083cb5`; **no control run ever cited the `66b54a2e` skim.**
Every multi run did. Same raws, same brain — only the batching differed.

⭐ **A fact citing BOTH raws of a pair appeared in 1 of 4 control runs**, not 4 of 4. So
§15.1's "every resulting fact cites both raws" was never a property of single-batch folding;
it described those particular passes.

## 3. Crowding, on a second and independent fixture

The 10 `codex` raws that [`G18`](../../known-gaps.md) counted as consumed-but-cited-by-
nothing — its own "31%" population. No facts deleted; the only question is whether raws that
contributed nothing contribute when they are not competing.

| arm | runs | upserts | raws cited | facts |
|---|---|---|---|---|
| one batch of 10 | 3 | **0, 0, 0** | **0/10 ×3** | 21 → 21 |
| ten batches of 1 | 3 | 6, 6, 6 | **6/10 ×3** | 21 → 23 |

**The same six raws contributed in all three isolated runs.** The other four never did, in
any run, and look genuinely thin. So G18's 31% is roughly **6 suppressed + 4 empty**, not
10 unknowables.

One of the facts produced, `doctor-model-mixture-review-findings`, cites `019fd214`,
`019fd231`, `019fd244`, `019fd255` — four raws, four separate batches. That is §1's
cross-batch merge, observed.

## 4. Where extraction falls off (sweep, 3 reps per size)

| raws/batch | runs | raws cited per run | mean | runs producing new facts | plan calls | cited/call |
|---|---|---|---|---|---|---|
| 1 | 6 | 6, 7, 5, 6, 6, 6 | 6.0 | **6/6** | 11 | 0.55 |
| 2 | 3 | 6, 8, **0** | 4.7 | 2/3 | 6 | 0.78 |
| **3** | 3 | 7, 7, 4 | 6.0 | **3/3** | 5 | **1.20** |
| 5 | 3 | 0, 5, 1 | 2.0 | 1/3 | 3 | 0.67 |
| 10 | 6 | 0, 0, 4, 0, 0, 0 | 0.7 | **0/6** | 2 | 0.33 |

*(sizes 1 and 10 pool the §3 runs with the sweep's, same conditions.)*

All ten raws together are **63,511 bytes** against a 262,144 byte cap. Request size was
never near a limit — **bytes are the wrong proxy for what limits extraction.**

⭐ **Why the default is 3, not 1.** Batches of 3 match 1-per-batch extraction at **fewer
than half the calls**. Isolation buys nothing over small batches; only *large* batches are
harmful.

⚠️ **Not a clean cliff.** Sizes 1-3 extract reliably, 5 and 10 do not, and the transition
sits somewhere between 3 and 5 — n=3 at the interior points cannot place it more precisely.
A single run at any size can come back empty; see the `0` at size 2.

## 5. Synthesis cost — why folding every session is expensive

`curate --resynth` isolates `_synthesize`: one brain call, no planning.

| run | 1 | 2 | 3 | mean |
|---|---|---|---|---|
| seconds | 21.2 | 24.8 | 23.0 | **23.0** |

Solving the sweep's two call-count conditions predicted **~27.1s** before it was measured.
So a plan call is ~12s and **synthesis is ~66% of a 3-raw pass**. Per-session real-time
folding costs 2 calls/raw against 2 calls per 3 raws — the measurement that settled
batching over real-time curation, and that made deferring synthesis not worth its
complexity (~8s/session).

🆕 **Incidental, and it became [`G20`](../../known-gaps.md):** the three resynths regenerated
the node at **4,173 / 4,198 / 5,313 bytes** from an unchanged 4,820 — up to 27% variation
with no input change. That node is what `inject` puts in front of an agent at session start.

## Limits

One project, one brain, `distill="off"`, 21 active facts sharing the plan payload
throughout. **`plan_max_raws = 3` is calibrated to that fact set, not derived** — a larger
curated set crowds raws harder and the threshold should be expected to fall. Re-run §4
before trusting the default at a materially bigger store.

## Outcome

`plan_max_raws = 3` and `plan_trigger_raws = 3`, shipped in PR #33 (`3117ad4`), with
`G19` fixed, `G20` filed, and `G18` updated rather than closed — the fix shrinks its
population without making a raw's contribution observable.
