---
slug: ci-local-gate-guardrails
status: awaiting-review
author: claude
reviewer: codex
branch: ci-local-gate-guardrails
diff: git diff main...HEAD
created: 2026-07-09
---

# Review: single-source-of-truth local CI gate + guardrails against partial local runs

## Brief  _(Author — Claude)_

**Intent.** Stop a PR from going red because only part of the CI gate was run
locally before pushing. The four CI checks (`ruff check .`, `ruff format
--check .`, `mypy src tests`, `pytest`) are collapsed into one shared entry
point that both local dev and every CI matrix job invoke, so the two can't
drift. Adds a `make ci` shortcut, an opt-in committed pre-push hook, and docs.
The gate's first CI run also surfaced that `main` was already red on Windows
(pre-existing, test-only); this branch cherry-picks the two existing fixes so
all six matrix jobs go green.

**Scope.** Branch `ci-local-gate-guardrails`, `git diff main...HEAD`. Key files:
- `scripts/ci.py` — **new.** The single source of truth: a `CHECKS` list of
  `(label, argv)` run via `subprocess.run`, each prefixed `uv run` to match CI
  byte-for-byte. Runs all four even past a failure, prints a PASS/FAIL summary,
  exits non-zero if any fail; exits 127 if `uv` isn't on PATH.
- `.github/workflows/ci.yml` — four separate steps replaced by one
  `uv run python scripts/ci.py`. Matrix (3 OS × py3.11/3.13) unchanged.
- `Makefile` — **new.** `make ci` → the script; plus `sync`/`fmt` shortcuts.
- `.githooks/pre-push` — **new.** POSIX-sh guard that runs the gate and blocks a
  red push; opt-in via `git config core.hooksPath .githooks`. Committed `100755`.
- `README.md`, `AGENTS.md` — document the one-command gate, the hook opt-in, and
  watching `gh run` to green after pushing; AGENTS repo-map updated.
- `tests/test_cli_{doctor,init,uninstall}.py`, `tests/test_store.py` — the two
  **test-only** cherry-picked Windows fixes (commits `ad15039` + `e454c69`):
  isolate `USERPROFILE` (Windows `Path.home()` reads it, not `HOME`), read the
  index with `encoding="utf-8"` (cp1252 mangled the em-dash), and escape
  backslashes so a Windows hooks path is a valid TOML basic-string key.

**Focus areas.**
- **Drift-proofing:** is `scripts/ci.py` genuinely the *only* definition of the
  gate now? Any place local and CI could still diverge (e.g. the nested
  `uv run`, argv quoting, working-dir assumptions on Windows)?
- **Gate behavior:** run-all-then-summarize vs. CI's per-step fail-fast — the
  pass/fail *result* is identical, but is continuing past a failure the right
  call? Exit codes (0 / 1 / 127) correct and useful?
- **Pre-push hook:** correct under `set -eu` with `if ! ...` (the gate failing
  must abort the push, not the hook); portable to Git-for-Windows `sh`; the
  `--no-verify` bypass documented.
- **Cherry-picks:** are those four test files exactly the upstream commits, and
  are they legitimately test-only / pre-existing (not smuggling in behavior)?

**Known risks / tradeoffs.**
- Nested `uv run` (workflow calls `uv run python scripts/ci.py`, which shells out
  to `uv run ruff …`) — chosen so the commands match CI exactly and work without
  an active venv; small per-check overhead.
- `scripts/` is outside mypy's `src tests` scope, so `ci.py` is lint/format-
  checked by ruff but not type-checked. Deliberately left matching CI's existing
  scope rather than widening it.
- The gate continues past failures (unlike GitHub's default step fail-fast) to
  show every problem in one pass. Same green/red outcome; more output on failure.

**How to verify.**
- `uv run python scripts/ci.py` (or `make ci`) → all four PASS, exit 0.
- Break formatting deliberately → gate reports `[FAIL] ruff format --check` and
  exits 1; individual checks still all run.
- `sh -n .githooks/pre-push` parses; `git ls-files --stage .githooks/pre-push`
  shows mode `100755`.
- Workflow parses (`yaml.safe_load`) with matrix intact and the single gate step.
- CI on PR #2: all six matrix jobs green (run `29019939834`, conclusion success)
  — including both Windows jobs, which were red on `main` before the cherry-picks.

**Out of scope.**
- The `actions/checkout@v4` / `setup-uv@v5` Node-20 deprecation warning (a
  separate maintenance bump).
- Phase 7 MCP work (this branch is off `main`, deliberately independent).
- Broadening mypy scope to `scripts/` or changing the ruff rule set.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(none yet)_

**Verdict:** _pending._
