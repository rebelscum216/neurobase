---
slug: scribe-handles
status: approved
author: claude
reviewer: codex
branch: adr-0015-scribe-handles
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: convert both scribes to the store handle — ADR-0015 step 3.2

## Brief  _(Author — Claude)_

**Intent.** Continue ADR-0015 step 3: convert the two capture scribes
(`adapters/claude/scribe.py`, `adapters/codex/scribe.py`) onto the `StoreHandle`,
dropping each scribe's own `store.ensure_store_metadata` guard call. Same pattern as
the recall_common conversion (step 3.1) — but these are **WRITE** paths with an
opt-in gate, which forces a specific two-handle shape (below).

**The two-handle shape (the one thing to scrutinize).** Each scribe currently runs,
in order: (1) `resolve_project` → return `None` if untracked; (2) `memory_dir(...).exists()`
→ return `None` if the tree is absent (opt-out); (3) the `ensure_store_metadata`
guard; (4) `write_raw`. Steps 1–2 run **before** the guard and **never create
`store.toml`**. A single `WRITE` handle opened up front would break that — `open_store(WRITE)`
creates `store.toml` eagerly on first use — so an untracked or opted-out capture
would start creating store metadata it never created before. To preserve behavior
exactly, the scribe now:
- opens a **READ** handle first (validates schema — newer-schema store raises →
  fail closed; never writes) and uses it for `resolve_project` + `memory_dir`;
- opens a **WRITE** handle only once it has committed to writing, and calls
  `write_raw` on it.

This keeps every prior outcome, including the **partial-store** case (tree present
but `store.toml` somehow absent → the `WRITE` open creates it, exactly as the old
`ensure_store_metadata` did) and Codex's `RawConsumedError` retry (the same `writer`
handle is reused for the second `write_raw`).

**Scope.** Branch `adr-0015-scribe-handles`, `git diff main...HEAD` (single commit
`a1cd32c`). Key files:
- `src/neurobase/adapters/claude/scribe.py` — `scribe()` two-handle conversion; drop
  the `projects` import (now `handle.resolve_project`).
- `src/neurobase/adapters/codex/scribe.py` — same, plus the `RawConsumedError` retry
  reuses the single `writer` handle.
- `tests/test_claude_scribe.py`, `tests/test_codex_scribe.py` — each gains two
  regression tests: an untracked capture and a registered-but-opted-out capture both
  return `None` **and create no `store.toml`**.

**Focus areas.**
1. **Behavior preservation of the guard + opt-in ordering** (the two-handle shape).
   Confirm untracked → `None` (no write, no `store.toml`); opted-out → `None` (no
   `store.toml`); newer-schema → fails closed; normal capture → writes as before.
2. **The schema check now runs first** (at the READ open), before `resolve_project`/
   opt-in, vs. the old order (guard after opt-in). As with step 3.1 I believe every
   case still returns the same value; the schema-validation reorder only changes
   *which* early-return fires, never the result.
3. **Codex retry correctness.** The `RawConsumedError` path reuses `writer` and
   passes `captured_at=datetime.now(UTC)` (fresh filename) — unchanged semantics, now
   via the handle. Confirm the per-turn overwrite / last-turn-wins behavior is intact.
4. **`store.read_doc`/redaction/parse code untouched.** Only the store/registry
   *access* changed; parsing, redaction (D13), bounds, and the §4/§5 body assembly are
   byte-for-byte the same.

**Known risks / tradeoffs.**
- **Two `open_store` calls per successful capture** (one READ inspect, one WRITE
  commit) — a second `store.toml` read. Negligible (capture is once per session-end /
  per turn), and it buys exact behavior preservation + semantically-correct handle
  modes (inspect=READ, mutate=WRITE) that a future per-method mode check won't fight.
  I considered writing through the READ handle to save an open, but rejected it — a
  READ handle that writes is muddy and would break under the deferred mode
  enforcement.

**How to verify.**
- `git diff main...HEAD`
- `uv run pytest tests/test_claude_scribe.py tests/test_codex_scribe.py tests/test_hook_schema_guard.py tests/test_cross_agent.py -q` (all pass)
- `uv run python scripts/ci.py` — full gate green (ruff/format/mypy/pytest + coverage floor).

**Out of scope.** The remaining step-3 modules (curator, MCP/D24, recommender, CLI/
doctor D26); removing the root-taking `store.py`/`projects.py` functions (step 4); the
CI AST check (step 5). Per-method mode enforcement stays deferred.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

No findings. I reviewed `git diff main...HEAD` against ADR-0015's READ/WRITE
handle semantics, spec §4/§5 scribe fail-safe and opt-in contracts, spec §10's
newer-schema refusal, and the existing hook wrappers. Both scribes now inspect
through a READ handle before project resolution/opt-in, open WRITE only for the
actual raw write, preserve the normal capture path, and keep Codex's
`RawConsumedError` retry on the same writer handle with a fresh `captured_at`.

Residual note: compared with the old code, a partial store with an existing memory
tree but an empty/no-op capture no longer creates `store.toml`, because the WRITE
handle is opened only after the empty-capture check. That is consistent with the
new "commit to writing before WRITE" shape and does not violate the scribe
contracts.

Verification run:
- `git diff main...HEAD`
- `uv run pytest tests/test_claude_scribe.py tests/test_codex_scribe.py tests/test_hook_schema_guard.py tests/test_cross_agent.py -q` — 53 passed
- `uv run python scripts/ci.py` — ruff, format check, mypy, and pytest passed;
  `1122 passed, 1 skipped`, total coverage `91.42%`

**Verdict:** approve — the scribe handle conversion preserves the capture
behavior that matters and closes the per-scribe guard placement without new
blocking regressions.
