---
slug: doctor-handle
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-doctor-handle
diff: git diff main...HEAD
created: 2026-07-22
---

# Review: convert `neurobase doctor` to the DOCTOR handle — ADR-0015 step 3.5

## Brief  _(Author — Claude)_

**Intent.** Continue ADR-0015 step 3 by routing `doctor`'s store-health checks
through a validated `DOCTOR`-mode `StoreHandle` instead of hand-parsing
`store.toml`. This is the module that carries the **step-1 review carry-in**: a
`DOCTOR` handle raises `UnsupportedSchemaError` on a genuinely-corrupt
`store.toml`, and `doctor` MUST catch/report it so it stays a read-only
reporting surface and never crashes (D26).

**Scope.** Branch `adr-0015-doctor-handle`, `git diff main...HEAD` (implementation
commit `096518a`). Key files:

- `src/neurobase/cli/diagnostics.py` — `_store_checks` opens one
  `open_store(root, StoreMode.DOCTOR)`; a new `_project_check` helper does the
  registry resolution.
- `tests/test_cli_doctor.py` — three regression tests over previously-uncovered
  branches: too-new schema reported, corrupt `store.toml` caught, read-only
  no-write invariant.

**Focus areas.**

1. **D26 corrupt-store catch (the carry-in).** `open_store(DOCTOR)` tolerates a
   schema *newer* than supported (carries the int, no raise) but still raises
   `UnsupportedSchemaError` on unreadable/invalid TOML or a missing/non-int
   `schema`. `_store_checks` catches only that, reports `str(exc)` verbatim, and
   sets `handle = None`. Confirm doctor never crashes on any corrupt-metadata
   shape and the reported detail reads sensibly (`exc` already embeds the path).
2. **Schema-branch behavior vs. the old hand-parse.** Old code reported a
   missing/non-int schema as `"unsupported schema {schema!r}"` (error). It now
   routes through the `except` branch and reports the `UnsupportedSchemaError`
   message instead (still **error** status, so `has_errors`/exit code are
   unchanged — only the detail text differs). A too-new int is still an explicit
   `"unsupported schema {n}"` error via `handle.schema`. Confirm the status
   parity is what matters and the text change is acceptable.
3. **`_project_check` fallback boundary.** Registry resolution is independent of
   `store.toml` health, so the project check resolves through `handle` when we
   have one and **falls back to `projects.resolve_project(root, cwd)` directly
   when the store is corrupt** (no handle) — deliberately preserving the old
   behavior that a broken `store.toml` doesn't also mask an otherwise-healthy
   project check. This is a knowing direct root-taking call that step 4 must
   handle; flag if you'd rather skip the project check entirely on a corrupt
   store instead.
4. **Read-only invariant.** DOCTOR is not a creating mode, so reporting on an
   uninitialized store must not materialize `store.toml`. Pinned by
   `test_doctor_is_read_only_and_does_not_create_store_toml`.

**Known risks / tradeoffs.**

- The `_project_check` direct fallback is an intentional step-4 wart (a
  root-taking `projects.resolve_project` call on the corrupt-store path).
- Detail-text change for the missing/non-int-schema case (status unchanged).

**How to verify.**

- `git diff main...HEAD`
- `uv run pytest tests/test_cli_doctor.py -q` — 14 passed.
- `uv run python scripts/ci.py` — full gate green: ruff, format, mypy,
  `1136 passed, 1 skipped`; coverage 91.70%.

**Out of scope.** The CLI-command WRITE conversions (`_check_store_schema →
open_store(..., WRITE)` at each command top); recommender; the deferred
distill/linkify edges; per-method mode enforcement; removing root-taking APIs
(step 4) and the AST enforcement check (step 5).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->
