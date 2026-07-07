---
slug: phase-4-claude-adapter
status: awaiting-review
author: claude
reviewer: codex
branch: phase-4-claude-adapter
diff: git diff main...phase-4-claude-adapter
created: 2026-07-07
---

# Review: Phase 4 (core) — Claude adapter scribe + recall + hook

## Brief  _(Author — Claude)_

**Intent.** Implement the capture + inject halves of the Claude Code loop:
`scribe.py` (spec §4, SessionEnd), `recall.py` (spec §3, SessionStart), and the
real `neurobase hook claude ...` entry points. This is the *core* of build-plan
Phase 4; the `init --agent claude` config installer and the in-vivo
session-A→session-B demo are deliberately deferred (they need the user's real
Claude config + live sessions, which can't run in this autonomous session).

**Scope.** Branch `phase-4-claude-adapter` (on `main`@`687fc40`), `git diff
main...phase-4-claude-adapter`. Key files:
- `src/neurobase/adapters/claude/scribe.py` — transcript parse per the §11.1
  fixture, D13 redaction, opt-in, §8 bounds, empty-capture skip.
- `src/neurobase/adapters/claude/recall.py` — nodes → `additionalContext`,
  framing header, 6000-char cap, detached `curate --if-stale` spawn,
  fail-safe.
- `src/neurobase/cli/__init__.py` — the real `hook` command (stdin JSON,
  always exit 0), replacing the Phase-0 stub.
- `tests/test_{claude_scribe,claude_recall,cli_hook}.py` — 31 new tests.

**Focus areas.**
- **Fail-safe / exit 0 (spec §4/§5 MUST).** Every hook code path must exit 0 —
  bad stdin, missing transcript, a scribe/recall exception, unknown agent. The
  `hook` command wraps dispatch in `try/except Exception: pass`; recall's
  `emit` swallows any error to `None`; scribe returns `None` rather than
  raising on the no-project / empty cases. Is there *any* path that can exit
  non-zero or wedge teardown? (`test_hook_always_exits_zero_on_garbage`,
  `test_session_end_scribe_failure_exits_zero`, `test_hook_no_args_exits_zero`.)
- **§11.1 parser fidelity.** Skip `isSidechain`; a user turn containing a
  `tool_result` block is skipped whole; noise-prefixed turns dropped; `content`
  may be a plain string OR a list of `{type:text,text}` blocks (joined);
  assistant summary = last non-empty visible text (thinking/tool excluded).
  Correct against the fixture? (`test_parses_fixture_prompts_and_summary` and
  friends.)
- **Redaction timing.** D13 redaction runs over the assembled body *before*
  the raw is written (`test_redaction_applied_before_write`). No secret should
  reach `raw/`.
- **Opt-in + empty-capture.** Scribe writes only if the resolved project's
  memory tree exists, and only if there's ≥1 prompt or a summary. Right?
- **Recall cap semantics (spec §3).** Header + nodes joined by `\n\n---\n\n`,
  capped at 6000: drop *whole* trailing nodes rather than truncate mid-node;
  truncate only if a single node alone exceeds the cap.
  (`test_cap_drops_whole_trailing_nodes`,
  `test_cap_truncates_single_oversized_first_node`.)

**Known risks / tradeoffs.**
- The `hook` command still goes through Typer. D12 wants the `hook` path to
  avoid Typer's startup niceties (the fast path); S6/ADR-0003 measured ~40ms
  which is well inside budget, so I kept Typer for now and left the
  Typer-light fast path as a later optimization. Flag if you'd rather that
  land now.
- `spawn_curate_if_stale` uses `sys.argv[0]` as the executable — correct for
  the installed shim (`~/.local/bin/neurobase`). Detached, output to
  /dev/null, `start_new_session=True`, best-effort (OSError suppressed). It is
  monkeypatched off in the hook tests so they don't spawn real processes.
- The scribe's `captured_at` is `datetime.now(UTC)` (session-end time). Claude
  sessions are one-file-per-session (no per-turn overwrite — that's Codex/§5),
  so no session-keyed dedupe is needed here.
- The `hook` command's broad `except Exception: pass` deliberately swallows
  everything for the fail-safe guarantee. A genuine bug would be silent — but
  spec §4/§5 explicitly prioritizes "never wedge teardown" over surfacing
  errors, and the unit tests exercise the real paths directly (not through the
  swallow).

**How to verify.** `uv sync && uv run pytest && uv run ruff check . && uv run
ruff format --check . && uv run mypy src tests`. Live (through the installed
shim): `uv tool install . --force`; enable a scratch repo; pipe a SessionEnd
payload — `echo '{"transcript_path":"...","cwd":"...","reason":"clear"}' |
neurobase hook claude session-end --root <store>` → a raw appears (redacted);
`neurobase curate`; pipe a SessionStart payload — `echo '{"cwd":"..."}' |
neurobase hook claude session-start --root <store>` → the `additionalContext`
JSON is printed. This was run manually end-to-end (raw → curate → injected
context); it's not in the committed suite (it hits the installed shim +
resolves a real brain for curate).

**Out of scope.** `init --agent claude` (settings.json read/diff/write +
consent + backup, spec §7) and the live two-session "Done when" demo —
deferred, they need the user's real environment. `adapters/codex/` (Phase 5).
The Typer-light fast path for `hook` (optimization, not correctness).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** approve | changes-requested — _one-line rationale._
