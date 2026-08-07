---
slug: adr-0028-typed-record-model
status: approved
author: claude
reviewer: codex
branch: docs/adr-0028-typed-record-model
diff: git diff main...HEAD
created: 2026-08-07
rounds: 4
last_reviewed: f736df459b67db3dab3197bf569c49f62dce04e0
---

> **Status note (2026-08-07).** The Brief below opened the relay and describes the ADR
> at `8c0c946`. Four rounds ran and the branch is three fix commits further on. Two of
> the decisions the Brief hands over — D53's migration predicate and D54's `kind`
> spellings — were **wrong as handed over**, and a third (D50's acceptance criterion)
> was satisfiable vacuously. The Brief is left as written, because it is the record of
> what was actually handed over; the outcome is at
> [Reviewer findings](#reviewer-findings--reviewer--codex).

# Review: ADR-0028 — the typed record model rides the schema-2 migration

## Brief  _(Author — Claude)_

**Intent.** ADR-0016 accepted a schema-2 migration on 2026-07-21 and was never
implemented. ADR-0028 proposes superseding it with a revision that carries D27–D31
forward **with their numbers and meaning intact** — ADR-0017 cites `D27` and ADR-0018
cites `D29` three times, so renumbering would strand both — and adds a typed record
model as D49–D54. ADRs are immutable once `Accepted`, so "revise" always meant
*supersede*; 0028 is `Proposed` and 0016 stays `Accepted` until this relay closes.

**Scope.** Branch `docs/adr-0028-typed-record-model`, `git diff main...HEAD`. At
hand-over a single commit `8c0c946` (+184/-1): the new ADR plus two index rows in
`docs/adr/README.md`.

**Focus areas.** The load-bearing claim is that `authority` is **write-only** — three
write sites, zero read sites — which *falsifies* §8.4's migration-economy argument while
leaving its shared-abstraction argument standing. The ADR rests on the second alone and
says so. Most wanted: whether D27–D31 survive semantically intact, and whether D50's
acceptance criterion actually prevents this ADR reproducing ADR-0027's outcome (a
correct field nothing consumes).

**Known risks / tradeoffs.** The ADR was drafted against the session's own "not yet"
recommendation, on Andrew's call. Its provenance is an unpublished private note, so the
inline code citations are the only inspectable evidence — a deliberate constraint.

**How to verify.** `make ci` (docs-only, but the gate is the gate). Every inline
`file.py:NNN` citation should be re-derived at the reviewed SHA rather than trusted.

**Out of scope.** Implementing schema 2 — `STORE_SCHEMA_VERSION` is still 1 by design.
Prose style. The unpublished working note.

---

## Reviewer findings  _(Reviewer — Codex)_

Four rounds, `gpt-5.6-sol` at effort `high`, each in a detached-HEAD worktree pinned to
the exact SHA. **7 findings — 6 × P1, 1 × P2 — all fixed, none disputed, none withdrawn.**
r1 `8c0c946` (BLOCKED, 5) → r2 `76078d4` (BLOCKED, 2 new) → r3 `82fc1f6` (BLOCKED, 1
partially fixed) → r4 `f736df4` (**APPROVED**, 0 open).

### F1 — `authority` had two incompatible meanings  (`P1-CORRECTNESS-001`)
- **severity:** blocker · **location:** `docs/adr/0028-…md:22` vs `docs/adr/0027-…md:133`
- **issue:** the ADR glossed the axis as *who may change a record*, while ADR-0027 `D47`
  states in bold that `authority` is "a claim about provenance, **not a grant of trust**".
  D51 simultaneously promised to preserve ADR-0027's values *and meanings*.
- **resolution:** resolved — Andrew chose provenance/authorship. Mutation permission moved
  to `lifecycle`/`pinned`; D51 now also scopes D47's permitted-consumer restriction to
  **every** value on the axis, not only the two raw values it named.

### F2 — D53 keyed the migration on a field that is not persisted  (`P1-DATA-INTEGRITY-002`)
- **severity:** blocker · **location:** `docs/adr/0028-…md:134`, `curator/engine.py:203-210`
- **issue:** D53 assigned `authority = user-directed` "where `pinned` is true", but no
  `pinned` key is ever written. It is derived from `provenance` containing `user-directed`;
  the `"pinned": true` in `engine.py:118` is the in-memory plan payload. An implementation
  following the wording literally would read **every user-directed fact as unpinned** and
  migrate it to `curator-derived` — destroying the consent signal at the exact boundary
  D53 promises to preserve it.
- **resolution:** resolved — D53 keys on the live predicate; D52 states `pinned` stays
  derived, so there is one source of truth and no disagreement case.

### F3 — record universe incomplete, and D54 respelled live vocabularies  (`P1-CORRECTNESS-003`)
- **severity:** blocker · **location:** `recommender/corpus.py:87-91`, `core/search.py:31`
- **issue:** D49 claimed every record carries all three axes while D53 defaulted only
  facts, and "record" was never bounded. Worse, D54 coined `fact`/`session` while
  `EvidenceRef.kind` is `curated|raw|proposal` (persisted inside proposal frontmatter) and
  `SearchHit.kind` is `curated|node` (crosses the MCP boundary) — **respelling live on-disk
  values one decision after D51 refuses to do exactly that for `authority`.**
- **resolution:** resolved — D49 closes the record list and names its exclusions; D54 adopts
  the union of the existing vocabularies, so no compatibility mapping and no API break.

### F4 — D50's acceptance criterion was vacuous  (`P1-CORRECTNESS-004`)
- **severity:** blocker · **location:** `docs/adr/0028-…md:112`
- **issue:** D50 said "every surface dispatches" but required only that "**at least one**
  surface" read through the abstraction — and "read through" needn't read an *axis*. Both
  readings satisfy the literal criterion while reproducing the inert-field outcome D50
  exists to prevent.
- **resolution:** resolved — names `doctor` as first consumer (already a D47-permitted one),
  requires a structural test that fails if it bypasses the accessor and an axis-sensitivity
  test that fails if changing only `authority`/`kind` leaves output unchanged, and permits
  unconverted surfaces only as a filed `known-gaps` entry.

### F5 — D54 rested on an unpublished decision as settled fact  (`P2-DOCS-PLAN-ACCURACY-005`)
- **severity:** minor · **location:** `docs/adr/0028-…md:7`, `:139`
- **issue:** the ADR promises inline code is the inspectable evidence for every claim, then
  rested D54's narrowness on private "decision 7". Code can show no provider exists; it
  cannot show the option was ratified closed.
- **resolution:** resolved — D54 decides the external-vault question publicly, on its own
  authority. The note is provenance, not proof.

### F6 — seed-imported facts resolved to a false authority  (`P1-CORRECTNESS-006`)
- **severity:** blocker · **location:** `core/store.py:474-477`, `recommender/seed.py:329-335`
- **issue:** raised against the round-1 *fix*. "Curated fact, otherwise → `curator-derived`"
  resolves a **seed-imported** fact to curator authorship — recreating at the axis layer the
  precise misattribution `agent_last` exists to prevent ("so this field never silently claims
  the curator touched a fact it never saw"). The obvious repair is also wrong: `store.py:470`
  **merges** provenance, so a `seed:*` entry survives a later curator rewrite and cannot
  signal *current* authorship.
- **resolution:** resolved — facts resolve by an ordered rule on `provenance` + `agent_last`,
  with an explicit prohibition on deriving authorship from `seed:*`.

### F7 — proposal authority is not derivable from the ledger  (`P1-CORRECTNESS-007`)
- **severity:** blocker · **location:** `recommender/proposals.py:405`, `:541-542`, `:280-282`,
  `tests/test_metrics.py:186-195`
- **issue:** raised r2 (a per-kind constant cannot express proposal authorship: bodies are
  miner-generated, and `recommend edit` replaces them with user text), **re-raised r3** after
  the fix addressed only ledger *parse tolerance*. The real defect is **evidence-to-body
  binding**: both body-producing paths write the document first and append the event second,
  so a failure between them leaves a valid, readable, *wrong* ledger in **both** directions —
  and `_edited_since_last_write` compares timestamps with a strict `>`, so equal timestamps
  resolve "not edited", a state `tests/test_metrics.py:186-195` **already reaches**, turning a
  user-edited body into a confident `recommender-mined`.
- **resolution:** resolved — this **tripped the relay's convergence guard**; the loop stopped
  for Andrew's decision rather than a third attempt at the derivation. Andrew chose to persist
  it: proposal `authority` is stamped in the same `store.write_doc` call that writes the body
  (verified atomic — temp file + rename, `core/store.py:141-145`). Lifecycle-only verbs carry
  the value through untouched; pre-migration proposals resolve ABSENT and are **not**
  back-filled. The migration therefore remains a no-op on all three axes.

**Verdict:** approve — _round 4, 0 open findings; all seven fixed, none disputed._

---

## What the relay changed beyond the findings

Three defects were caught by the author *while fixing*, each the same shape as the finding
above it — the fix becoming the next defect:

1. The first pass at F2 **recreated F2 one axis over**: a stored `lifecycle: protected`
   beside a `pinned` derived from `provenance` is two encodings of one fact. This produced
   D49's rule that a derivable axis must be **derived, never written** — which then cascaded,
   making `kind`, `lifecycle` and curated-fact `authority` all resolved and the migration a
   no-op.
2. The proposal rule needed the *ordering* of `_edited_since_last_write`, not "an `edited`
   event exists" — a miner refresh appends a new `proposed` after rewriting the body.
3. That predicate must not simply be **called**: it returns `False` for a missing ledger,
   correct for the miner ("may I safely refresh?") but here turning "I cannot tell" into the
   positive claim `recommender-mined`. Same evidence, different question, different safe
   answer.

⭐ **The durable lesson, now stated in D49 rather than left in this baton:** *derivable*
means evidence provably **bound** to the value it describes — same record, same write — not
evidence that merely correlates. Where the only candidate evidence is produced by a separate
operation, deriving manufactures confident falsehoods exactly where storing manufactures
none. F6 and F7 are both instances; so is the tie-break.

⭐ **And the shape all three shared:** D53's table enumerated its **rows** (kinds) but not its
**cells** — `authority` has authorship sub-cases *within* a kind. A table is only as good as
its narrowest column.
