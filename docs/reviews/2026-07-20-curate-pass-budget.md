---
slug: curate-pass-budget
status: draft
author: claude
reviewer: codex
branch: runaway-guard-split
diff: git diff 5e6fefa...a708034
created: 2026-07-20
---

# Review: bound every curation pass with a call/raw/chunk/time budget

## Brief  _(Author — Claude)_

**Read packet 1 first.** This is **packet 2 of 2**, and it builds directly on the
containment reviewed in `2026-07-20-runaway-containment.md`. Its whole premise is
that those three guards are *not enough*, so it is hard to judge in isolation.
Status stays `draft` until packet 1 has a verdict; flip it to `awaiting-review`
then.

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
git diff 5e6fefa...a708034
uv run python scripts/ci.py          # 1077 passed, 1 skipped, 91.14%
uv run pytest tests/test_curate_budget.py -q    # 21 tests
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

**Verdict:** _(pending)_
