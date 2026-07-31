# ADR-0015: Store chokepoint — a validated `StoreHandle` every path must obtain

- **Status:** Accepted
- **Date:** 2026-07-21
- **Resolves:** G1 ([known-gaps](../known-gaps.md)); the D11 schema guard (spec §10)
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

`open_store()` validates **only the store's own identity**: the `store.toml` schema
(the D11 comparison) and the store-root path. It **does not** validate
`registry.toml` — registry-parse failure is a separate concern with its own contract
(below). Migration-lock and partial-transaction detection are **out of scope here**:
they arrive with the schema-2 migration ADR that needs them; this ADR only reserves
the `MIGRATE` mode name so that ADR has a seam to fill.

**Registry parseability is not part of the schema guard** _(added in review — F1)._
A malformed or unreadable `registry.toml` is a different failure from a newer
`store.toml` schema, and folding it into `open_store()` as a hard error would break
the very MCP fail-soft contract D24 exists to protect. It stays **fail-soft per
surface**, exactly as today. After a handle opens:

- **Registry reads** (`load_registry`, `resolve_project`) treat a bad registry as an
  **empty** one: `memory_search` and `memory_list_projects` return `[]`, and
  `build_server()` resolves `current_project = None` — the behavior pinned by
  `test_search_and_list_projects_fail_soft_on_corrupt_registry` and
  `test_build_server_survives_corrupt_registry`
  ([`tests/test_mcp_server.py`](../../tests/test_mcp_server.py)).
- **Explicit-project reads that never consult the registry** — `memory_read_node(project, name)` —
  are unaffected by registry state entirely; a corrupt registry must not block them.
- A **registry write** (`register_project`, WRITE mode) may still hard-fail on a
  corrupt registry as it does today — a write path is allowed to refuse a store it
  cannot safely rewrite.

**Registry CONTAINMENT is part of this chokepoint** _(added 2026-07-31 — Codex
`P2-SAFETY-SECURITY-010`, PR #11 rounds 4–5)._ Parseability is fail-soft, but
*ownership* is not a parse concern at all. `<root>/registry.toml` is the store's
**selector** — it decides which project namespaces any sweep walks — so a symlinked
registry selects namespaces from **outside** the store while every document
enumeration remains correctly contained. That is the same raw-root class this ADR
exists to close, one level up: not "which file did we read" but "which store did we
read *for*".

Two things this ADR got wrong in the first attempt, both worth recording because the
shape recurs:

1. **Altitude.** The guard was first placed on `StoreHandle.load_registry()` alone.
   `resolve_project` and `register_project` delegate to the raw-root functions, which
   reach the low-level reader directly — so an external registry read as `{}` through
   the guarded method while still resolving the attacker-selected slug on the capture,
   recall, `status`, `curate`, `doctor` and MCP paths. **A guard on one method of an
   accessor trio is a guard on one caller.** Containment therefore lives at the
   `core/projects.py` accessor boundary itself, which every registry read in the
   process reaches — including doctor's sanctioned no-handle fallback, which by
   definition has no handle to guard. The handle's three methods are plain delegates,
   deliberately: a second guard above could disagree with the one below, and two
   containment definitions that can drift is the failure this ADR's whole design
   avoids.
2b. **And the enforcement model cannot be made universal one spelling at a time**
   _(round 7)._ Closing (2) by listing the names was still not enough: a **reassigned
   module alias** (`project_api = projects`) reached the helper with the checker printing
   OK, and closing *that* left **tuple unpacking** and a **class-held alias** — while the
   alias fix itself introduced a **false positive** on parameter shadowing, flagging the
   sanctioned `handle.load_registry()` pattern. Three rounds, three more spellings. The
   conclusion recorded here deliberately is that **the claim was wrong, not just the
   code**: an AST checker with no scope or dataflow semantics cannot support a universal
   "omission is impossible / no false positives" contract, and adding one form per review
   round does not converge on one. So step 5 is now described everywhere — here, in the
   guard's own docstring, in spec §10 and in G1 — as a **conservative, enumerated
   syntactic regression guard** with its residuals named. **The unavoidability guarantee
   rests on the deferred signature-removal step below, not on this check.** If that step
   is ever cancelled rather than deferred, this ADR's core claim needs revisiting, not
   another alias form.

2. **The enforcement model must be able to SEE the exemption.** The first fix promoted
   `_registry_path` to a public `registry_path` so the guard could name the file it was
   proving. But `scripts/check_store_chokepoint.py` matches accessors **by name**, and
   the `"registry.toml"` literal is hidden inside that helper — so the public spelling
   handed every production module a CI-approved way to reach the registry from a raw
   root, exactly the reintroduction step 5 exists to make mechanically hard (Codex
   `P2-SAFETY-SECURITY-013`). The helper is private again, and `registry_path`,
   `_registry_path` and `registry_is_contained` are all in the forbidden set, so
   re-publishing it fails the gate rather than silently reopening the hole. **Making a
   name public inside `core` is not the problem; making it public while the guard
   cannot see it is.**

Posture, mirroring the read/write split above: read paths fail **soft** to empty (one
hostile selector must not blank a surface — §13, and the regression this project
already paid for once); the read-for-rewrite path fails **closed**, before the read,
since reading an external registry would import its entries into the mapping handed to
the writer and then follow the symlink to rewrite a file outside the store. Because
"uncontained" and "absent" both read as empty, `doctor` gets
`projects.registry_is_contained(root)` — allow-listed by (file, name) as its third
sanctioned raw-root read — and reports the uncontained case as an **error**. Empty is
the right operational answer; it must not read as *healthy*.

The handle carries only the `store.toml` schema verdict; registry validation lives on
the registry accessors, not the whole-store gate. Concretely, the per-surface
fail-soft wrappers that exist today (`_safe_registry`, `build_server()`'s
`try/except`) are preserved — requiring a handle changes their *signature*, not their
tolerance.

**Enforcement.** The chokepoint only works if the handle is unavoidable:

- `StoreHandle.__init__` is private; `open_store()` is the sole entrypoint.
- Every store and project API takes a `StoreHandle`, not a raw `root: Path` —
  `memory_dir`, `load_registry`, `register_project`, `resolve_project`,
  `list_raw` / `list_curated` / `write_raw` / `upsert_curated` / `write_node` /
  `rebuild_index`, and the recommender's corpus/ledger accessors.
- The raw-`Path` signatures are removed (not merely deprecated) — a lingering
  overload re-arms the same footgun.
- A **CI check** (AST-based) forbids constructing store paths from a bare root or
  reading `registry.toml` / `store.toml` / `memory/` outside `core/store.py`,
  `core/store_handle.py`, and `core/projects.py`.

**D24 — MCP failures surface as structured tool errors (spec §13).**
`build_server()` opens a `READ` handle. A newer-schema store must **not** raise at
startup: `build_server()` still constructs, `resources/list` still returns a valid
(possibly empty) array, and each tool returns a structured incompatibility error
when invoked. The handle is opened once and its outcome captured; tools branch on
it rather than re-checking. This structured error is for an unsupported **`store.toml`
schema** only; a corrupt **registry** is not conflated with it — that stays fail-soft
(empty), unchanged from today, per the registry contract above.

**D25 — `uninstall --purge-store` is exempt.** It opens a `PURGE` handle and may
`rmtree(<root>)` even when the schema is unsupported or unparseable — with the
existing explicit user confirmation. **Spec §10 gains this exemption in writing**
(the only sanctioned mutation of an unsupported store's **schema-versioned content**
— `memory/`, `registry.toml` — is its deletion).

*Refined during step-4d implementation (recorded here and in spec §10 to keep the two
in step):* the **config-backup facility** (`backups.backup_files` / `restore_backup`,
under `<root>/backups/`) is a **schema-independent maintenance exception**, not a
mutation of schema-versioned content. It copies agent-config files *verbatim* and never
reads or writes `memory/` / `registry.toml`, so it is safe on a store of any schema —
which is required, because uninstall and disaster-recovery must not be bricked by a
store whose schema the binary cannot operate on. It therefore stays outside the
schema-refusing handle: `init --agent` opens a `READ` handle before installing hooks and
`uninstall --purge-store` opens a `PURGE` handle before deleting (and skips the backup),
but the non-purge-uninstall and `--restore-backup` paths that use the facility open no
handle. "Deletion is the only mutation" governs schema-versioned content only.

**D26 — `doctor` reuses the guard, read-only.** `cli/diagnostics._store_checks`
stops re-implementing the schema comparison and instead opens a `DOCTOR` handle,
mapping `schema is None` → "not initialized" (warn), `schema > MAX` → "unsupported"
(error), else ok. One comparison, one place.

**Migration order** (land as separate reviewable PRs):

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
  policy, and the central egress gate — the later trust-boundary work this handle is
  the foundation for — layer on top as their *own* ADRs, and the schema-2 migration
  ADR is where migration-lock and partial-transaction detection land, using the
  `MIGRATE` mode reserved here.
- **Spec appendix** updates: §10 gains the `open_store()` chokepoint + mode table as
  the store-access contract and the `uninstall --purge-store` exemption (D25); §13
  gains the D24 rule that an unsupported-schema store yields structured tool errors,
  never a startup failure. **This ADR is the proposal; the spec appendix is the
  law** — fold these in when implementing.
- **Follow-up for known-gaps.** On accept, mark G1 `fixed` (link the migration PRs)
  once step 4 lands; the CI check (step 5) is what keeps the *ordinary* reintroduction
  out — see the round-7 note above for what it does and does not prove, and why the
  deferred signature removal is still the load-bearing part.

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
