# ADR-0028: Store schema 2, revised — the typed record model rides the same migration

- **Status:** Proposed
- **Date:** 2026-08-06
- **Supersedes:** [ADR-0016](0016-store-schema-2-project-records-profiles.md) on
  acceptance (see **Sequencing** — 0016 stays `Accepted` until then)
- **Resolves:** decision 3 of the memory-layering working note (2026-08-01, §6.3 /
  §8.4 / §10.2). ⚠️ **That note is unpublished** — a private lab notebook, not
  committed to this repo — so its section references are provenance for the author
  and are **not inspectable by a reader of the public repo**. The inspectable
  evidence for every claim below is the code cited inline.

## Context

[ADR-0016](0016-store-schema-2-project-records-profiles.md) accepted a schema-2
migration — project records, logical profiles, ULID event IDs, content hashes, and
monorepo `match_subpath` — on 2026-07-21. It has **not been implemented**:
`STORE_SCHEMA_VERSION = 1` ([`core/store.py:29`](../../src/neurobase/core/store.py)),
the live store's `store.toml` reads `schema = 1`, and neither `profile` nor
`match_subpath` exists in [`core/projects.py`](../../src/neurobase/core/projects.py).

Separately, the working note proposes typing stored records on **three independent
axes** — `kind` (what a record is), `lifecycle` (how it changes), and `authority`
(who may change it) — replacing a model that conflates all three. Verified: no
`kind`, `lifecycle`, or generic record abstraction exists in
[`core/store.py`](../../src/neurobase/core/store.py) today. Curated facts carry
`name`, `status`, `supersedes`, `provenance`, `agent_last`, `updated_at` (plus the
seed importer's `source_path` / `source_digest`), and `status` is only
`active|tombstoned`.

The question was whether to fold the second into the first before the first is
built. Two arguments were offered for doing so, and **they are not equally strong**.
This ADR is explicit about which one it rests on, because the other has since been
falsified:

- **Migration economy — falsified.** [ADR-0027](0027-authored-raw-authority-field.md)
  landed the `authority` axis on a *raw*, on schema 1, at zero migration cost, and
  fixed the rule (D46) that a schema-1→2 migration **carries it forward unchanged**
  with no migration step depending on its value. Measured on this commit:
  `authority` has three write sites
  ([`core/store.py:316`](../../src/neurobase/core/store.py),
  [`:373-374`](../../src/neurobase/core/store.py);
  [`core/store_handle.py:180`](../../src/neurobase/core/store_handle.py),
  [`:193`](../../src/neurobase/core/store_handle.py);
  [`cli/__init__.py:414`](../../src/neurobase/cli/__init__.py)) and **no read
  sites** — `curator/` never consults it. There is no migration to save and no
  reader to touch twice. An argument to bundle *for migration economy* would be
  optimizing a cost already measured at zero.
- **Shared abstraction — stands, and is the reason for this ADR.** The other
  argument is untouched by the above: do not add an axis as bare frontmatter and
  then teach every surface to branch on it, because search, graph, UI, MCP, and the
  recommender each still enumerate **concrete directories and concrete object
  types**. A per-document key is cheap *precisely while nothing reads it*. The first
  surface that must dispatch on an axis is where a shared record abstraction has to
  exist, and creating one is a reader-side change across every surface. That cost is
  not reduced by waiting, and it grows with each surface added meanwhile.

This ADR therefore folds the record model into schema 2 **for the abstraction, not
for the migration**, and carries ADR-0016's decisions forward unchanged.

## Sequencing

This ADR is `Proposed`. **ADR-0016 stays `Accepted` until this one is accepted**; on
acceptance, 0016's status becomes `Superseded by 0028` — the one edit the
[ADR README](README.md) sanctions on an accepted ADR. 0016 is retained in full as
the historical record and keeps its rationale and alternatives.

**D27–D31 keep their numbers and their meaning here**, because they are cited by
number elsewhere: [ADR-0017](0017-egress-policy-gate.md) declares it builds on the
project records and cites `D27`; [ADR-0018](0018-hook-receipts-degraded-state.md)
cites `D29` three times for the ULID and append-only conventions. Renumbering them
would strand both.

## Decision

### Carried forward from ADR-0016, unchanged in meaning

Bump `STORE_SCHEMA_VERSION` to **2**; the migration is explicit, backed up, and
forward-only. Consult ADR-0016 for the full rationale and alternatives behind each.

- **D27 — `registry.toml` entries become project *records*.** Each entry gains
  policy fields (`profile`, `match_subpath`, `privacy`, `allowed_brains`,
  `allow_transcript_distill`, `allow_cross_agent_backend`,
  `allow_cross_project_mining`); `roots` is preserved. `load_registry()` returns a
  typed `ProjectRecord`. A schema-1 registry read under a schema-2 binary is a
  migration trigger, never silently coerced. Unknown policy values fail closed.
- **D28 — profiles are logical partitions under one root; the handle is
  profile-qualified.** `open_store(root, mode, profile=None) -> StoreHandle`;
  `StoreHandle` carries `profile`. Cross-profile access is a different handle, not a
  caller-assembled path. The handle is scoped to a *profile*, not a project, so
  per-project policy is **not** read off the handle.
- **D29 — forward-compatible identifiers and append-only semantics.** Stable ULID
  `event_id` on every appended event; append-only generalized to a shared `core`
  helper; content hashes on curated facts and nodes. No sync code ships.
- **D30 — monorepo subproject resolution (`match_subpath`).** Path-segment bounded,
  never a string prefix; longest match wins; empty matches the whole repo.
- **D31 — migration mechanics.** `neurobase store migrate` over a `MIGRATE` handle:
  migration marker, refuses to run twice, full backup first, `--dry-run` writes
  nothing, and the single sanctioned one-time ledger rewrite that assigns ULIDs to
  pre-schema-2 events under that backup.

### New — the typed record model

**D49 — every stored record carries three independent axes.** `kind`, `lifecycle`,
and `authority` are separate fields and independently queryable; a record's `kind`
does **not** determine its `authority`. Readers MUST tolerate the absence of any
axis, exactly as they already must for `transcript_path` / `capture_version` /
`authority` — `write_raw`'s contract
([`core/store.py:326-329`](../../src/neurobase/core/store.py)) already states this
requirement and ADR-0027 D45 restates it. Migration defaults are D53.

**D50 — the axes are consumed through one record abstraction, never by per-surface
branching, and the migration is not complete until a surface reads through it.**
This is the entire reason the model rides schema 2 rather than accreting as
frontmatter. Once the axes exist, no surface may enumerate concrete directories or
concrete object types to decide behavior; a shared accessor resolves a record's axes
and every surface dispatches on that. **Acceptance criterion:** at least one surface
must be converted to read through the abstraction within this migration. Without
it, this ADR reproduces ADR-0027's outcome — a correct field nothing consumes — at
considerably greater cost.

**D51 — ADR-0027's raw `authority` vocabulary is contract; schema 2 extends it and
never respells it.** `captured` and `agent-authored` keep their spellings and
meanings, and **absence still means `captured`** (D45). Records of other kinds may
carry additional values. A tidier respelling is rejected: ADR-0027 is immutable, and
those values are already written to disk in the live store.

**D52 — `pinned` is retained, not replaced by `authority`.** It expresses consent,
not a content type, and the contradiction-and-correction workflow that would let
`authority` carry that guarantee does not exist. The system may flag a protected
record and propose a replacement; it must not silently overwrite one. Retire the
boolean only once that workflow ships.

**D53 — migration defaults, under D31's backup.** Existing curated facts become
`kind = fact`, `lifecycle = replaceable`, and `authority = user-directed` where
`pinned` is true, else `curator-derived`. Raws keep whatever ADR-0027 wrote, absence
included. No record is dropped, reordered, or reinterpreted.

**D54 — the `kind` vocabulary is opened only as far as Neurobase actually stores.**
Named now: `fact` (a curated fact), `session` (an authored digest or raw capture),
`proposal` (a recommender proposal), `node` (a synthesized status node). The working
note's longer list was drafted while mounting an external vault as a live source was
still under consideration; that option is **closed** (decision 7, 2026-08-06 — no
provider, borrow the shapes only), so kinds belonging to that model — `person`,
`goal`, and similar — are deliberately **not** adopted. A new kind is added when a
record of that kind exists and something reads it, not before.

## Consequences

- **Still one migration, not several.** ADR-0016's consequence holds unchanged:
  egress policy (ADR-0017) and hook receipts (ADR-0018) add fields and files on top
  of this shape without each bumping the schema. The record model now rides the same
  bump.
- **The reader-side sweep is the real cost, and it is now explicit.** D50 converts
  what was an unbounded "teach every surface to branch" into one abstraction plus a
  conversion, with a named acceptance criterion. This is the work; the schema bump
  is not.
- **`load_registry`'s return type still changes** from `dict[str, list[str]]` to
  records — the same breaking internal API touch ADR-0016 scheduled.
- **Citations into ADR-0016 keep resolving.** D27–D31 are stable here; ADR-0017 and
  ADR-0018 need no edit.
- **Spec appendix** updates as ADR-0016 specified, plus the record model: the three
  axes, their absence semantics, the `kind` vocabulary, and D53's defaults. **This
  ADR proposes; the spec is the law** — fold in on implement.
- **Deferred deliberately, unchanged from ADR-0016:** multi-machine sync, teams, and
  the adapter SDK. Added here: any `kind` without a real record and a real reader.

## Alternatives considered

- **Not yet — keep evolving per-document on schema 1, as ADR-0027 did.** The
  measured position: migration cost is zero, so bundling buys nothing on that axis,
  and the revisit trigger would be the first surface needing to dispatch. Rejected
  (Andrew, 2026-08-06) because it optimizes the cost already at zero while deferring
  the reader-side abstraction, which is the expensive half and grows with each
  surface added meanwhile.
- **Respell ADR-0027's `authority` values into a single tidy vocabulary** —
  rejected: it contradicts an immutable ADR and would need a migration of live
  on-disk data to buy consistency nothing has yet asked for.
- **Adopt the working note's full `kind` vocabulary** (`person`, `goal`, `review`,
  …) — rejected: those kinds came from the external-vault model that decision 7
  closed. Adopting them would bake a data model for records Neurobase does not hold.
- **Edit ADR-0016 in place** — not available: an ADR is immutable once `Accepted`,
  and a status flip to `Superseded by NNNN` is the only sanctioned edit
  ([ADR README](README.md)).
