---
slug: phase-5-codex-adapter
status: awaiting-review
author: claude
reviewer: codex
branch: phase-5-codex-adapter
diff: git diff main...phase-5-codex-adapter
created: 2026-07-08
---

# Review: Phase 5 (core) — Codex adapter → cross-agent MVP (spec §5)

## Brief  _(Author — Claude)_

**Intent.** The headline milestone: a fact captured by one agent surfaces in the
other. This branch implements the Codex-side capture (spec §5 rollout scribe) and
injection (mirrors §3 per ADR-0005), plus the `neurobase hook codex …` wiring, so
`curate` folds Claude + Codex raws into one fact set that **both** next-sessions
recall. The `init --agent codex` installer (hooks.json + config.toml trust keys,
spec §7) is deliberately deferred to a Phase 5-init follow-up — same split we used
for Phase 4 (core vs. init) — since it writes the user's real `~/.codex/` config
and the live cross-agent demo needs the user anyway.

**Scope.** Branch `phase-5-codex-adapter` (on `main`@`475d5f9`), `git diff
main...phase-5-codex-adapter`. Key files:
- `src/neurobase/adapters/codex/scribe.py` — the rollout scribe: `parse_rollout`,
  `_split_ide_wrapper`, `_assemble_body`, `discover_rollout`, `scribe`.
- `src/neurobase/adapters/recall_common.py` — injection core (nodes →
  `additionalContext`), **extracted** from `adapters/claude/recall.py`.
- `src/neurobase/adapters/claude/recall.py` — now a thin re-export of the shared
  core (public surface unchanged; Phase-4 tests still green).
- `src/neurobase/adapters/codex/recall.py` — thin re-export (mirrors §3).
- `src/neurobase/cli/__init__.py` — `hook codex session-start|stop|notify`
  dispatch, `_argv_json_payload` (notify's argv JSON), `--rollout` test flag.
- `tests/test_codex_scribe.py` (13), `tests/test_cli_hook_codex.py` (6),
  `tests/test_cross_agent.py` (2).

**Focus areas.** Where I most want your eyes:
- **Per-turn overwrite (the key §5 trick).** Codex has no SessionEnd — the hook
  fires per turn — so `scribe` passes `captured_at = session-start timestamp`
  (`session_meta.timestamp`), which the store's `raw_filename` derives the path
  from, so every turn's atomic write overwrites the same file (one raw/session,
  last-turn-wins). Verify: does `test_per_turn_overwrite_one_raw_last_turn_wins`
  actually prove one file, latest content? Is the `RawConsumedError` fallback
  (write a fresh `captured_at=now` file if the raw was folded mid-session) the
  right call per the §1 mutability rule, or should a consumed raw be left alone?
- **IDE-wrapper split (spec §5).** `_split_ide_wrapper` partitions on
  `## My request for Codex:` — request becomes the prompt, the preceding block
  (minus the `# Context from my IDE setup:` header) is kept once as the latest
  `## Files in focus (IDE)` section, capped at 800. Does the partition mishandle
  any real shape (marker absent, marker present but empty request, context
  header absent)?
- **`discover_rollout` (spec §5 / §11.4).** For the `notify` path (no rollout
  path in payload — confirmed never present): newest `rollout-*.jsonl` by mtime
  ≥ `min_mtime`, session-id cross-checked against `session_meta`. Is the
  fallback-to-newest-when-no-session-match acceptable, or should a non-match
  return `None` (risk: capturing the wrong session vs. capturing nothing)?
- **Fail-safe / exit 0 (spec §5 MUST).** Every `hook codex …` path must exit 0
  and never wedge a turn — dispatch is wrapped in `run_hook`'s
  `try/except Exception: pass`; scribe returns `None` on no-project/empty; the
  notify argv-JSON parse is defensive. Any path that can raise out or exit
  non-zero? (`test_codex_stop_garbage_exits_zero`,
  `test_codex_notify_no_rollout_exits_zero`.)
- **The recall extraction.** I moved the inject logic to `recall_common.py` and
  made both adapters re-export it (rather than have Codex import from the Claude
  adapter). Claude's `recall` public names are preserved so Phase-4 tests pass
  untouched. Is the re-export surface right, or would you structure the shared
  module differently?

**Known risks / tradeoffs.**
- `_assemble_body` for Codex duplicates the §8 bound constants
  (`MAX_PROMPTS`/`MAX_PROMPT_CHARS`/`MAX_SUMMARY_CHARS`) that also live in
  `claude/scribe.py`, rather than importing them cross-adapter. Deliberate
  (adapter decoupling), but flag if you'd rather they share one home.
- `notify` delivers its JSON as argv, not stdin; I extract it by scanning
  `run_hook`'s args for the first `{`-prefixed token (`_argv_json_payload`)
  because `_parse_hook_args`'s `(agent, event, opts)` tuple is locked by an
  existing test. Acceptable, or too clever?
- `_parse_started_at` falls back to `datetime.now(UTC)` on an unparseable
  timestamp — capture still works but loses per-turn dedupe for that session
  (each turn writes a new file). Right failure mode?

**How to verify.**
```
git switch phase-5-codex-adapter
uv run ruff check . && uv run mypy src && uv run pytest -q   # 228 passing
uv run pytest tests/test_cross_agent.py -q                   # the MVP "Done when"
```
Live-verified separately: `neurobase hook codex stop` through the installed
shim wrote `…Z_codex_<sid>.md` with `agent: codex` + the prompt (exit 0).

**Out of scope.** `init --agent codex` (Phase 5-init). The AGENTS.override.md
fallback (documented-only per §5/ADR-0005, not implemented as a code path). The
live cross-agent session demo (needs the user's real Codex install).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(pending)_

**Verdict:** _(pending)_
