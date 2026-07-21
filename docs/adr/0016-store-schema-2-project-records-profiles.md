# ADR-0016: Store schema 2 — project-record registry, logical profiles, forward-compatible identifiers

- **Status:** Accepted
- **Date:** 2026-07-21
- **Resolves:** hardening plan §12 / §16 / §25 ([plan draft](../notes/2026-07-21-hardening-plan-draft.md), [ratified decisions](../notes/2026-07-21-hardening-decisions.md)); Issues 6 + 10
- **Supersedes:** none — extends the (accepted, unimplemented) `StoreHandle` of [ADR-0015](0015-store-chokepoint-handle.md)

## Context

The hardening plan's trust-boundary work (egress policy in [ADR-0017](0017-egress-policy-gate.md),
scope-safe recall, cross-project mining limits) all needs a place to record
*per-project policy* and a *partition* a fact belongs to. Neither exists today:

- **`registry.toml` collapses a project to a bare root list.** `load_registry()`
  ([`core/projects.py:37`](../../src/neurobase/core/projects.py)) returns
  `{slug: [roots...]}` and `_write_registry()` round-trips exactly that. There is
  nowhere to hang a profile, a privacy mode, allowed backends, or a monorepo
  subpath.
- **There is no partition primitive.** Every registered project shares one flat
  namespace, one proposal store, and one recommender ledger. §12 (cross-project
  mining) and §16 (teams/clients) both require an isolation boundary that does not
  exist.
- **Event records carry no stable identity.** The recommender ledger is already
  append-only JSONL (`_append_ledger`, [`recommender/proposals.py:434`](../../src/neurobase/recommender/proposals.py))
  but its lines are keyed only by `slug` — there is no per-event ULID and no
  content hash, so nothing supports the conflict-safe merge §16.3.3 wants for a
  future multi-machine story.

The ratified decisions (2026-07-21) settled the shape: profiles are **logical
partitions under one visible store root** (§27 Q5), monorepo `match_subpath` is
**folded in now** while the schema is already moving, and the multi-machine
**conflict-safe primitives are baked in now** even though sync itself stays
documentation-only. These are all `store.toml` structural changes, so they ride
**one** schema bump rather than several — retrofitting ULIDs or append-only
semantics onto a shipped schema 2 later would be far more disruptive than adding
them in the same migration.

ADR-0015 established `open_store(root, mode) -> StoreHandle` as the sole store
entrypoint but is **schema-1, no-behavior-change** and explicitly left profiles,
policy, and migration mechanics to "later Phase-0 work over schema 2, built on
this handle." This is that ADR. Because ADR-0015 is accepted but **not yet
implemented**, this ADR pins the profile qualifier the handle must carry *before*
migration step 1 writes `core/store_handle.py`, so it is built in from the start
rather than reworked.

## Decision

Bump `STORE_SCHEMA_VERSION` to **2**. The migration is explicit, backed up, and
reversible-forward-only (an old binary already refuses a newer schema —
[`store.py:107`](../../src/neurobase/core/store.py)).

**D27 — `registry.toml` entries become project *records*.** Each entry gains
policy fields; `roots` is preserved:

```toml
[projects.neurobase]
roots = ["/Users/me/code/neurobase"]
profile = "open-source"
match_subpath = ""            # "" ⇒ whole repo (D30)
privacy = "default"           # egress mode; see ADR-0017 (D32)
allowed_brains = ["claude-cli", "codex-cli"]
allow_transcript_distill = true
allow_cross_agent_backend = false
allow_cross_project_mining = false   # explicit-only (§27); default false

[profiles.open-source]
default_scope = "profile"
```

`load_registry()` returns a typed `ProjectRecord`, not `{slug: [roots]}`. A
**schema-1** registry (bare `{roots}` entries) read under a schema-2 binary is a
migration trigger, never silently coerced. Unknown policy values fail closed
(§4.5): an unrecognized `privacy` mode is treated as the most restrictive, not
ignored.

**D28 — Profiles are logical partitions under one root; the handle is
profile-qualified.** One visible store root; a profile is a namespace enforced in
the API, not a separate directory tree. ADR-0015's entrypoint gains a profile:

```python
def open_store(
    root: Path,
    mode: StoreMode = StoreMode.READ,
    profile: str | None = None,   # None ⇒ the store's default_profile
) -> StoreHandle: ...
```

`StoreHandle` carries `profile`. Proposal stores, recommender ledgers, and default
artifact targets are addressed **through** the handle's profile, so cross-profile
access is a different handle, not a path the caller assembles. Markdown stays
inspectable in one place; isolation is a type-level property of the handle, matching
ADR-0015's "omission is a type error" posture. The handle is scoped to a *profile*,
not a single project — a profile can hold several project records with different
privacy — so per-project policy is **not** read off the handle. The egress gate
resolves each source project's record (D27) from the payload's *derived* provenance
(the source project slugs it actually draws from), never from the handle's profile
alone ([ADR-0017](0017-egress-policy-gate.md) D33). Choosing logical over physical
partitions keeps ADR-0015's single-root model intact (a physical split would mean
multiple roots, multiple handles, and heavier cross-profile tooling for no privacy
gain that in-API enforcement doesn't already give).

**D29 — Forward-compatible identifiers and append-only semantics.** So a future
multi-machine sync (§16.3.3) can merge without a schema change:

- Every appended event (recommender ledger, and the hook receipts / fact-event
  ledgers introduced by sibling ADRs) carries a **stable ULID** `event_id`,
  generated once at write. ULIDs are lexically sortable and collision-free across
  machines without coordination.
- Append-only ledger semantics are **generalized** from the recommender's existing
  JSONL to a shared `core` append helper — never rewrite a line, only append. This is
  the **normal-operation** invariant; the schema-1→2 migration (D31) is the single
  sanctioned, backed-up exception that rewrites the ledger once to assign ULIDs to
  pre-schema-2 events, after which every event — legacy included — carries one, so a
  future sync has **no legacy `event_id` hole** to special-case.
- Curated facts and nodes carry a **content hash** in frontmatter so a future sync
  can detect a genuine conflict (two machines edited the same fact) versus an
  identical copy, and mark conflicts `disputed` rather than silently merging.

No sync code ships now. Only the identifiers and semantics that would be painful to
retrofit do.

**D30 — Monorepo subproject resolution (`match_subpath`).** `resolve_project()`
([`core/projects.py:105`](../../src/neurobase/core/projects.py)) already collapses
worktrees to the git common root and longest-prefix-matches on roots. It gains a
second dimension: after selecting candidate records by git common root, pick the one
whose `match_subpath` **contains** the cwd. Two records can share a `roots` entry and
differ only by `match_subpath`, giving `apps/web` and `apps/api` isolated memories
inside one repo. An empty `match_subpath` matches the whole repo (the current
behavior, preserved as the default).

`match_subpath` is **path-segment bounded, not a string prefix** _(added in review —
F3)._ It is a normalized relative directory path; absolute or `..`-bearing values are
rejected at registry write. A record matches only when the cwd's path *relative to
the root* **equals `match_subpath` or is contained under it as whole segments** —
computed with `Path.relative_to`, exactly as the existing root match avoids the same
bug class. So `match_subpath = "apps/web"` matches `apps/web` and `apps/web/ui` but
**never** `apps/web-old`. Among matching records, the longest `match_subpath` wins.

**D31 — Migration mechanics (uses ADR-0015's reserved `MIGRATE` mode).** A new
`neurobase store migrate`:

- Opens a `MIGRATE` handle; refuses to run twice (writes a **migration marker**;
  a partially-migrated store makes normal commands refuse until migration
  completes — the partial-transaction detection ADR-0015 reserved the mode for).
- Takes a **full backup** first (reuse `core/backups.py`, spec §10).
- `--dry-run` prints the planned record/frontmatter changes and writes nothing.
- Preserves every current active fact as `status: active`, `scope: project`,
  conservative sensitivity; assigns each project record `profile = default_profile`,
  `privacy = "default"`; leaves derived indexes ([ADR-0018] / FTS) absent until
  rebuilt.
- **Rewrites the ledger exactly once, under the backup above** _(clarified in review —
  F2)._ Pre-schema-2 events have no `event_id` (today's `_append_ledger` writes only
  `at`/`slug`/`event`/`candidate_type`), so migration is the one place they get a
  deterministic ULID (derived from line order + content). This preserves every
  event's **order, content, and meaning** — none are dropped or reordered — but it is
  a genuine one-time rewrite, not a byte-for-byte copy. It is sanctioned precisely
  because migration is explicit and fully backed up; the append-only invariant (D29)
  governs *normal operation*, which this is not. The result is the hole-free ULID
  invariant D29 relies on.
- Old binary refuses schema 2 (already true); new binary refuses normal operation
  on a schema-1 store except to offer migration.

## Consequences

- **One migration, not several.** Egress policy (ADR-0017) and hook receipts
  (ADR-0018) add *fields and files* on top of this schema-2 shape but do not each
  bump the schema. Issue 3/6/10's structural needs are satisfied by this single
  `store.toml` schema-2 change.
- **ADR-0015 migration step 1 must build the profile qualifier in.** `open_store()`
  ships with the `profile` parameter from the first commit; the schema-1 chokepoint
  and the schema-2 records land close together but the *signature* is fixed here so
  it is never reworked. Everything ADR-0015 says about the handle being unavoidable
  (private constructor, CI AST check) applies unchanged.
- **`load_registry`'s return type changes** from `dict[str, list[str]]` to records —
  a breaking internal API touch across every caller, done under the same
  handle-conversion sweep ADR-0015 already schedules (its migration steps 2–3).
- **Config split is explicit.** *Per-project* policy lives in `registry.toml`
  records (neurobase-written); *global defaults* (`default_profile`,
  `default_privacy`) live in `config.toml` (hand-edited — [`core/config.py`](../../src/neurobase/core/config.py)
  keeps its "neurobase never writes this file" contract). ADR-0017 defines those
  default keys.
- **Spec appendix** updates: §10 gains the schema-2 store layout, the project-record
  table, the profile partition model, `match_subpath` resolution, and the explicit
  migration contract; the ULID/append-only/content-hash primitives are recorded as
  the on-disk invariants a future sync relies on. **This ADR proposes; the spec is
  the law** — fold in on implement.
- **Deferred deliberately:** actual multi-machine sync, teams, and the adapter SDK
  (§16.2) — all Issue-10 items the ratified decisions held until core is proven
  (§30). This ADR ships only the primitives that must exist *before* those, not the
  features.

## Alternatives considered

- **Physical store partitions per profile** (Issue 6 Solution B, physical form) —
  rejected: hard filesystem isolation, but multiple roots and handles, heavier
  cross-profile tooling, and it breaks ADR-0015's single-root handle. Logical
  partitions get the same isolation guarantee enforced in-API.
- **Separate schema bumps for profiles, then egress, then sync primitives** —
  rejected: three migrations over the same files, three backups, three windows for a
  half-migrated store. The fields are known now; migrate once.
- **Defer `match_subpath` and the sync primitives to "when needed"** (Issue 10
  defer) — rejected *for these two specifically*: both are cheap structural
  additions now and expensive retrofits later (ULIDs onto a live ledger; subpath
  onto a shipped resolver). The adapter SDK and real sync stay deferred; only the
  schema seams come early.
- **Skip ULIDs, key events by slug + timestamp** — rejected: timestamps collide at
  one-second resolution (the recommender already hit same-second backup-dir
  collisions, ADR history) and are not machine-unique. A ULID is the minimum stable
  identity a mergeable append-only log needs.
