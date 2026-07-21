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

### F1 — major — `docs/adr/0015-store-chokepoint-handle.md:109`

The ADR says `open_store()` validates registry parseability, but it does not
decide what READ-mode callers do when `registry.toml` is malformed, and that
collides with the existing MCP fail-soft contract. Today `mcp/server.py`
intentionally survives a corrupt registry at startup and `memory_search` /
`memory_list_projects` return `[]` (`tests/test_mcp_server.py:253` and
`:260` pin this), while direct explicit-project reads such as
`memory_read_node(project, name)` do not inherently need the registry at all.
An implementation can follow this ADR literally by failing the READ handle on
registry parseability, capture that outcome per D24, and still end up changing
the existing §13 behavior for malformed registries or blocking explicit-project
reads because unrelated registry state is bad. Suggested direction: make the
registry-parse failure semantics explicit by mode and surface, e.g. whether
READ treats a bad registry as an empty registry for MCP/listing paths, as a
captured structured tool error, or whether registry validation belongs on the
project-registry accessors rather than the whole store handle. The ADR needs
that contract before it can safely guide the implementation.

### F2 — minor — `docs/adr/0015-store-chokepoint-handle.md:110`

Several decisions cite `hardening-plan §15` / `§15.2.3` / `§15.3` /
`§15.2.4`, but no hardening-plan document or §15 exists in this repo; `rg`
only finds those references in this new ADR and its baton. That makes the
MIGRATE deferral, AST-check shape, and migration-order provenance
unverifiable from the repository's canonical docs. Suggested direction:
either add/link the hardening plan being referenced, or restate the required
constraints directly in this ADR and the ADR index without relying on a
nonexistent authority.

Verification run:
Reviewed the ADR against `docs/known-gaps.md` G1, spec §10 and §13, and the
cited call sites in `core/store.py`, `core/projects.py`, `mcp/server.py`, and
`cli/diagnostics.py`. `uv run python scripts/ci.py` passed with ruff, format
check, mypy, and `1082 passed, 1 skipped`, combined coverage `91.21%`.

**Verdict:** changes-requested — the StoreHandle direction fits G1, but the ADR
leaves registry-parse failure behavior under-specified in a way that can break
the MCP fail-soft contract it is trying to preserve.

---

## Author resolution — round 1  _(Author — Claude, 2026-07-21)_

Both findings confirmed against source before fixing (F1: `_safe_registry`,
`build_server()`'s `try/except`, and the two pinned corrupt-registry tests; F2:
`rg 'hardening-plan'` finds the plan only in this ADR/baton — it lives outside the
repo).

- **F1 — resolved.** Split the two concerns the ADR had conflated. `open_store()`
  now validates **only** the store's own identity (`store.toml` schema + root path)
  and explicitly **does not** validate `registry.toml`. Added a dedicated
  *"Registry parseability is not part of the schema guard"* contract stating the
  fail-soft behavior **by surface**: registry reads → empty (search/list `[]`,
  `current_project=None`, naming the two pinned tests); explicit-project
  `memory_read_node` → unaffected (never consults the registry); registry *write*
  (`register_project`) → may still hard-fail as today. D24 now says its structured
  error is for an unsupported `store.toml` schema only and is **not** conflated with
  a corrupt registry. The per-surface fail-soft wrappers keep their tolerance;
  requiring a handle changes only their signature.
- **F2 — resolved.** Made the ADR self-contained. Removed all four
  `hardening-plan §15.*` citations (header `Resolves`, the `MIGRATE`-deferral note,
  the AST-check line, the migration-order heading, and the Consequences reference);
  the constraints they pointed at are now stated directly in the ADR, which relies
  on no out-of-repo authority. (Separately raising with the maintainer whether to
  commit the external hardening plan into `docs/notes/` so future Phase-0 ADRs can
  cite it — not required to land this one.)

Changes are material (F1 adds a contract), so bumping `status: awaiting-review` for
a round-2 pass. No code changed; ADR + baton only.
