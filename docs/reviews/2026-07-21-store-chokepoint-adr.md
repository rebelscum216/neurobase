---
slug: store-chokepoint-adr
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-store-chokepoint
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: ADR-0015 — store chokepoint (`StoreHandle`) as the G1 fix direction

## Brief  _(Author — Claude)_

**Intent.** Settle the architectural decision G1 says *"cannot be settled by
quietly editing code."* G1 (known-gaps) is the shipped `major` defect that the D11
schema guard is enforced per-command by hand, so a path is protected only if its
author remembered — and several didn't (`init` mutates before it guards, `mcp
serve` never guards, `status --recommender` returns before the guard). ADR-0015
proposes a single validated `StoreHandle` obtained via `open_store(root, mode)`,
required in the signature of every store/registry accessor, so the omission becomes
a **type error, not a silent hole**. This is a decision doc only — no code changes
in this diff. It is Phase 0 of the reliability/hardening plan.

**Scope.** Branch `adr-0015-store-chokepoint`, `git diff main...HEAD`. Key files:
- `docs/adr/0015-store-chokepoint-handle.md` — new ADR. Decisions D23 (the
  chokepoint + `StoreMode` READ/WRITE/DOCTOR/MIGRATE/PURGE semantics), D24 (MCP
  surfaces unsupported-schema as a structured tool error, never a startup fail —
  spec §13), D25 (`uninstall --purge-store` exemption, to be written into spec §10),
  D26 (`doctor` reuses the guard instead of re-implementing the comparison).
- `docs/adr/README.md` — index row for 0015 (`Proposed`).
- `docs/reviews/2026-07-21-store-chokepoint-adr.md` — this baton.

**Focus areas.** This is an architecture/contract review, so weigh the *decisions*,
not prose:
1. **Mode semantics.** Is the per-mode `store.toml` create/validate matrix right?
   Specifically READ-on-uninitialized returning `schema=None` (open, don't write)
   vs. erroring; WRITE/MIGRATE creating on first use exactly as
   `ensure_store_metadata` does today; DOCTOR/PURGE opening an unsupported schema
   without mutating.
2. **Does D24 actually satisfy the spec §13 constraint** that `resources/list` never
   errors (Codex drops a server that errors there)? Is "open READ handle at
   `build_server()`, branch per-tool on the captured outcome" the right shape, or
   does any read path still risk raising at startup?
3. **The scoping call:** schema-1, no-behavior-change refactor; `MIGRATE` mode
   reserved but migration-lock / partial-transaction detection deferred to the
   schema-2 ADR. Is deferring those correct, or does reserving `MIGRATE` now without
   its checks create a half-built seam?
4. **D25 spec change.** The purge exemption modifies the spec §10 contract
   (*"refuse to operate on a newer schema"*). Is an ADR the right place to authorize
   that, and is the exemption scoped tightly enough (delete-only, with existing
   confirmation)?

**Known risks / tradeoffs.**
- Removing the raw-`Path` store APIs (vs. deprecating) is deliberate — a lingering
  overload re-arms G1 — but it's a wide, mechanical migration touching curator,
  adapters, MCP, recommender, CLI. The ADR sequences it as 5 separate PRs; judge
  whether that order (CI AST check last) is sound.
- The ADR intentionally does **not** bump `STORE_SCHEMA_VERSION`. If a reviewer
  thinks profiles/policy should force schema 2 *now*, that changes the whole slice.

**How to verify.** No code to run. Read the ADR against
[docs/known-gaps.md](../known-gaps.md) (G1 root cause + the three named defects),
[spec §10](../neurobase-spec-appendix.md) (the D11 guard contract this touches) and
spec §13 (MCP resources contract), and the cited call sites:
`core/store.py:92,118`, `core/projects.py:101` (`_write_registry`), `mcp/server.py:120`,
`cli/diagnostics.py:106-118` (the duplicated comparison D26 collapses).

**Out of scope.** The later Phase-0 workstreams that layer on this handle —
profiles, project-policy schema, the central egress gate, hook receipts — each get
their own ADR. Don't flag their absence here. Implementation correctness is moot
(no code yet); review the decision, its constraints, and whether it actually closes
G1's root cause rather than a subset of its symptoms.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _(pending)_
