---
slug: doctor-model-mixture
status: approved
author: claude
reviewer: codex
branch: feat/doctor-model-mixture
diff: git diff main...HEAD
created: 2026-08-05
rounds: 4
last_reviewed: 9a6403f152a5e29bcdb458b696c71bdccd1a9835
---

> **Status note (2026-08-05).** The Brief below opened the relay and describes the
> branch at `1c8df4e`. Four rounds ran; the branch is three fix commits further on,
> and one of those rounds **deleted** a mechanism the Brief defends. The Brief is
> left as written — it is the record of what was handed over, including the claim
> that turned out to be wrong — and the outcome is at
> [Reviewer findings](#reviewer-findings--reviewer--codex).

# Review: `doctor` reports the model mixture PR #17 was already journaling

## Brief  _(Author — Claude)_

**Intent.** PR #17 journals `brain_usage.models` — per-model call counts — on every
curate record, but nothing ever read them, so a pass whose model mixture changed
halfway through looked identical to a clean one. `doctor` already reads this journal
for `curate health`, so this is the read side of a fact the engine was already
writing: no new contract, no new file, nothing written (a `DOCTOR` store handle).

**Scope.** Branch `feat/doctor-model-mixture`, `git diff main...HEAD` (single commit
`1c8df4e` at hand-over, +395/-0). Key files:

- `src/neurobase/cli/diagnostics.py` — new `_model_mixture` (bucket the counts),
  `_last_pass_that_spent_calls` (which record to report on), `_model_mixture_check`
  (the check), and one line wiring it into `collect_checks`.
- `tests/test_doctor_model_mixture.py` — **new**, 21 tests driving the real engine
  with a real `BrainUsage`, per the standard `test_curate_health_end_to_end.py` sets.

**Focus areas.** Six judgement calls, volunteered for the reviewer to attack rather
than left implicit: the `0 < count < calls` rejection rule; the deliberate non-reuse
of the spike's `compare_arms`; anchoring on the last brain-spending pass; four
deliberate silences where `curate health` already speaks; the claim that every model
lands in exactly one bucket; and `warn` as the severity for something whose remedy
says no action is required.

**Known risks / tradeoffs.** More than one model per pass is the *healthy* case — one
`claude -p` call reports the main model plus a `claude-haiku-4-5` auxiliary — so
`len(models) > 1` would fire on every clean pass. The rule used here is the spike's
(`docs/notes/spikes/2026-08-04-stance-test.py`, `model_verdict`): a per-model count
that does not cover the pass. **This check cannot fire on the author's machine as
configured** — the brain is pinned to `codex-cli`, which exposes no `usage`, so the
live journal carries `brain_usage: null`. Verified by reading the store, not inferred.

**How to verify.** `make ci` (6/6 green at hand-over). Five mutations were run against
the guards — disabling partial detection, the naive ">1 model" rule, anchoring on the
last record, emitting where the check must stay silent, and a silent-drop hole found
and closed mid-build — each caught.

**Out of scope.** Everything outside the two files; in particular `brain/base.py`,
`curator/engine.py`, and the untouched parts of `diagnostics.py`.

---

## Reviewer findings  _(Reviewer — Codex)_

Four rounds. **No blocker or major finding at any point.** Every finding was verified
against the code by the author before triage, and **none was disputed**. Full round
detail, prompts and drops are in the vault card `reviews/doctor-model-mixture.md`.

### F1 — `P2-CORRECTNESS-001`: claims that outran the journal
- **severity:** major (P2) · **location:** `src/neurobase/cli/diagnostics.py`
- **issue:** the through-line of the whole relay. R1: a partial count was reported as
  a *proven* model change with a cause attached ("fallback or capacity") the record
  cannot establish — `record` increments `calls` for every successful envelope but a
  model only when that envelope carries `modelUsage`, so one model with a silent
  envelope is indistinguishable from a real change. R2: the replacement coverage
  proof was itself unsound (`record` keys by `str(model)`, so colliding keys reach
  `count == calls` without full coverage) and the ambiguous branch overclaimed from
  the other side. R3: the "neutral" headline still asserted missing coverage.
- **suggested direction:** render journal facts as journal facts; do not infer
  coverage, service, or cause from aggregate counts.
- **resolution:** **partly resolved — one residual accepted.** `4337d67` added a
  coverage argument; `b949ee5` **deleted it** on the owner's call rather than narrow
  it a third time, collapsing every uncovered shape into one line that states the
  counts and says the journal cannot distinguish the causes; `9a6403f` restated every
  remaining headline as a recorded fact. **Accepted residual:** the all-equal headline
  ("recorded every model on all N call(s)") still distributes an aggregate across
  individual calls, which the collision fixture disproves. Not fixed because the
  counterexample cannot cross the real input boundary — it needs a Python dict holding
  raw keys `1` and `"1"`, while the CLI envelope arrives as JSON, whose object keys
  are unique strings. Reviewer: *"Required fix: none before merge."*

### F2 — `P2-CORRECTNESS-002`: the wrong record was reported on
- **severity:** major (P2) · **location:** `diagnostics.py`, pass selection
- **issue:** absent `brain_usage` was read as "spent no calls". It is not: the codex
  and API brains expose no `BrainUsage` at all, and `--resynth` journals none though
  `_synthesize` spends a real budgeted call. The scan skipped those newer real passes
  for an older instrumented one — wrong precisely after a backend switch, when the
  stale warning describes a brain no longer in use. R3 added the failed-preview case:
  `status: "error"` with `dry_run: true`, the shape that exists so "a health consumer
  does not read a failed *preview* as a failed pass".
- **suggested direction:** distinguish neutral outcomes from substantive passes whose
  telemetry is merely unavailable.
- **resolution:** **resolved** (`b949ee5`, `9a6403f`). `_newest_substantive_pass`
  skips the neutral statuses *and* anything carrying `dry_run: true`, stops at the
  first real pass, and reports it as untelemetered rather than reaching further back.

### F3 — `P2-OBSERVABILITY-003`: damaged history was not silent
- **severity:** major (P2) · **location:** `diagnostics.py`, log-state gate
- **issue:** `_read_curator_log` returns usable entries *alongside* `DAMAGED`, and an
  unrecognized status leaves the log `OK` — so the check printed a confident model
  verdict beside `curate health`'s "health unknown".
- **resolution:** **resolved** (`4337d67`). Suppressed unless the log is `OK`, reusing
  `_classify_curate_history`'s own `saw_unknown` so the two lines cannot disagree
  about the same records.

### F4 — `P2-CORRECTNESS-004`: the producer's domain was wrong in both directions
- **severity:** major (P2) · **location:** `diagnostics.py`, usage validation
- **issue:** `bool` subclasses `int`, so a JSON `true` count read as healthy. Then
  container-level scalars (`brain_usage: true`, `"x"`, `[]`, `{}`) and a non-dict
  `models` still reached `ok`, while a truncated two-key block passed as valid.
  Conversely `calls: 0` was warned about as corrupt though it is **producer-valid** —
  a fresh `BrainUsage` writes a complete seven-key zero block and `ClaudeCLIBrain`
  records only after its success checks, so a pass whose every call failed produces
  exactly that.
- **resolution:** **resolved** (`4337d67`, `b949ee5`, `9a6403f`). `_counter` requires
  `type(x) is int`; an absent key (legitimately uninstrumented) is distinguished from
  a present malformed one; validation matches `as_dict()`'s real domain, with extra
  fields tolerated so an older doctor cannot call a newer record corrupt.

### F5 — `P2-OBSERVABILITY-005`: counts attributed to digests
- **severity:** major (P2) · **location:** `diagnostics.py`, remedy text
- **issue:** the remedy told the reader the pass's *digests* came from different model
  sets, but `models` aggregates every envelope regardless of stage. In the suite's own
  central fixture distillation falls back deterministically, so the two observations
  come from planning and synthesis and **no digest was brain-produced at all**.
- **resolution:** **resolved** (`b949ee5`). Rescoped to aggregate brain calls, and the
  remedy now states the per-stage attribution gap outright.

### F6 — `P3-DOCS-006`: stale comments contradict the corrected collision model
- **severity:** nit (P3) · **location:** `diagnostics.py:1045`, `:1089`, and a
  unit-test docstring
- **issue:** the bucket signature still labels counts "served", and an inline comment
  plus a test docstring still call an above-total count unreachable from `record` —
  now contradicted by both the corrected docstring and the executable collision
  fixture. A maintainer following them could reintroduce the removed inference.
- **resolution:** **deferred** — optional cleanup, no runtime effect. ID preserved.

### F7 — `P3-TEST-GAP-007`: three schema guarantees not independently mutation-covered
- **severity:** nit (P3) · **location:** `diagnostics.py:1018`, the test suite
- **issue:** the suite would not catch removing `cache_creation_input_tokens` from
  `_USAGE_COUNTER_FIELDS`, dropping the non-negative requirement, or narrowing
  `cost_usd` to float-only — the truncation sweep omits that field, every helper uses
  non-negative counters, and every cost fixture is `0.0`.
- **resolution:** **deferred** — implementation confirmed correct; coverage only. ID
  preserved.

**Verdict:** **approve (with nits)** — *"the cumulative state at `9a6403f` is safe to
merge with `P2-CORRECTNESS-001` explicitly accepted as the named residual… No further
relay round is requested."*

### Notes for the record

- **Tests 21 → 58**, all engine-driven. **15 mutations** were run by the author across
  the four rounds and every one was caught — including reintroducing the naive
  ">1 model" rule, the stale anchor, and the "model set changed … digests" wording.
- One test-design defect was found by the reviewer and removed: a keyword filter that
  banned `"does not cover"` while a test positively *required* the equivalent
  `"did not all cover"`, and which any rephrasing would have passed. The under-counted
  branch's detail and remedy are now asserted **in full** against a module constant.
- Reviewer's final checks at `9a6403f`: 58 focused tests, full suite 1816 passed /
  1 skipped, `ruff check` over the tree, `mypy --no-incremental src tests`, plus a
  shape matrix over the usage-schema boundaries. The author ran `make ci` 6/6.
- ⚠️ **The check cannot fire while the brain is pinned to `codex-cli`**, which exposes
  no `usage` — every curate record then carries no `brain_usage` at all. It is
  exercisable only on the claude brain.
