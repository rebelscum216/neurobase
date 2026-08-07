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
(**how its body came to exist**) — replacing a model that conflates all three.
⚠️ The working note glossed the third axis as *who may change a record*. That gloss is
**not adopted**, because it contradicts [ADR-0027](0027-authored-raw-authority-field.md)
`D47` — "`authority` is a claim about provenance, not a grant of trust" — which is
`Accepted` and therefore immutable. Permission to change a record is carried by
`lifecycle` and by `pinned` (D52), never by `authority`. Verified: no
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

**D49 — every stored *record* carries three independent axes, and "record" is a closed
list.** `kind`, `lifecycle`, and `authority` are separate fields and independently
queryable; a record's `kind` does **not** determine its `authority`.

**The record universe is exactly:** curated facts, raws, recommender proposals, and
synthesized status nodes. **Explicitly excluded**, and not given axes by this ADR:
D27 project records (registry policy, not stored content), D29 ledger/journal events
(append-only event log), digest sidecars (derived cache), and the nested
`EvidenceRef` values inside a proposal's frontmatter (references *to* records, not
records — see D54). Anything outside that list is not addressed here and a later ADR
owns it; the accessor MUST NOT be extended to it by implication.

**An axis is RESOLVED, not necessarily STORED.** The accessor is the single place that
knows which axes are persisted frontmatter and which are computed from existing fields;
callers see a uniform resolved value either way. This matters because some axes already
have a source of truth that must not be duplicated — see D52 and D53. **Where an axis is
derivable from an existing field, it MUST be derived rather than written**, so a
migration cannot create two encodings of one fact that later disagree.

Readers MUST tolerate the absence of any axis, exactly as they already must for
`transcript_path` / `capture_version` / `authority` — `write_raw`'s contract
([`core/store.py:326-329`](../../src/neurobase/core/store.py)) already states this
requirement and ADR-0027 D45 restates it. ⚠️ **Tolerating absence is a parsing
posture, not a default**: what an absent axis *means* is D53's table, and every
included kind has an entry there.

📎 **Citation note.** ADR-0027 D45 cites this same contract as `core/store.py:325-328`.
That range is off by one at the origin — line 325 is blank and the operative clause
("must tolerate the absence of both keys") is line 329. ADR-0027 is `Accepted` and
immutable, so it cannot be corrected; this ADR cites the accurate range deliberately,
and the divergence is expected rather than a transcription error.

**D50 — the axes are consumed through one record abstraction, and the migration is not
complete until a *named* surface dispatches on a resolved axis.** This is the entire
reason the model rides schema 2 rather than accreting as frontmatter. A shared accessor
resolves a record's axes, and a converted surface MUST obtain them only from it.

⚠️ **Scope, stated precisely, because an earlier draft was vacuous.** This migration
does **not** convert every surface. "No surface may enumerate concrete directories or
concrete object types" is the *end state*, not this ADR's completion bar — and a bar of
"at least one surface reads through the abstraction" is satisfiable by a surface that
takes a body from the accessor and ignores all three axes, which is precisely the inert
outcome this decision exists to prevent.

**Acceptance criterion — all three, or the migration is not done:**

1. **The named first consumer is `doctor`.** It MUST report a per-`kind` breakdown of
   the store and MUST distinguish `authority = agent-authored` from `captured` in that
   report. `doctor` is chosen because ADR-0027 `D47` already names it a permitted
   consumer of `authority`, so this adds no new consumer class.
2. **It reads only through the accessor.** A structural test MUST fail if `doctor`
   reaches store paths or object types directly for this readout.
3. **It is axis-sensitive.** A behavioral test MUST fail if changing *only* a record's
   `authority` (or `kind`) leaves `doctor`'s output unchanged. A consumer that receives
   records and ignores the resolved axis does **not** satisfy this criterion.

**Permitted, tracked residue.** Every surface not converted here — search, graph, MCP,
the web UI, the recommender — MAY continue its current enumeration until its own ADR or
gap entry converts it. That residue MUST be recorded as a `known-gaps` entry when this
ADR is implemented, so "not yet converted" stays visible rather than becoming the silent
status quo.

**D51 — ADR-0027's raw `authority` vocabulary is contract; schema 2 extends it and
never respells it.** `captured` and `agent-authored` keep their spellings and
meanings, and **absence still means `captured`** (D45). Records of other kinds may
carry additional values. A tidier respelling is rejected: ADR-0027 is immutable, and
those values are already written to disk in the live store.

**The axis means what `D47` says it means, on every kind.** `authority` answers *how
this body came to exist* — it is a provenance claim, never a permission. It MUST NOT
raise or lower a record's weight in planning, ranking, recall, or proposal mining, and
that prohibition binds **every value on the axis, not only the two raw values ADR-0027
named**. Extending the vocabulary to a new kind therefore cannot smuggle in a policy
meaning: a value that would change how a record is *treated* rather than describe how it
was *made* does not belong on this axis.

**Permitted consumers, scoped.** `D47` restricts consumption to surfaces that would
otherwise misdescribe what they hold — the fold journal, `doctor`, and any UI naming the
rung it is showing. That restriction is carried forward unchanged and applies to the
shared axis as a whole. D50's named consumer is `doctor`, already on that list, so this
migration adds no consumer `D47` did not already permit.

**D52 — `pinned` is retained, not replaced by `authority`, and it stays DERIVED.** It
expresses consent, not a content type, and — per D51 — `authority` is a provenance claim
that cannot carry a protection guarantee at all. The system may flag a protected record
and propose a replacement; it must not silently overwrite one.

⚠️ **`pinned` is not a stored field and this ADR does not make it one.** In schema 1 the
sole source of truth is **provenance containing `user-directed`**:
[`curator/engine.py:203-210`](../../src/neurobase/curator/engine.py) computes the pinned
slug set from it, [`core/graph.py:68,296`](../../src/neurobase/core/graph.py) derives
`pinned` from the same sentinel, and the spec appendix states it directly ("a curated
fact whose `provenance` includes `user-directed` … is *pinned*"). The `"pinned": true`
key that appears in the curator **plan payload**
([`curator/engine.py:118`](../../src/neurobase/curator/engine.py)) is constructed
in-memory for the brain; it is never written to frontmatter. Schema 2 keeps it derived,
so there is exactly one source of truth and no disagreement case to resolve. Retire the
derived boolean only once the contradiction-and-correction workflow ships.

**D53 — migration defaults, under D31's backup — complete over D49's record list.**
No record is dropped, reordered, or reinterpreted.

**Rows are evaluated top to bottom; the first match wins.**

| Record | `kind` | `lifecycle` | `authority` |
|---|---|---|---|
| Curated fact, `status: tombstoned` | `curated` | `tombstoned` | per the next two rows |
| Curated fact, `provenance` ∋ `user-directed` | `curated` | `protected` | `user-directed` |
| Curated fact, otherwise | `curated` | `replaceable` | `curator-derived` |
| Raw capture | `raw` | `consumable` | **stored** — whatever ADR-0027 wrote, absence included |
| Recommender proposal | `proposal` | `decidable` | `curator-derived` |
| Synthesized status node | `node` | `derived` | `curator-derived` |

**Every value above is DERIVED except the raw's `authority`, which ADR-0027 already
persists.** `kind` follows from the store collection a record lives in; `lifecycle` from
`status` and `provenance`; a curated fact's `authority` from the same `user-directed`
sentinel that D52's pin test uses. Nothing else is written.

⛔ **The pin predicate is `provenance` containing `user-directed` — NOT a `pinned`
field.** An earlier draft of this decision said "where `pinned` is true". No such key is
persisted (D52), so an implementation following that wording literally would read every
user-directed fact as unpinned and migrate it to `curator-derived` — **destroying the
consent signal at the one boundary this decision promises to preserve it.** The
predicate above is the same one `curator/engine.py:203-210` already uses.

⛔ **Deriving is not a stylistic choice here — writing these axes would recreate the
defect above, one axis over.** A stored `lifecycle: protected` beside a `pinned` derived
from `provenance` is two encodings of one fact: the moment a later write adds
`user-directed` to a fact's provenance without rewriting `lifecycle`, they disagree, and
the consent signal is again at the mercy of whichever encoding the reader happened to
consult. The same argument applies to a written `authority: user-directed`, which is
derivable from the identical sentinel. Raws are the sole exception, and only because
ADR-0027 already persists that field with no derivable source behind it.

⭐ **Consequence for D31's migration step: for the three axes it is a NO-OP.** Nothing is
rewritten and no backup is strictly required for this part — D31's backup still applies
to the rest of the schema-2 migration. This is the strongest available answer to the
falsified migration-economy argument in the Context: the migration cost is not merely
low, it is **zero by construction**, and the entire cost of this ADR is the accessor plus
`doctor`'s conversion (D50). If an implementation finds itself writing `kind` or
`lifecycle` to disk, it has diverged from this decision.

**`lifecycle` vocabulary** (complete, closed): `protected` (may be flagged and a
replacement proposed, never silently overwritten), `replaceable` (the curator may
reword, supersede, or tombstone), `tombstoned` (retained, not live), `consumable`
(rewritable by the owning scribe until `consumed: true`, per `write_raw`'s existing
mutability rule), `decidable` (moves by explicit accept/reject/reopen), `derived`
(regenerated wholesale from its inputs). A new value requires an ADR.

**Accessor fixtures are an acceptance criterion**, one per row above, plus: a
`user-directed` fact MUST stay pinned and MUST *resolve* to `authority = user-directed`;
an ordinary curator fact MUST retain its current mutability; a tombstoned fact MUST NOT
be revived; and a raw with no `authority` key MUST still have none on disk afterward and
MUST resolve to `captured` (D45). Each fixture asserts on the **resolved** value and on
the **bytes on disk** — the second half is what catches an implementation that quietly
starts persisting a derived axis.

**D54 — the `kind` vocabulary is opened only as far as Neurobase actually stores, and it
adopts the spellings already on disk.** Named now: **`curated`** (a curated fact),
**`raw`** (a raw capture, including an authored session digest), **`proposal`** (a
recommender proposal), **`node`** (a synthesized status node).

⛔ **These are not new words, and that is the point — D51's rule binds here too.** An
earlier draft named them `fact` / `session` / `proposal` / `node`, which would have
respelled two vocabularies that are already persisted and already public:
`EvidenceRef.kind` is `curated | raw | proposal`
([`recommender/corpus.py:87-91`](../../src/neurobase/recommender/corpus.py)) and is
stored inside proposal frontmatter, and `SearchHit.kind` is `curated | node`
([`core/search.py:31`](../../src/neurobase/core/search.py)) and is returned across the
MCP boundary. Respelling them would have broken persisted evidence lists and the MCP
result contract to buy tidiness — exactly what D51 refuses to do for `authority`. The
adopted list is the **union of the two live vocabularies**, so it needs no compatibility
mapping and no API break: existing proposal evidence keeps loading and MCP `kind` output
is unchanged.

`EvidenceRef` values remain **references to** records, not records (D49); they share the
spellings deliberately, and a reference is resolved to a record before its axes are read.

**Mounting an external vault as a live record source is outside this model.** This ADR
decides that here, publicly, rather than resting it on an unpublished note: no provider
is defined, none is required by any decision above, and kinds that would only exist to
describe such a source — `person`, `goal`, and similar — are **not adopted**. The
decision forecloses nothing: an adapter remains additive, and the ADR that introduces one
owns its kinds and their axes. A new kind is added when a record of that kind exists and
something reads it, not before.

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
  …) — rejected: those kinds exist only to describe a mounted external vault, which
  **D54 places outside this model**. Adopting them would bake a data model for records
  Neurobase does not hold.
- **Coin fresh `kind` spellings** (`fact` / `session` / …) for tidiness — rejected for
  the same reason D51 rejects respelling `authority`: `curated`, `raw` and `proposal`
  are already persisted in proposal evidence and `curated` / `node` already cross the
  MCP boundary. Consistency bought with a data migration and an API break is not a
  saving.
- **Edit ADR-0016 in place** — not available: an ADR is immutable once `Accepted`,
  and a status flip to `Superseded by NNNN` is the only sanctioned edit
  ([ADR README](README.md)).
