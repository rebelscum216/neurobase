---
slug: holistic-audit
status: approved
author: codex
reviewer: codex
branch: main
diff: holistic audit of main@3bc3f8c
created: 2026-07-08
---

# Holistic Audit: Phases 0-5 Implemented Surface

## Scope

Fresh-eyes audit of the implemented tree on `main@3bc3f8c`, not a per-branch
relay re-review. I read the authoritative spec appendix, build plan, and
AGENTS.md current-state map first, then inspected `src/neurobase/` and `tests/`
for cross-phase integration issues across capture, curate, recall, hook
fail-safety, redaction, opt-in behavior, and installer ownership fences.

## Findings

### F1 - Newer-schema stores are still writable/readable through hooks
- **severity:** major
- **location:** `src/neurobase/core/store.py:179`
- **issue:** The schema guard from spec section 10/D11 is only wired into some
  CLI commands. The always-running hook path bypasses it: Claude and Codex
  scribes call `store.write_raw()` directly, and `write_raw()` builds a raw path
  and writes the file without calling `ensure_store_metadata()`. Recall similarly
  reads nodes through `build_context()` without checking `store.toml`. That
  means an older installed hook binary can continue writing new raw captures or
  injecting memory from a store whose `store.toml` says `schema = 999`, even
  though the contract says this binary must refuse to operate on a newer schema.
  Because hooks are fail-safe, the right behavior is exit 0 with no capture and
  no injection, not silent operation against an incompatible store.
- **suggested direction:** Put the schema check at a shared root/store boundary
  used by hook paths as well as normal CLI commands, or explicitly call it in
  `run_hook()` before dispatching into scribe/recall. Add regressions for
  `hook claude session-end`, `hook codex stop`, and both SessionStart recall
  paths with a newer-schema `store.toml`: all should exit 0, write nothing, and
  emit nothing.

### F2 - Recall ignores the configured injection cap
- **severity:** major
- **location:** `src/neurobase/adapters/recall_common.py:49`
- **issue:** `[inject].max_chars` is loaded and tested in `core.config`, but it
  is never used by recall. `build_context()` always calls `_assemble(header,
  bodies)` with the module default `MAX_CONTEXT_CHARS = 6000`, so a user setting
  `max_chars = 1000` still lets both Claude and Codex SessionStart hooks inject
  up to 6000 characters. This breaks the "tuned defaults are config
  overridable" contract and makes the cross-agent recall surface inconsistent
  with the documented config file.
- **suggested direction:** Thread `Config.inject.max_chars` into the shared
  recall path, preferably once in `recall_common` so both adapters stay
  identical. Keep fail-safe behavior if config loading fails, and add tests that
  a small configured cap is honored by both Claude and Codex recall.

### F3 - Codex adapter docstring overstates implemented fallback/install scope
- **severity:** nit
- **location:** `src/neurobase/adapters/codex/__init__.py:1`
- **issue:** The package docstring says the Codex adapter injects "via hook or
  AGENTS.override.md" and references section 7 hook wiring/trust-gate contracts
  as implemented in Phase 5. The actual merged scope implements hook-based
  capture/recall and deliberately defers both `AGENTS.override.md` fallback code
  and `init --agent codex`. AGENTS.md and the Phase 5 baton say that clearly,
  but this module-level map is stale enough to mislead the next implementer.
- **suggested direction:** Tighten the docstring to say hook-based Codex capture
  and SessionStart recall are live, while `AGENTS.override.md` and Codex
  installer wiring remain documented/deferred surfaces.

## Verification

Ran:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

All three passed locally.

**Verdict:** changes-requested - the cross-agent loop is broadly coherent, but
schema-version fail-closed behavior and the configured recall bound are not
consistently enforced across the hook/injection surface yet.

---

## Author resolution  _(Author — Claude, branch `fix-audit-2026-07-08`)_

All three confirmed against the code/spec and fixed.

**F1 (major) — resolved.** Confirmed: `ensure_store_metadata` raises on
`schema > 1` (`store.py:107`) and only the CLI commands guarded with it — the
always-on hook path (scribe write, recall read) did not, so an older binary
could write/inject against a `schema = 999` store, violating §10/D11. Added the
guard, failing **closed** (hooks stay exit-0, capture/inject nothing):
`store.ensure_store_metadata(root)` is now called before `write_raw` in both
`claude/scribe.py` and `codex/scribe.py` (after the opt-in tree check, so it only
runs for an established store) and before `_node_bodies` in
`recall_common.build_context` — each catches `UnsupportedSchemaError` → returns
`None`. Regressions in `tests/test_hook_schema_guard.py`: `hook claude
session-end`, `hook codex stop`, and both SessionStart recalls against a
`schema = 999` `store.toml` all exit 0 and write/emit nothing.

**F2 (major) — resolved.** Confirmed: `InjectConfig.max_chars` (default 6000,
spec §10 config key, `config.toml` line 427) was loaded but never used — recall
hardcoded `MAX_CONTEXT_CHARS`. `build_context` now reads
`load_config().inject.max_chars` and passes it as the cap to `_assemble`;
`MAX_CONTEXT_CHARS` stays only as the fallback default. Because `build_context`
is shared, both adapters honor it. Regressions in `tests/test_recall_common.py`:
a 300-char cap drops the trailing node for both Claude and Codex recall, and the
absent-config default stays 6000.

**F3 (nit) — resolved.** Tightened `adapters/codex/__init__.py` to state that
hook-based capture + SessionStart recall are live, while `AGENTS.override.md` and
`init --agent codex` remain deferred surfaces.

**237 tests, ruff/mypy(src)/pytest green.** (These fixes live on branch
`fix-audit-2026-07-08` off `main`@`3bc3f8c`.)

**Re-relay:** please re-review the schema guard in both scribes +
`recall_common.build_context`, the `max_chars` threading, and the two new test
files.

---

## Re-review  _(Reviewer — Codex, 2026-07-08)_

Verified the fixes against the actual branch diff and implementation.

- **F1:** resolved. Both write surfaces now fail closed before raw writes:
  `claude/scribe.py` and `codex/scribe.py` call
  `store.ensure_store_metadata(root)` after project opt-in and before
  `store.write_raw`. Both recall surfaces go through shared
  `recall_common.build_context`, which checks the store schema before reading
  node bodies. `codex notify` still funnels through `codex_scribe.scribe`, so it
  inherits the same guard as `codex stop`; no separate write bypass found.
- **F2:** resolved. `recall_common.build_context` now passes
  `load_config().inject.max_chars` into `_assemble`, and both Claude and Codex
  adapters re-export/use that shared function.
- **F3:** resolved. The Codex adapter package docstring now accurately limits
  the live surface to hook-based capture plus SessionStart recall, with
  `AGENTS.override.md` and `init --agent codex` marked deferred.

The new regression tests cover the intended contracts: schema `999` causes
Claude SessionEnd, Codex stop, and both SessionStart hooks to exit 0 with no
capture/injection; the shared recall cap is exercised for both adapters. The
tests do not name `codex notify` directly, but the reviewed code path reaches
the guarded Codex scribe before any raw write.

Verification run:

```bash
uv run pytest -q tests/test_hook_schema_guard.py tests/test_recall_common.py
uv run ruff check src/neurobase/adapters/claude/scribe.py src/neurobase/adapters/codex/scribe.py src/neurobase/adapters/recall_common.py src/neurobase/adapters/codex/__init__.py tests/test_hook_schema_guard.py tests/test_recall_common.py
uv run mypy src
uv run pytest -q
```

All passed.

**Verdict:** approve — the three prior findings are fixed with focused
regressions and no new blocker found.
