# ADR-0022: Curator fold journal + `from_raw` validation (provenance hardening, Slice B)

- **Status:** Proposed
- **Date:** 2026-07-24 (decision first authored 2026-07-19)
- **Resolves:** session→fact provenance hardening, write side ("Slice B")
- **Supersedes:** none

> **Provenance of this ADR.** The decision below was authored on 2026-07-19 and
> reviewed over six Codex rounds on `feat/provenance-slice-b`, which numbered it
> ADR-0016 against the then-current `main`. That branch was never pushed, and
> `main` subsequently took 0016–0021 *and* migrated `curator/engine.py` onto the
> `StoreHandle` chokepoint (ADR-0015) — rewriting the same functions B1/B2
> rewrite. The implementation was therefore **re-derived from this decision** on
> current `main` rather than merged, and the earlier approval does not carry: it
> was given against the old `store.*`/`root` API, so this lands as new code
> awaiting its own review. The decision itself is unchanged.

## Context

The planned memory graph needs **session→fact edges** — which conversations fed
which memory. A subsystem review across curator, store, brain, and recommender
found that data largely exists already: every curated fact carries
`provenance: ["raw/<file>", …]` (merged order-preserving across upserts), every
raw carries its `session_id`/`agent`/`captured_at`, and raw files are never
deleted by any pipeline path — so session→fact is one deterministic hop today.

But three gaps make that record untrustworthy or perishable, and none is in
`known-gaps.md`:

1. **Trust.** `from_raw` is LLM-asserted and never validated against the batch.
   A hallucinated filename — commonly an echo of an old raw basename sitting in a
   fact body's `## Lineage` block that the model sees — lands in the permanent
   `provenance` record, the exact channel the recommender ranker treats as
   scoring ground truth (ADR-0007 D21).
2. **Durability.** Lineage thins at the system's central operation: when facts
   fold, a superseded fact's edges survive only the 14-day tombstone grace, and
   `supersedes` is overwritten (not merged) on re-upsert, so chains are
   single-generation.
3. **Audit.** `.curator-log.jsonl` records per-pass *summaries* (counts, status,
   timestamp) but no per-pass raw→fact identities. Which raws fed which facts in
   a given pass is recorded nowhere.

This ADR is the write-side half (Slice B). The read service (`core/graph.py`)
and the graph UI are Slice A and Phase G, sequenced after.

## Decision

Two deterministic changes to `curator/engine.py` — the LLM response schema is
byte-for-byte unchanged (D9 / `parse_plan_json` / ADR-0002 all untouched) — plus
a supporting constant move.

**B1 — validate `from_raw` against the batch.** `_apply_upserts` gains a
`batch: set[str]` parameter (the raw filenames actually shown to the model this
batch — with D22 batching the model only ever sees the current batch's raws, so
this is the tightest honest set). Provenance is built from `from_raw ∩ batch`;
names outside the batch are dropped before they reach `provenance` and counted.
One sentence is added to `PLAN_SYSTEM` ("using only filenames present in this
request's raw_captures") — §2.1 permits prompt-text changes; the deterministic
filter is the enforcement, not the prompt. This **is** a spec §2 step-4 MUST edit
(step 4 previously fixed `provenance = ["raw/"+name for name in from_raw]` with
no validation). The drop count surfaces as `dropped_from_raw` on the committed
summary.

Every current `FakeBrain` plan fixture cites raw filenames it actually writes
into its tmp store, so no existing test changes behavior — the
hallucinated-`from_raw` case is a new adversarial test, not a retrofit.

**B2 — the fold journal.** `_log_pass` gains a `fold: dict | None` keyword —
**never** a new key on the *summary* dict, which the CLI prints as JSON and which
must not carry raw `session_id`s (loopback-only sensitivity, §2). When present it
rides the journal record alongside the summary keys:

```json
"fold": {
  "v": 1,
  "consumed": [{"file": "…", "session_id": "…", "agent": "…", "captured_at": "…"}],
  "edges": {"<fact-slug>": ["<validated raw filename>", …]},
  "superseded": [{"slug": "old-fact", "by": "new-fact"}],
  "tombstoned": ["dead-fact"]
}
```

- `consumed` embeds full session identity **at consumption time**, so a journal
  edge outlives any future raw-retention policy or a fresh clone with a
  gitignored `raw/`.
- `edges` is the validated `from_raw ∩ batch` map; an empty list means "upserted
  this pass, unattributed" (a graph orphan). A raw in `consumed` absent from
  every `edges` list fed no fact this pass.
- `superseded`/`tombstoned` are recorded **only where the soft-delete actually
  applied and the slug is still removed at pass end** — a pinned target, or a slug
  re-upserted the same pass, is not tombstoned and must not be journaled as
  removed. The per-batch skip handles a re-upsert **within** the batch; a
  **cross-batch** resurrection (D22: an earlier batch removes a slug, a later
  batch re-upserts it) is caught by a pass-end reconciliation against the active
  set — the false journal entry is dropped and its count decremented (keeping
  count == fold list length). This is the only record that survives
  multi-generation supersession and tombstone pruning.

**Store placement rule — resolution, not atomicity, and no auto-deletion.**
The reconciliation above keeps the *journal* honest. The related *store*
question — an active fact with a same-slug tombstone beside it — is settled by a
read rule rather than by cleanup code. Three review rounds walked this back to
what a filesystem can actually provide:

1. **Ordered so a single-process interruption does not lose a fact.** Each
   transition writes the new location before removing the old, so an interrupted
   *sequential* move strands a tombstone beside an active fact rather than losing
   it. This is scoped deliberately: it is **not** a claim about concurrent
   processes, which need the ownership boundary in G4.
2. **A deterministic resolution rule.** `curated/` is authoritative: if both
   exist the slug is active. Every current reader satisfies this for free
   (`list_curated` reads only `curated/`; the recommender's evidence resolver
   checks `curated/` before `.tombstones/`). A `.tombstones/` reader MUST exclude
   slugs present in `curated/` — a **binding constraint on the Slice A graph
   reader**, and the actual fix for the trap that motivated this work.
3. **No unowned deletion of `.tombstones/<slug>`.** Two revisions of this ADR got
   this wrong in the same way and both were caught by review probes: first an
   upsert-time clear plus a pass-start sweep, then a failed soft-delete rolling
   its own tombstone back. Both delete a **shared path** that a racing
   soft-delete can atomically replace, so each could destroy another operation's
   in-flight tombstone and let the racing delete remove the active file — losing
   the fact, a strictly worse outcome than the residue being cleaned. The rule
   that survives: **no write path deletes a tombstone.** `soft_delete_curated`
   does not undo itself; `upsert_curated` does not clear. Residue is invisible to
   readers (rule 2). Neither age nor a delay substitutes for ownership — age
   describes the contents *inspected*, not the file version later unlinked.

Explicitly out of scope, recorded in `known-gaps.md` **G4**:

- **A project-level store lock.** The prerequisite for *any* safe reclamation. It
  is a pre-existing gap affecting concurrent curate passes generally, not this
  feature, and needs its own ADR (boundary, all mutating entry points, a 3-OS
  mechanism, stale-lock policy).
- **`prune_tombstones`' TOCTOU.** It reads `tombstoned_at` then unlinks the path,
  so a soft-delete replacing that path in between can have its fresh tombstone
  pruned. Pre-existing and orthogonal to this ADR; closing it needs the same
  ownership boundary. It is recorded rather than papered over — an earlier draft
  of this ADR wrongly cited prune's age gate as race-proof.
- **Full crash-atomicity** (journalled/two-phase writes) — disproportionate for a
  local markdown store, and it does not address the concurrency gap that actually
  bites here.

A resurrection stays a *fresh* fact regardless: `upsert_curated` reads prior
state only from the curated path, so a tombstone's provenance/supersedes are
deliberately not inherited.

**Fold gating — one refinement over the plan's literal text.** The plan wrote
"ok/partial → fold present; error/noop → no fold." That conflated the two error
paths. The gate implemented here is **"a batch committed"** (`batch_count > 0`,
not a dry run) — the *same* condition that already gates steps 7–8 (D22: derived
state must not lag committed facts). This subsumes ok/partial **and the
after-commit error path** (a later batch failed after ≥1 committed): those
committed raws *are* consumed, and dropping their fold would erase exactly the
durable session identity the journal exists to provide. It excludes the
first-batch abort (`batch_count == 0`, nothing committed) and noop (early return)
— identical outcomes to the plan's intent for those. On the after-commit error
path the fold reflects only the committed batches; the failed batch's raws are
unconsumed and appear nowhere in it.

**Supporting move.** `CURATOR_LOG` moves to `core.store` (the canonical owner),
aliased from `curator.engine` for back-compat, so a core reader
(`core/graph.py`, Slice A) shares the name without a cross-layer format
dependency.

**No `STORE_SCHEMA_VERSION` bump (D11).** The fold is an additive JSONL field on
a derived-state log, and B1 tightens a write without changing on-disk fact
format. Older binaries ignore an unknown journal key; the D11 guard exists for
incompatible *store* schema, not log enrichment — the same reasoning as the seed
importer's `extra_frontmatter` and ADR-0014's additive raw keys. Recorded
explicitly because G1 shows the schema guard is contentious.

## Consequences

- **The journal may have gaps** (a crash between `mark_consumed` and the log
  append). The invariant is stated and tested: **the frontmatter ∪ journal union
  is the contract.** Frontmatter (`provenance`, `supersedes`) backstops exactly
  the fact-granular edges of a lost pass; the journal adds session identity,
  multi-generation supersession, and pre-prune history on top.
- The recommender ranker reads only `provenance` (frontmatter). B1 makes that
  channel strictly cleaner (no hallucinated raws); it never adds to it. Ranker
  breadth/recurrence semantics (D21) are unchanged.
- **Spec appendix** gains: §2 step 4 `from_raw` validation; the `dropped_from_raw`
  summary key (step 9); the core-owned `CURATOR_LOG`; and the fold-record contract
  under the pass-log clause. **This ADR is the proposal; that appendix is the
  law** — the appendix edits land with this ADR.
- **Sensitivity.** `fold.consumed` embeds full session ids; the journal is the
  same sensitivity class as `raw/`. Served only on the loopback UI; the seeded
  dev store stays BWE-internal.

## Alternatives considered

- **Heuristic backfill** (a Jaccard text-similarity pass writing `raw/` entries
  into `provenance` for orphan facts) — rejected. Backfilled entries would be
  frontmatter-indistinguishable from curator-asserted ones (any marker is dropped
  on the next upsert), permanent under the §1 merge contract, and would pollute
  the exact channel the ranker treats as ground truth. Orphan facts render as
  orphans.
- **Drop the fold on any error status** (the plan's literal wording) — rejected:
  loses the committed batches' durable session identity on the after-commit error
  path for no benefit; the fold there is honest and bounded to what committed.
- **Put the fold on the summary dict** — rejected: the CLI prints the summary as
  JSON, and the fold carries raw `session_id`s. It rides the journal line only.
- **Write-time `sources:` frontmatter stamping** (full session metadata embedded
  per curated file, with supersession inheritance) — the most durable candidate,
  deferred. Its own backfill analysis proved the read-time ladder (Slice A)
  covers every current store with zero writes; the extra machinery hedges a
  raw-retention policy that does not exist. If revived: `sources` must be a
  store-managed core key (unknown keys are dropped on upsert); inheritance must
  run only over facts whose soft-delete actually applied and must exclude the
  `user-directed` sentinel (else a pinned fact's successor is permanently
  pinned); and any provenance inheritance changes ranker breadth/recurrence
  semantics (D21) and must be named, not discovered.
