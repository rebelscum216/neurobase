# Coverage report — full repo crawl (2026-07-18)

**Scope:** all 41 source modules (9,364 LOC), 551 tests across 42 test files,
CI/tooling, docs, and test architecture.
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

| Metric | Value |
|---|---|
| Tests collected | **551** (42 files, 0 failures, 0 xfail, 4 platform skips) |
| Line coverage | **91%** (423 / 4767 stmts missed) |
| Branch coverage | **89%** (239 partial branches) |
| Coverage enforced in CI | **No — not measured at all** |
| Documented open defects | 1 ([known-gaps.md](../known-gaps.md) G1) |

The suite is genuinely strong: 89% branch coverage with the misses concentrated
in defensive/error paths. The gaps below are ranked by what actually matters,
not by raw percentage.

## Lowest-covered modules (branch coverage)

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

## Gap 4 — No integration / end-to-end test [priority: medium]

Every one of the 42 test files is unit-level (no `conftest.py`, no shared
fixtures, no `tests/__init__.py`). There is **no test that drives the real loop**
— hook → capture (scribe) → curate → recall/inject → recommend — across a live
store. The MCP server is tested at the handler level (`tests/test_mcp_server.py`)
but **not over the stdio protocol** it actually ships on, and neither
`neurobase mcp serve` nor `cli serve` (`cli/__init__.py:1048–1050`) is exercised
end to end. Given the cross-agent Claude↔Codex design, a single happy-path
integration test would catch wiring regressions no unit test can see.

## Gap 5 — Windows lock path is exercised but never proven [priority: low]

`core/locks.py` is the lowest module at **66%** — the entire `msvcrt` Windows
branch (lines 16–27, 40–44) is dark on this run. CI *does* include
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
   set a `fail_under` (start at the current 89 to ratchet, never regress).
2. **Add `audit_command_redaction.py` to the gate** (or fold it into pytest) so
   the redaction property is checked on every push, not by hand.
3. **Add branch tests for the two real-logic clusters:** the codex `install.py`
   shell-word parser (207–262) and the `redact.py` `$(…)`/quote scrubbing
   branches.
4. **Add one happy-path integration test** driving capture→curate→recall against
   a temp store, plus a stdio-level MCP smoke test.
5. Leave G1 to its ADR; leave the defensive-branch misses alone — chasing them to
   100% is low value.

---

## Method / reproduction

```
uv run --with coverage --with pytest-timeout coverage run --branch -m pytest -q
uv run --with coverage coverage report --include='src/*' --show-missing
```

Measured against the working tree of `fix-curator-runaway-guard-lock` (base
`main` at `46b2763` + uncommitted work described in the header), macOS
(Darwin 25.5.0), Python 3.13. Numbers will shift slightly by platform (e.g.
`core/locks.py` rises on Windows, falls on POSIX). This report reflects a POSIX
run.
