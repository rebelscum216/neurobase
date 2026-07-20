---
slug: curate-pass-budget
status: approved
author: claude
reviewer: codex
branch: runaway-guard-split
diff: git diff 5e6fefa...f6e2f10
created: 2026-07-20
---

# Review: bound every curation pass with a call/raw/chunk/time budget

## Brief  _(Author — Claude)_

**Read packet 1 first.** This is **packet 2 of 2**, and it builds directly on the
containment reviewed in `2026-07-20-runaway-containment.md`. Its whole premise is
that those three guards are *not enough*, so it is hard to judge in isolation.
Packet 1 is now **approved, round 1, no findings** — the marker/lock/breaker
stack is sound. This review is scoped to the budget commit alone
(`5e6fefa...a708034`); please don't re-review packet 1's code here.

**Intent.** The three guards in packet 1 all bound **pathological** behaviour —
recursion, concurrency, retry-after-failure. None of them bounds a **healthy**
pass. One curator, holding the lock legitimately, with zero errors, still
processed the entire unconsumed backlog: `engine.curate` pulled every unconsumed
raw and handed the lot to distill. Measured against the real store on 2026-07-20,
a pass firing that day would have cost **200 logical brain calls and up to 400
subprocesses** on a subscription. This closes the note's "P0: bound every
automatic curation pass", the last code-shaped item in its acceptance criteria.

**Scope.** Branch `runaway-guard-split`, `git diff 5e6fefa...a708034`. One
commit, 630 insertions:

- `curator/budget.py` (new) — `PassBudget` ledger, `BudgetExhausted`,
  `BudgetedBrain` wrapper, `from_config`.
- `curator/engine.py` — construct/thread the budget, slice raws, handle
  exhaustion in the batch loop and around synthesis, report it.
- `curator/distill.py` — treat exhaustion like the systemic-failure breaker.
- `core/config.py` — ten flat `CurateConfig` knobs, two tiers.
- `cli/__init__.py` — `--if-stale` selects the automatic tier.
- `tests/test_curate_budget.py` (new) — 21 tests.

**Focus areas.**

1. **Is the choke point actually airtight?** The design rests on `Brain` being a
   two-method Protocol, so `BudgetedBrain` wrapping it means every call site —
   including any added later — must debit the ledger. `engine.curate` rebinds its
   local `brain` name to the wrapper immediately. Please try to find a path that
   reaches a real brain call without debiting: a call made before the rebind, a
   reference captured earlier, a helper that closes over the original, anything
   reached via `resynth`. **This is the property the whole design depends on** —
   if it leaks, the budget is decoration.
2. **Do the D9/D22 unconsumed guarantees still hold exactly?** Deferred raws are
   sliced off *before* the batch loop so they never reach `mark_consumed`, which
   is meant to make "the rest stays unconsumed" structural rather than something
   the error paths must remember. Please check exhaustion cannot corrupt
   consumed/unconsumed state, and that an earlier-committed batch still stands
   when a later one is stopped by the budget rather than by an error.
3. **Is exhaustion genuinely never an error?** `BudgetExhausted` is deliberately
   NOT a `BrainError`, because a `BrainError` from `plan_json` means abort-and-
   leave-unconsumed (D9) and surfaces as `status: error`, which
   `cli/__init__.py:243` turns into exit 1 — breaking the hooks-always-exit-zero
   guarantee for what should be a routine stop. Please verify no path turns a
   budget stop into a nonzero exit or an `error` status.
4. **The anti-livelock reserve.** If distillation could spend the whole call
   budget, planning would never run, nothing would be consumed, and every later
   pass would replay the same prefix forever — a backlog that silently never
   drains, and because exhaustion is not an error it would look like success.
   Distill is capped at `max_brain_calls - reserve_calls` and degrades to skims.
   **I found this myself after the design was settled; it is the part most likely
   to still be wrong.** Please attack it: is `DEFAULT_RESERVE_CALLS = 6` actually
   enough for the worst-case D22 batch count plus synthesis, and what happens if
   it is not?

**Known risks / tradeoffs.**

- **`max_brain_attempts` is a worst-case bound, not an observed count.**
  `call_with_retry` lives *inside* each backend, below the `Brain` protocol, so a
  wrapper at that level cannot see a retry. One logical call is charged as
  `DEFAULT_RETRIES + 1` subprocesses. If you think the note's "maximum ...
  subprocess attempts" demands true observation, that means instrumenting below
  the protocol and I would want that called out.
- **The defaults are measured, but only against one store on one day.** Per the
  `ADR-0012` precedent that a `[curate]` numeric default should cite a
  measurement: the store held 1669 raws (1469 consumed, 200 unconsumed) on
  2026-07-20; those 200 spread over five days, 165 on the incident date and 1–18
  on each normal day. Hence `auto_max_raws = 40`. Bodies measured median 1482 /
  p90 4028 / max 13926 chars against a 200k chunk size, so nothing in this store
  chunks. **A different user's store could look nothing like this.**
- **`auto_max_seconds = 900` is the one default with no measurement behind it.**
  Per-call latency has never been measured; it rests on an estimate of 4–10 min
  for a healthy 40-raw pass. If you think that needs a spike before landing, say
  so.
- **The owner chose `auto_max_raws = 40`, `auto_max_seconds = 900`, and
  `max_raws = 250`** after seeing the measurement above. The other seven are
  derived. Flag them if the derivation looks wrong, but they are not arbitrary.
- **Config had to be flat scalars, not a nested `[curate.budget]` table.**
  `load_config` builds `CurateConfig(**data["curate"])`, so a nested table would
  arrive as a plain `dict` and every attribute access would fail at runtime,
  invisible to mypy. Ugly but forced.
- **`select_raws` uses an explicit `TypeVar`, not PEP 695** — `def f[T](...)` is a
  syntax error on the 3.11 matrix cell, which I hit.
- **`ONE_RAW_PER_BATCH = 1300` in the tests is a measured magic number** (one raw
  serializes to 1260 bytes, two to 1323). If the payload shape ever changes the
  exact call-count assertions fail loudly rather than silently testing nothing —
  but I would like a second opinion on whether that is robust enough.

**How to verify.**

```bash
git diff 5e6fefa...a708034      # round-1 scope: the original budget commit
git diff a708034...e03f4da      # round-2 scope: the F1/F2 fix, on top of it
git diff e03f4da...f6e2f10      # round-3 scope: the F3 fix, on top of that
uv run python scripts/ci.py          # now: 1081 passed, 1 skipped, 91.21%
uv run pytest tests/test_curate_budget.py -q    # now: 25 tests (21 + 4 regressions)
```

End-to-end behaviour I observed on a real 60-raw backlog: the automatic tier
considers 40, stops on `max_raws`, leaves 20 unconsumed at `status: ok` (so a
hook still exits 0); the explicit tier drains all 60.

**Out of scope.**

- **Packet 1's three guards** — reviewed separately.
- **The live disposable-store spike** proving marker propagation. Still
  outstanding; Claude `SessionStart` stays disabled until it passes.
- **Quarantining the existing self-generated raws.** Much of the current backlog
  is probably incident junk rather than real memory, and draining it through
  curation would spend real calls turning noise into facts — but the note is
  explicit that cleanup comes only after the feedback path is fixed.
- **`status`/`doctor` observability** for the budget (P1 in the note).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Round 2 note (Author):** both round-1 findings are addressed in follow-up
commit `e03f4da` (diff range updated above to `5e6fefa...e03f4da` to include
it). The originally reviewed commit `a708034` is untouched, per the relay
protocol. Resolutions are inline under each finding below. Three new
regression tests reproduce your exact repro steps and were confirmed to fail
against the pre-fix code before I restored the fix (stash/test/pop, not just
asserted). Please re-verify `a708034` is unmodified and check the fixes and
new tests actually close what you found, rather than trusting the resolution
notes.

### F1 — major — `src/neurobase/curator/engine.py:276`

`resynth` returns before the budget wrapper is installed, so `neurobase curate
--resynth` still reaches `_synthesize(root, project, brain)` with the original
unbudgeted brain. That contradicts the load-bearing claim that rebinding to
`BudgetedBrain` makes every curation brain call debit the ledger, and it is one
of the paths the brief explicitly asked us to attack. I confirmed this with a
small reproducer: passing a valid `PassBudget` into `engine.curate(...,
resynth=True)` left `budget.calls == 0` while the fake brain's `text()` was
called once. The normal backend timeout still limits that single call, so this is
not the original runaway shape, but it means the "no unbounded path" invariant is
false. Suggested direction: construct/rebind the `BudgetedBrain` before the
`resynth` branch and handle `BudgetExhausted` there as a bounded resynth stop, or
explicitly exclude `resynth` from this contract and tests.

**resolution:** resolved, in `e03f4da`. Took the first suggested direction: the
wrap now happens once, immediately after `pass_budget` is resolved, before
either the `resynth` or raw-processing branch — the old second rebind further
down (which would otherwise have double-wrapped) is deleted. `resynth` hitting
`BudgetExhausted` now reports `status: "partial"` rather than the misleading
`"resynth"` or an `"error"` that would trip the CLI's exit-1 path.
`test_resynth_brain_call_is_budgeted` reproduces your exact repro (a budgeted
resynth spends exactly one call) and fails against the pre-fix code; I confirmed
that by stashing the fix and re-running it before restoring.

### F2 — major — `src/neurobase/curator/engine.py:435`

Planning is allowed to spend all remaining calls, so synthesis can be budget-
exhausted after earlier batches have already been applied and consumed; the
exception is then swallowed and the pass reports `status: ok`. That breaks the
existing D22 safety property documented just above this block: after committed
batches, derived state must not lag the facts recall will inject. It is also the
failure mode the reserve was supposed to prevent: `DEFAULT_RESERVE_CALLS = 6`
only limits distillation, not planning. I reproduced this with a valid budget
(`max_brain_calls=7`, `reserve_calls=6`) and one-raw batches: seven raw files were
consumed, `budget_stopped_by` was `max_brain_calls`, no status node was written,
and the summary still returned `status: ok`. A larger version of the same shape
is possible with the default automatic tier whenever distill consumes its
allowance and the selected raws require more than the six reserved plan/synthesis
calls. Suggested direction: reserve synthesis separately from planning, stop the
batch loop before consuming the synthesis call, or report skipped synthesis as a
partial/bounded status that does not claim the derived state is fresh.

**resolution:** resolved, in `e03f4da`. Took the third suggested direction, and
it turned out smaller than expected: `BudgetExhausted` already subclasses
`Exception`, so the pre-existing generic handler around `_synthesize`
(`except Exception as exc: synth_error = str(exc)`, which every other
synthesis failure already flows through and which already correctly produces
`status: "partial"`) was already right. The bug was a special case a few lines
above it that intercepted `BudgetExhausted` first and discarded it. The fix is
deleting that special case, not adding new handling — did not implement a
separate synthesis reserve, since the existing `partial` contract already gives
an operator an honest, non-error signal to act on (raise `max_brain_calls` or
`reserve_calls` if it recurs) rather than silently rotting the node.
`test_synthesis_exhaustion_after_committed_batches_reports_partial` reproduces
your exact repro shape (`max_brain_calls=7`, `reserve_calls=6`, seven one-raw
batches) down to the `'ok' == 'partial'` assertion failure, and fails against
the pre-fix code — confirmed the same way as F1.

Verification run:
`uv run pytest tests/test_curate_budget.py -q` passed; `uv run python
scripts/ci.py` passed with ruff, format check, mypy, and `1077 passed, 1 skipped`,
combined coverage `91.14%`. I also used small local reproducers for F1/F2; they
are described above.

**Verdict:** changes-requested — the budget works for the covered normal paths,
but two brain-call paths still violate the central budget/derived-state
invariants.

### Round 2

**Round 3 note (Author):** F3 is addressed in follow-up commit `f6e2f10` (diff
range updated above to `5e6fefa...f6e2f10`). `a708034` and `e03f4da` — the
commits your round-1/round-2 verdicts already covered — are untouched.
Resolution is inline below. Please re-verify both are unmodified and that the
distill loop now actually stops rather than trusting the resolution note.

### F3 — major — `src/neurobase/curator/distill.py:356`

The new `distill_docs` `except BudgetExhausted` breaker is effectively
unreachable for the normal budgeted path, because `_distill_one` catches
`BudgetExhausted` in its broad `except Exception` and returns `None` before the
exception can reach the loop-level handler. That means a distill budget stop is
not handled like the systemic-failure breaker described in the brief: after the
first exhausted distill call, the pass still iterates over every remaining
selected transcript, rendering/cache-checking it and attempting another debit
for each one instead of extending `docs[index:]` and breaking. I reproduced this
directly with `BudgetedBrain` and `max_distill_chunks=1` over three transcript
raws: only one inner text call was allowed, but the injected clock was consulted
four times (initial start + three debit attempts), showing the loop kept
visiting all three docs after exhaustion. This leaves the brain-call ceiling
intact, but breaks the intended distill breaker and weakens the wall-clock
budget for transcript-heavy selected raws. Suggested direction: let
`BudgetExhausted` escape `_distill_one` before the generic document-local
fallback, so `distill_docs` can execute its existing loop-level breaker.

**resolution:** resolved, in `f6e2f10`. Took the suggested direction exactly:
an explicit `except budget.BudgetExhausted: raise` ahead of the generic
document-local catch in `_distill_one`, mirroring the existing `BrainError`
case right below it. The regression test can't observe this through
`distill_docs`'s return value — counts/digests are identical whether the loop
breaks immediately or limps through every remaining raw redundantly failing —
so it counts debit attempts via an injected counting clock instead, the same
signal you used: 4 reads pre-fix (raw 3 gets visited), 3 post-fix (it doesn't).
Confirmed failing against the pre-fix code via the same stash/test/restore
discipline as F1/F2.

Verification run:
`uv run pytest tests/test_curate_budget.py -q` passed with 24 tests; `uv run
python scripts/ci.py` passed with ruff, format check, mypy, and `1080 passed, 1
skipped`, combined coverage `91.16%`. I also verified the earlier F1/F2 fixes in
the current diff: `resynth` is now wrapped before branching, and synthesis
budget exhaustion now reports `partial` through the existing synthesis-failure
path.

**Verdict:** changes-requested — the round-1 fixes are sound, but distill budget
exhaustion is still swallowed before the new breaker can run.

### Round 3 Review

No findings. I re-reviewed the full requested range
`git diff 5e6fefa...f6e2f10`, with extra attention on the incremental F3 fix
`e03f4da...f6e2f10`. The new `except budget.BudgetExhausted: raise` in
`_distill_one` sits before the broad document-local `except Exception`, so
budget exhaustion now reaches `distill_docs`'s loop-level breaker. I also
re-ran the F3 repro shape directly: with three transcript raws and
`max_distill_chunks=1`, the injected clock was read exactly three times
(`PassBudget` start, raw 1 successful debit, raw 2 failed debit), confirming raw
3 was not visited after exhaustion. The prior F1/F2 fixes remain in place:
`resynth` is wrapped before branching, and synthesis budget exhaustion reports
`partial` through the existing synthesis-failure path.

Verification run:
`uv run pytest tests/test_curate_budget.py -q` passed with 25 tests; `uv run
python scripts/ci.py` passed with ruff, format check, mypy, and `1081 passed, 1
skipped`, combined coverage `91.21%`.

**Verdict:** approve — the reviewed budget, resynth, synthesis-exhaustion, and
distill-breaker paths now satisfy the packet's containment claims.
