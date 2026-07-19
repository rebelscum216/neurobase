---
slug: gap3-coverage
status: approved
author: claude
reviewer: codex
branch: close-gap3-coverage
diff: git diff aef895f...HEAD
created: 2026-07-19
---

# Review: close the Gap 3 coverage holes in metrics and the MCP server

## Brief  _(Author — Claude)_

**Intent.** Close the two remaining "real-logic (not just defensive) coverage
holes" that `docs/notes/2026-07-18-coverage-report.md` names under **Gap 3** and
that the coverage-gate branch deliberately left out of its own scope:
`recommender/metrics.py` (80%, the lowest module in the repo) and `mcp/server.py`'s
empty-fact guard and bad-slug skip.

**Tests only — no source change.** That is deliberate, preserving the separation
the coverage report set between test work and source fixes. Two things I found
that *would* be source changes are written up under "Known risks" below rather
than fixed here; both want your call, not mine.

**Scope.** Branch `close-gap3-coverage`, base `aef895f` (current `origin/main`),
`git diff aef895f...HEAD`. One commit, two files, +362 lines, all test code:

- `tests/test_metrics.py` — +280. Four survival-fallback tests, four
  `_recurrence_reduction` tests, and three helpers (`_rewrite_ledger_event`,
  `_seed_project`, `_seed_raw`).
- `tests/test_mcp_server.py` — +82. Two bad-slug-in-registry tests, three
  empty-fact tests (one parametrized over three whitespace shapes).

Measured effect:

| Module | Before | After | Lines closed |
|---|---|---|---|
| `recommender/metrics.py` | 80% | **99%** | 161, 182, 192, 201–202, 205, 234, 236–242, 245 |
| `mcp/server.py` | 86% | **90%** | 84–85, 171–172, 183 |
| combined (gate) | 90.35% | **90.85%** | 999 → 1014 passed |

**Focus areas.** In rough order of how much I want a second pair of eyes:

1. **Are the `_recurrence_reduction` tests testing the right thing?** I seed raw
   captures whose body is the proposal's *own rendered body*, read back via
   `_duplicate_capture_body`. That makes the Jaccard near-duplicate check
   trivially true (similarity 1.0), which is intentional — it isolates the
   before/after partition and the ratio arithmetic, which is the untested logic.
   But it does mean these tests do **not** exercise `is_near_duplicate`'s
   threshold behaviour at the margin. Tell me if you'd rather see a body that
   sits deliberately near the 0.6 boundary.
2. **`test_survival_not_survived_when_artifact_cannot_be_read`** reaches the
   `except OSError` branch by making `installed_path` a *directory*, so
   `read_bytes()` raises. That's `IsADirectoryError` on POSIX and
   `PermissionError` on Windows — both `OSError` subclasses, so the test should
   hold on every matrix cell, but it is the one new test whose mechanism is
   platform-dependent and I have only run it on macOS.
3. **The ledger-rewriting helper.** `_rewrite_ledger_event` edits an already-written
   ledger to produce shapes this codebase no longer emits (an unparseable `at`, a
   legacy `accepted` event with no `installed_hash`). I think reproducing real
   historical on-disk states is more honest than mocking `_parse_iso`, but it does
   couple the tests to the ledger's JSONL shape.

**Known risks / tradeoffs.**

- **`metrics.py:185` is unreachable and I left it that way.** It is the only line
  in the module still uncovered, and it cannot be covered by any input:
  `_latest_accepted_event` builds its candidate list from events where
  `(when := _parse_iso(event.get("at"))) is not None`, so every event it returns
  has a parseable `at`; `_survival_one:183` then re-parses that same value with the
  same pure function, so `accepted_at is None` at :184 is never true. Removing it
  cleanly means having `_latest_accepted_event` return the parsed datetime
  alongside the event, which would also drop the identical redundant re-parse at
  `:232`. **That is a source change, so it is not in this branch** — flagging it for
  your call on whether it lands as a follow-up or gets a `# pragma: no cover`.
- **`_recurrence_reduction` ignores the injected config.** At `:227` it calls
  `corpus.load_corpus(root, now=now)` **without** forwarding `cfg`, so
  `load_corpus` falls back to `load_config().recommend` — an injected
  `RecommendConfig` (e.g. a widened `raw_lookback_days`) never reaches the raw
  lookback there, even though `compute_metrics` accepted one. My tests work
  *around* this by keeping every seeded capture inside the default 30-day window;
  the comment above them says so. I believe this is a real inconsistency with
  `compute_metrics`'s documented "injectable for deterministic tests" contract,
  but it only affects an explicitly advisory/best-effort metric, so I did not
  treat it as a blocker. Worth your judgement.
- **`fail_under` left at 90, not ratcheted.** Combined coverage rose 90.35 →
  90.85, and the `pyproject.toml` comment does say to raise the floor when
  coverage rises. I deliberately did not, because the floor has to hold on the
  *lowest* matrix cell and Windows measured 0.09 below macOS on the last run
  (90.26 vs 90.35). Ratcheting on a macOS-only number is exactly the mistake that
  comment warns about. Once CI reports this branch's real Windows figure, ~90.5
  looks safe.
- **Not a full audit of either module.** `mcp/server.py` still has uncovered lines
  69, 87, 92–95, 159–160, 214–215, 217, 239–240. Those are the defensive
  exception handlers Gap 3 explicitly classes as "acceptable", so they are out of
  scope here.

**How to verify.**

```bash
git diff aef895f...HEAD
uv run python scripts/ci.py          # full gate: 1014 passed, 1 skipped, 90.85%

# the two modules in isolation, with the before/after numbers above:
uv run pytest tests/ -q --cov=neurobase.recommender.metrics \
    --cov=neurobase.mcp.server --cov-report=term-missing --cov-branch
```

The `metrics.py` line should read `100 1 38 1 99% 185` — that single remaining
line is the unreachable one discussed above, not an oversight.

**Out of scope.**

- **G1 / the D11 store-schema guard.** Still deferred to its own ADR.
- The other defensive handlers in `mcp/server.py` listed above.
- Any source change to `metrics.py`, including the two findings I raise here.
- The `fail_under` ratchet, which wants a real Windows measurement first.
- `docs/how-it-works.md`'s `scripts/ci.py` argv listing, which you flagged as
  "too easy to stale" in the coverage-gate review — still open, still beyond
  this branch's remit.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

No findings.

Verified the requested diff range (`git diff aef895f...HEAD`) in the clean
`/tmp/nb-gap3` worktree. The branch is test-only: `tests/test_metrics.py` and
`tests/test_mcp_server.py` are the only non-baton files changed. The new metrics
tests exercise the intended survival fallback branches and the
`_recurrence_reduction` before/after arithmetic without depending on mocked
internals; the recurrence fixtures intentionally keep raw captures inside the
default 30-day lookback, which matches the brief's explicit scope. The MCP tests
cover the bad-registry-slug skip paths and the `memory_remember` empty-fact hard
error, including the ordering of the empty check before project resolution and
the no-write guarantee.

I also checked the two source observations in the brief. `metrics.py:185` is
indeed unreachable with the current `_latest_accepted_event` shape, and
`_recurrence_reduction` still does not receive an injected `RecommendConfig`.
Those are real follow-up candidates, but they are pre-existing source issues and
not regressions in this tests-only delta; the normal CLI path calls
`compute_metrics(root)` without an injected config, so the config inconsistency is
limited to explicit injection/test-style callers.

Verification run:
`uv run pytest tests/test_metrics.py tests/test_mcp_server.py -q` passed;
`uv run pytest tests/ -q --cov=neurobase.recommender.metrics
--cov=neurobase.mcp.server --cov-report=term-missing --cov-branch` reproduced
`metrics.py` at `99%` with only line `185` missing and `mcp/server.py` at `90%`;
`uv run python scripts/ci.py` passed with ruff, format check, mypy, and
`1014 passed, 1 skipped`, combined coverage `90.85%`.

**Verdict:** approve — the Gap 3 coverage tests constrain the intended behavior
and I did not find a blocking correctness, spec, security, or coverage issue in
this diff.
