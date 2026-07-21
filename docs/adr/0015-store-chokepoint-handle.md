# ADR-0015: Store chokepoint — a validated `StoreHandle` every path must obtain

- **Status:** Proposed
- **Date:** 2026-07-21
- **Resolves:** G1 ([known-gaps](../known-gaps.md)); the D11 schema guard (spec §10); hardening-plan §15 / Phase 0
- **Supersedes:** none

## Context

The D11 schema guard exists to honor spec §10: *"refuse to **operate** on a
schema newer than the binary."* The guard itself — `store.ensure_store_metadata()`
([`store.py:92`](../../src/neurobase/core/store.py)) — is correct. Its **placement**
is the defect. It is called by hand at five sites, so protection is opt-in per
command and holds only where an author remembered:

| Call site | Reached by |
|---|---|
| `core/store.py:118` (`ensure_tree`) | anything that creates a tree |
| `cli/__init__.py` (`_check_store_schema`) | the CLI commands that call it |
| `adapters/recall_common.py` (`build_context`) | session-start recall |
| `adapters/claude/scribe.py` | Claude capture |
| `adapters/codex/scribe.py` | Codex capture |

Nothing enforces the guard *at the store boundary*: `store.memory_dir()` and
`projects.load_registry()` read or write a store of any schema. G1 records three
individually-verified defects that follow from this, not a hand census (the
known-gaps entry is explicit that a hand-maintained call-path census is the wrong
artifact — three attempts produced three wrong tables):

1. **`init --guided` mutates before it guards.** `_init_guided` →
   `projects.register_project()` reads *and unconditionally rewrites*
   `registry.toml` ([`projects.py:101`](../../src/neurobase/core/projects.py), `_write_registry`)
   and only *then* calls `ensure_tree()`, where the guard finally runs. A
   newer-schema store is **written to** before it is ever checked — the exact
   outcome D11 exists to prevent.
2. **`mcp serve` never guards at all.** `build_server()` resolves the project from
   `registry.toml` at startup ([`mcp/server.py:120`](../../src/neurobase/mcp/server.py))
   and `memory_search` / `memory_read_node` / `memory_list_projects` /
   `recommendations_list` read store state with no check. `memory_remember`'s
   *write* is guarded via `ensure_tree`, but its reads precede it.
3. **`status --recommender` returns before the guard**, reading `proposals/`, the
   ledger, and — via `corpus.load_corpus` — `registry.toml`, `curated/`, and
   `raw/`.

And the root cause is broader than any list: `projects.resolve_project()` reads
`registry.toml` and runs first in most flows (`status`, `curate`, every hook), so
even "guarded" commands typically touch the store before the guard. `enable`'s own
comment — `# before registry.toml is touched` — shows registry reads were *meant*
to sit behind the guard; nothing makes them.

The known-gaps entry states plainly that this **needs an ADR**: either route
changes the contract (exemptions) or the architecture (a store chokepoint), and it
*"cannot be settled by quietly editing code."* Three constraints any fix must
respect are already recorded there:

- **MCP cannot hard-fail at startup.** Spec §13 requires `resources/list` to always
  answer with a valid array and never error (Codex drops a server that errors
  there). An MCP refusal must surface as a **structured tool error**, not an
  exception at `build_server()`.
- **`uninstall --purge-store` should be exempt.** Deleting a store you cannot parse
  is the safe escape hatch *from* a newer-schema store; the exemption must be
  written into spec §10, not left implicit.
- **`doctor` must keep reporting, never refuse** — that is its job. It currently
  *re-implements* the schema comparison inline
  ([`cli/diagnostics.py:106-118`](../../src/neurobase/cli/diagnostics.py),
  `_store_checks`) instead of reusing the guard; the two will drift.

## Decision

Introduce a single validated store handle that every path must obtain before
touching the store. Make the omission that produced G1 a **type error, not a
silent hole**. This ADR is a **schema-1, no-behavior-change refactor** — it does
*not* bump `STORE_SCHEMA_VERSION` and does *not* add profiles, policy, or egress
(those are later Phase-0 work over schema 2, built on this handle).

**D23 — The store chokepoint (`StoreHandle`).** Add `core/store_handle.py`:

```python
class StoreMode(Enum):
    READ; WRITE; DOCTOR; MIGRATE; PURGE

class StoreHandle:
    # constructor is private — the ONLY way in is open_store()
    root: Path
    mode: StoreMode
    schema: int | None          # None ⇒ uninitialized (no store.toml yet)

def open_store(root: Path, mode: StoreMode = StoreMode.READ) -> StoreHandle: ...
```

`open_store()` is the one place the D11 comparison lives. What it does, by mode:

- **`READ`** — validate `store.toml` if present; a newer schema raises
  `UnsupportedSchemaError`. A **missing** `store.toml` is *not* an error — it is an
  uninitialized store (`schema = None`); the handle opens and readers behave as
  today (empty registry, no facts). READ **never writes** `store.toml`.
- **`WRITE` / `MIGRATE`** — validate as READ, and create `store.toml`
  (`schema = 1`, `created_at`) on first use, exactly as `ensure_store_metadata`
  does now. This is what closes defect (1): a WRITE handle validates/creates the
  metadata **before** any `register_project` can run, because that function now
  requires the handle.
- **`DOCTOR`** — inspect any schema, including unsupported, **without mutating**.
  Returns a handle whose `schema` may exceed `STORE_SCHEMA_VERSION`; the caller
  reports rather than refuses. Never creates `store.toml` (preserves the existing
  read-only doctor contract).
- **`PURGE`** — open even an unparseable or newer-schema store, so
  `uninstall --purge-store` can delete it (D25).

`open_store()` validates: `store.toml` schema, registry parseability, and store-root
identity. (Migration-lock and partial-transaction markers named in hardening-plan
§15.2.3 are **out of scope here** — they arrive with the schema-2 migration ADR
that needs them; this handle just reserves `MIGRATE`.)

**Enforcement.** The chokepoint only works if the handle is unavoidable:

- `StoreHandle.__init__` is private; `open_store()` is the sole entrypoint.
- Every store and project API takes a `StoreHandle`, not a raw `root: Path` —
  `memory_dir`, `load_registry`, `register_project`, `resolve_project`,
  `list_raw` / `list_curated` / `write_raw` / `upsert_curated` / `write_node` /
  `rebuild_index`, and the recommender's corpus/ledger accessors.
- The raw-`Path` signatures are removed (not merely deprecated) — a lingering
  overload re-arms the same footgun.
- A **CI check** (AST-based, per hardening-plan §15.3) forbids constructing store
  paths from a bare root or reading `registry.toml` / `store.toml` / `memory/`
  outside `core/store.py`, `core/store_handle.py`, and `core/projects.py`.

**D24 — MCP failures surface as structured tool errors (spec §13).**
`build_server()` opens a `READ` handle. A newer-schema store must **not** raise at
startup: `build_server()` still constructs, `resources/list` still returns a valid
(possibly empty) array, and each tool returns a structured incompatibility error
when invoked. The handle is opened once and its outcome captured; tools branch on
it rather than re-checking.

**D25 — `uninstall --purge-store` is exempt.** It opens a `PURGE` handle and may
`rmtree(<root>)` even when the schema is unsupported or unparseable — with the
existing explicit user confirmation. **Spec §10 gains this exemption in writing**
(the only sanctioned mutation of an unsupported store is its deletion).

**D26 — `doctor` reuses the guard, read-only.** `cli/diagnostics._store_checks`
stops re-implementing the schema comparison and instead opens a `DOCTOR` handle,
mapping `schema is None` → "not initialized" (warn), `schema > MAX` → "unsupported"
(error), else ok. One comparison, one place.

**Migration order** (hardening-plan §15.2.4 — land as separate reviewable PRs):

1. Introduce `store_handle.py` + `open_store()` alongside today's API (no callers
   yet).
2. Convert `core/store.py` and `core/projects.py` to require a handle; port the
   five existing guard sites; delete `_check_store_schema`'s standalone role
   (it becomes `open_store(..., WRITE)` at each command's top).
3. Convert curator, adapters (recall + both scribes), MCP (D24), recommender, CLI.
4. Remove the raw-`Path` store APIs.
5. Add the CI AST check (step 4 must land first, or the check fails on the code it
   is meant to protect).

## Consequences

- **G1 closes at the type level.** A new call site cannot touch the store without an
  `open_store()`, so the next author *cannot* forget the guard — the compiler (well,
  the type checker + CI) forces it. `init`'s mutate-before-guard, `mcp serve`'s
  no-guard, and `status --recommender`'s early return all resolve as a consequence
  of requiring the handle, not by patching each in isolation.
- **The pre-guard registry read disappears.** `resolve_project`/`load_registry`
  require a handle, so the pervasive "read `registry.toml` before the guard" pattern
  can no longer compile.
- **`doctor` de-duplicates.** One schema comparison (D26); the drift risk the
  known-gaps entry flags is removed.
- **Cost is one validation per invocation.** `open_store()` reads/parses
  `store.toml` once; pass the handle down rather than re-opening. Hooks already call
  the guard once today, so no latency regression (ADR-0003 budget unaffected).
- **No schema bump, no data migration.** Existing stores keep `schema = 1` and every
  command behaves as before; this is a pure interior refactor. Profiles, project
  policy, and the central egress gate (hardening-plan Phase 0 remainder) layer on
  top as their *own* ADRs — and the schema-2 migration ADR is where migration-lock
  and partial-transaction detection land, using the `MIGRATE` mode reserved here.
- **Spec appendix** updates: §10 gains the `open_store()` chokepoint + mode table as
  the store-access contract and the `uninstall --purge-store` exemption (D25); §13
  gains the D24 rule that an unsupported-schema store yields structured tool errors,
  never a startup failure. **This ADR is the proposal; the spec appendix is the
  law** — fold these in when implementing.
- **Follow-up for known-gaps.** On accept, mark G1 `fixed` (link the migration PRs)
  once step 4 lands; the CI check (step 5) is what keeps it fixed.

## Alternatives considered

- **Keep it per-command, add the missing guard calls** (G1 fix-direction 2) —
  rejected: cheaper today, but it re-arms the identical footgun tomorrow. It is the
  exact process that produced three wrong censuses; the defect is *"a path is
  protected only if its author remembered,"* and adding more remembered calls does
  not remove the *only-if-remembered*.
- **Guard inside `memory_dir()` / `load_registry()` only, keeping raw-`Path`
  signatures** (G1 fix-direction 1, lighter form) — rejected: it plugs today's known
  holes but functions still accept a bare `root`, so nothing at the type level forces
  a future accessor through the guard. The point is to make omission *impossible*,
  not merely currently-absent; that requires the handle in the signature.
- **Bump to schema 2 now and fold profiles/policy into this change** — rejected:
  scope creep on a load-bearing refactor. A minimal schema-1 chokepoint is
  independently valuable (it closes a shipped `major` defect), reviews cleanly
  through the Codex relay, and de-risks the profile/egress/migration work that
  depends on it. Keep the first slice small.
- **A committed, runnable entry-point enumerator instead of a chokepoint** — that is
  the *audit* artifact the known-gaps entry says to build *if exhaustive accounting
  is ever needed*; it is not a fix. The chokepoint makes exhaustive accounting
  unnecessary — there is one entry point, by construction.
