---
slug: store-handle-impl
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-store-handle
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: StoreHandle chokepoint — ADR-0015 migration step 1

## Brief  _(Author — Claude)_

**Intent.** Implement **step 1 of the ADR-0015 migration**: introduce the store
chokepoint — a single validated `open_store()` entrypoint returning a `StoreHandle`
— so the D11 schema guard (spec §10, *"refuse to operate on a schema newer than the
binary"*) lives in exactly one place instead of five hand-placed call sites (the G1
defect). Step 1 is deliberately **additive and callerless**: it ships alongside the
existing `root: Path` store API and nothing consumes it yet. Later steps (2–5)
convert `store`/`projects`/adapters/`mcp`/`recommender`/`cli` to require a handle,
delete the raw-`Path` signatures, and add the CI AST check.

**Scope.** Branch `adr-0015-store-handle`, `git diff main...HEAD` (single commit
`b0ec3ed`). Key files:
- `src/neurobase/core/store_handle.py` — **new.** `StoreMode` enum
  (READ/WRITE/DOCTOR/MIGRATE/PURGE), the frozen `StoreHandle` dataclass with a
  private (token-guarded) constructor, and `open_store(root, mode, profile=None)`.
- `tests/test_store_handle.py` — **new.** 25 cases pinning per-mode behavior, the
  profile qualifier, and construction enforcement.

**Focus areas.** Where I most want your eyes:
1. **Faithfulness to ADR-0015 D23–D26.** Does `open_store`'s per-mode behavior match
   the accepted ADR? Specifically: READ/DOCTOR never write; WRITE/MIGRATE create
   `store.toml` when absent; DOCTOR reports a newer schema without raising or
   mutating; PURGE opens even an unparseable/newer store (D25).
2. **The D11 comparison is single-sourced and fail-closed.** `open_store` reuses
   `store.ensure_store_metadata` for the create-when-absent write (so the on-disk
   `store.toml` format stays in one place) and does its own read-side
   `_parse_schema`. Is the schema check correct and does it fail closed on
   unreadable/non-int/`bool` metadata? Is there any path where an unvalidated store
   escapes as a READ/WRITE/MIGRATE handle?
3. **Construction enforcement.** `StoreHandle.__init__` is guarded by a module-private
   token so `open_store` is the only constructor — the "an unvalidated store is
   unrepresentable" property. Is the token pattern actually airtight (frozen +
   `__post_init__` raise), and is `_token` correctly excluded from equality/repr?
4. **The `profile` qualifier (ADR-0016 D28).** Built into the signature from commit 1
   as required, carried onto the handle unchanged. Under schema 1 there is no
   `default_profile` to resolve against, so it is simply recorded. Is "carry, don't
   resolve, don't validate yet" the right call for step 1, or should step 1 already
   constrain the profile string?

**Known risks / tradeoffs.**
- **DOCTOR on genuinely-corrupt `store.toml` raises** rather than reporting. I kept
  `schema: int | None` unambiguous (None ⟺ *no* `store.toml`), so DOCTOR tolerates a
  *newer valid-int* schema (the real D11/D26 case) but a hand-corrupted TOML surfaces
  as `UnsupportedSchemaError`. Rationale in the module docstring/comments: D26's
  "never refuse" concerns the schema *comparison*, not silently accepting corrupt
  metadata; the doctor conversion (step 2–3) decides its presentation. Flag if you
  think DOCTOR must be fully non-raising now.
- **No profile validation** (see focus area 4).
- **Root is taken as-is** (`Path(root)`, no `expanduser`/`resolve`) to match how the
  existing `store.py` functions treat the root they're handed; callers resolve via
  `store.resolve_root()` upstream.

**How to verify.**
- `git diff main...HEAD`
- `uv run pytest tests/test_store_handle.py -q` (25 pass)
- `uv run python scripts/ci.py` — full gate green (1107 passed, 1 skipped;
  `store_handle.py` at 100% coverage; ruff/format/mypy pass).

**Out of scope.** Converting any existing caller to the handle (steps 2–5); removing
raw-`Path` store signatures; the CI AST check; the schema-2 bump / `registry.toml`
records / profile *resolution* / migration mechanics (ADR-0016, later ADRs);
`registry.toml` parseability (stays fail-soft per ADR-0015 F1, not folded into the
schema guard here).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _(pending)_
