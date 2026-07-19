# Coverage report — full repo crawl (2026-07-18)

**Scope:** all 44 source `.py` files / 43 measured by coverage (9,509 physical
lines, `wc -l`), 847 collected test items across 42 test files, CI/tooling, docs,
and test architecture.
**Measured:** `coverage run --branch -m pytest` via
`uv run --with coverage --with pytest-timeout` (macOS, Python 3.13). No coverage
tooling is installed in the repo — this was an out-of-band measurement (see
Gap 1). Measured against the **working tree of branch
`fix-curator-runaway-guard-lock`**, which carries uncommitted new modules
(`core/locks.py`, `core/process_guard.py`, `tests/test_locks.py`) and local edits
to `brain/`, `cli/`, and `curator/distill.py` — so `core/locks.py` and
`core/process_guard.py` below do **not** yet exist on `main`. Re-running on a
clean `main` will show a slightly different module set.

## Headline numbers

Three distinct metrics — kept separate deliberately, because conflating them is
exactly the error an earlier draft of this report made:

| Metric | Value |
|---|---|
| Tests collected | **847** items (551 `def test_` functions; the rest are parametrized cases), 42 files, 0 failures, 0 xfail, 4 platform skips |
| **Statement** coverage | **91%** (4344 / 4767 statements; 423 missed) |
| **Branch** coverage | **84%** (1347 / 1610 branches taken; 263 branches missed, 239 partial) |
| coverage.py combined `Cover` (branch mode) | **89%** — the number `coverage report` prints; = (covered stmts + covered branches) / (stmts + branches). **Not** branch coverage. |
| Coverage enforced in CI | **No — not measured at all** |
| Documented open defects | 1 ([known-gaps.md](../known-gaps.md) G1) |

The suite is genuinely strong: 91% of statements and 84% of branches exercised,
with the misses concentrated in defensive/error paths. The gaps below are ranked
by what actually matters, not by raw percentage.

## Lowest-covered modules

Ranked by coverage.py's combined `Cover` (branch mode) — the per-file analogue of
the 89% total above, not pure branch coverage.

| Module | Cover | Stmts miss / branch miss |
|---|---|---|
| `core/locks.py` | 66% | 15 / 2 |
| `recommender/metrics.py` | 80% | 15 / 8 |
| `adapters/codex/install.py` | 82% | 44 / 23 |
| `brain/anthropic_api.py` | 84% | 7 / 1 |
| `cli/diagnostics.py` | 84% | 29 / 21 |
| `recommender/emitters.py` | 84% | 11 / 7 |
| `recommender/ranker.py` | 84% | 19 / 8 |
| `mcp/server.py` | 85% | 19 / 4 |
| `adapters/codex/scribe.py` | 86% | 22 / 12 |
| `cli/__init__.py` | 88% | 69 / 42 |
| `curator/distill.py` | 88% | 18 / 13 |

Everything else sits at 91–100%.

---

## Gap 1 — Coverage is never measured or enforced (the meta-gap) [priority: high]

`scripts/ci.py` — the single source of truth for "green" — runs
`ruff · format · mypy · pytest` and **nothing else**. There is:

- No `coverage`/`pytest-cov` in the `dev` dependency group (`pyproject.toml`)
- No `--cov` / `fail_under` / branch config anywhere (grep across pyproject, CI,
  Makefile, pre-commit returned empty)
- A stale root `.coverage` file (Jul 16) with no tooling installed in `.venv`
  that could have produced it

**Consequence:** the 89% is real *today* but completely unguarded. Any PR can
delete a test or add an untested branch and the gate stays green. For a project
whose crown jewel is a **security boundary** (redaction), that is the gap to
close first.

## Gap 2 — The redaction property check runs only by hand [priority: high]

`scripts/audit_command_redaction.py` is described in `tests/test_redact.py:316`
as *"the one real property check"* for the lexical redaction boundary — the exact
boundary the project history flags as having *failed open and leaked* over a
12-round relay. It is **never invoked by CI or pytest** (only referenced in
comments). The security-critical invariant is verified only when a human
remembers to run the script.

## Gap 3 — Real-logic (not just defensive) coverage holes [priority: medium]

Most misses are exception handlers and `continue`/`break` guards — acceptable.
These are the exceptions where **actual logic** is untested:

| Module | Cover | Untested real logic |
|---|---|---|
| `adapters/codex/install.py` | 82% | Shell-word / dotted-key parser at lines **207–262**: `\u`/`\U` unicode-escape decoding, single-quote parsing, unterminated-string → `None`. This is the surgical TOML editor — parser correctness, not error plumbing. |
| `core/redact.py` | 92% | Scrubbing branches inside the security boundary: `$(…)` command-substitution scrub (**489–493, 564–567**), quote handling (**549–562**), backslash passthrough (**425–428**), `fail_closed` on malformed input (**328–329**). |
| `mcp/server.py` | 85% | Empty-fact guard (`raise ValueError("fact must not be empty")`, **183**), bad-slug skip in scans (**84–85**), the `serve()` stdio entrypoint (**247**). |
| `recommender/metrics.py` | 80% | Lowest recommender module — reduction/aggregation branches (161, 182–245) partly unexercised. |

## Gap 4 — Integration coverage stops short of the shipped edges [priority: medium]

Integration coverage exists and is real — this is *not* an all-unit suite:
[`tests/test_cross_agent.py`](../../tests/test_cross_agent.py) drives the real
Claude *and* Codex scribes → `engine.curate` → CLI `hook … session-start` recall
across a temp store (both `test_claude_plus_codex_fold_and_both_sessions_recall`
and `test_codex_raw_alone_curates`), and `tests/test_cli_curate.py` exercises
curate at the CLI level. So the capture → curate → recall spine *is* covered.

What that spine does **not** reach is the two shipped edges:

1. **MCP over stdio.** `tests/test_mcp_server.py` tests the handlers in-process; no
   test drives the server over the **stdio protocol** it actually ships on, and
   `neurobase mcp serve` / `cli serve` (`cli/__init__.py:1048–1050`, `server.run`
   at `mcp/server.py:247`) is never launched end to end.
2. **The recommender in the loop.** `test_cross_agent.py` stops at recall; no
   happy-path test carries a curated store through to a `recommend`/emit step, so
   the curate → mine → rank → emit wiring is only covered piecewise by the
   `recommender/*` unit tests, never as one flow.

Both use a `FakeBrain` and trigger curation directly (the auto-spawn is
monkeypatched off), so brain-invocation and the stale-spawn trigger are also
outside the integration path. Two targeted tests — a stdio MCP smoke test and a
curate-through-recommend happy path — would close the real edges without
re-testing what `test_cross_agent.py` already proves.

## Gap 5 — Windows lock path is exercised but never proven [priority: low]

`core/locks.py` is the lowest module at **66%** combined `Cover` — the entire
`msvcrt` Windows branch (lines 16–27, 40–44) is dark on this run. CI *does* include
`windows-latest`, so the code runs there — but because coverage isn't collected
on any runner (Gap 1), nothing asserts the Windows branch actually executes. It
is covered by faith, not measurement.

## Gap 6 — Known, already-documented defect (not re-derived) [priority: tracked]

`known-gaps.md` **G1** (status: open, severity: major): the D11 store-schema
guard is enforced per-command by hand, not at the store boundary — `mcp serve`
never guards, `init` mutates `registry.toml` before guarding,
`status --recommender` returns before the guard. This is a correctness gap the
code's own tests can't catch because the guard simply isn't called on those
paths. It needs an ADR, not a patch. Flagged here so it is in the coverage
picture; the doc is authoritative.

---

## What's healthy (where *not* to spend effort)

- Core store, backups, config, projects, linkify, brain layer, scribe adapters,
  distill, curator engine, corpus, miner, seed, proposals all sit at 91–100%
  with misses that are genuinely just `except OSError` / malformed-input guards.
- Cross-platform hygiene is deliberate: the 4 skips are correct `skipif(win32)`
  on POSIX permission-bit and symlink tests.
- The `pytest-timeout` fail-loud guard and 60s cap are in place.

---

## Recommendations (prioritized)

1. **Wire coverage into the gate.** Add `coverage`/`pytest-cov` to the `dev`
   group, run `pytest --cov=src/neurobase --cov-branch` in `scripts/ci.py`, and
   set `fail_under`. Note what the number gates: coverage.py's `fail_under`
   enforces the **combined** `Cover` (89 today), not branch coverage — so seed it
   at 89 to ratchet the combined figure, or gate statement coverage explicitly at
   91 if that's the intent. Pick one and state it; don't leave the metric implicit.
2. **Add `audit_command_redaction.py` to the gate** (or fold it into pytest) so
   the redaction property is checked on every push, not by hand.
3. **Add branch tests for the two real-logic clusters:** the codex `install.py`
   shell-word parser (207–262) and the `redact.py` `$(…)`/quote scrubbing
   branches.
4. **Close the two integration edges from Gap 4:** a stdio-level MCP smoke test
   and a curate→recommend happy path (capture→curate→recall is already covered by
   `test_cross_agent.py`).
5. Leave G1 to its ADR; leave the defensive-branch misses alone — chasing them to
   100% is low value.

---

## Method / reproduction

```
uv run --with coverage --with pytest-timeout coverage run --branch -m pytest -q
uv run --with coverage coverage report --include='src/*' --show-missing   # prints combined Cover
uv run --with coverage coverage json  --include='src/*' -o - | python -c \
  'import json,sys; t=json.load(sys.stdin)["totals"]; \
   print("statement", round(100*t["covered_lines"]/t["num_statements"],1)); \
   print("branch", round(100*t["covered_branches"]/t["num_branches"],1)); \
   print("combined", round(t["percent_covered"],1))'
```

`coverage report`'s `Cover` column is the combined metric (89%). Statement (91%)
and branch (84%) coverage come from the JSON totals, as above — that separation
is the whole point of the headline table. Test-item count is
`uv run pytest --collect-only -q` (847); `grep -c 'def test_'` gives the 551
function count.

Measured against the working tree of `fix-curator-runaway-guard-lock` (base
`main` at `46b2763` + uncommitted work described in the header), macOS
(Darwin 25.5.0), Python 3.13. Numbers will shift slightly by platform (e.g.
`core/locks.py` rises on Windows, falls on POSIX). This report reflects a POSIX
run.
