# Session→fact provenance — hardening plan for the graph UI

_2026-07-16 — working plan. This is a plan, not a contract. Companion to
[`2026-07-16-webui-app-shell-plan.md`](2026-07-16-webui-app-shell-plan.md), which
consumes the read service specified here (its Phase G). Grounded in a full
subsystem review of curator, store, brain, recommender, webui, and project law;
three candidate designs were drafted and adversarially judged — this plan is the
synthesis._

## Reframing the gap

The app-vision graph needs **session→fact edges** ("which conversations fed which
memory"). First finding: **the data largely exists already.**

- Every curated fact carries `provenance: ["raw/<filename>.md", ...]` in its
  frontmatter, written by the curator from the plan's `from_raw` field
  (`engine.py:113`, spec §2 step 4) and **merged order-preserving across
  upserts** (spec §1 contract — "MERGED across upserts"; `store.py:285-296`).
- Every raw capture's frontmatter carries the full `session_id`, `agent`,
  `captured_at`, `cwd`, `branch` (`store.py:208-215`), and its filename encodes
  `{ts}_{agent}_{sid8}` (`store.py:169-184`).
- Raw files are never deleted by any pipeline path (only `.tombstones/` is
  pruned, `store.py:339-361`), so provenance targets persist.
- The recommender ranker already walks this exact chain one hop deep
  (`ranker.py:285-308`, ADR-0007 D21).

So session→fact is **one deterministic hop today**, and the seeded dev store
renders a full graph with zero writes. The *real* gaps, none formally
acknowledged in `known-gaps.md`:

1. **Trust** — `from_raw` is LLM-asserted and never validated against the
   actual batch. Hallucinated filenames (including echoes of old raw basenames
   present in the `## Lineage` blocks inside fact bodies the LLM sees) land in
   the permanent record.
2. **Durability** — lineage thins at the system's central operation: when facts
   fold, the superseded facts' edges survive only the 14-day tombstone grace;
   `supersedes` is overwritten (not merged) on re-upsert (`engine.py:111` +
   `store.py:295`), so chains are single-generation.
3. **Audit** — the per-pass `.curator-log.jsonl` records pass summaries —
   status, integer counts, timestamp, optional error, and differently-shaped
   noop/resynth records (`_log_pass`, `engine.py:153-158`) — but no per-pass
   raw→fact identities or edges; which raws fed which facts in a given pass is
   not recorded anywhere.
4. **Read surface** — nothing exposes the edges; the ranker's walk is private.

## The design (synthesis of three judged candidates)

Two slices, sequenced by blast radius. Slice A ships the UI; Slice B hardens
the record. Neither depends on the uncommitted `feat/webui-phase1-suggestions`
branch.

### Slice A — read-only graph service (zero writes, zero spec changes)

New module `src/neurobase/core/graph.py` — legal in `core/` per the
`core/search.py` precedent (it reads only markdown/frontmatter/filesystem;
**must not import `recommender/`** — that would be an upward import per
`docs/architecture.md`).

```python
@dataclass(frozen=True)
class SessionNode:   # id, project, file, session_id?, agent?, captured_at?, resolved: bool
@dataclass(frozen=True)
class FactNode:      # slug, project, status, pinned, updated_at, tombstoned: bool
@dataclass(frozen=True)
class SessionFactEdge:  # session_id_or_file, fact_slug, project, resolved: bool

def memory_graph(root: Path, project: str | None = None,
                 *, include_tombstoned: bool = False) -> MemoryGraph:
    """Deterministic, LLM-free, fail-soft. One frontmatter parse per file per
    request (the existing webui per-request re-read posture)."""
```

Edge construction is a **degradation ladder** (D21 discipline — degrade to
coarse, never fabricate, never drop):

1. For each active curated fact (`store.list_curated`), each `provenance` entry
   starting with `raw/` resolves to the raw file; session identity read from
   raw frontmatter → **resolved edge**.
2. Raw file missing → parse the filename grammar `{ts}_{agent}_{sid8}.md`
   (inverse of `store.raw_filename`) → skeleton session node, edge kept,
   `resolved: False`.
3. `user-directed` and `seed:*` provenance entries produce **no** session edge
   (matching the ranker's `startswith("raw/")` filter); `user-directed`
   membership marks the fact `pinned` (the sentinel stays untouched —
   `engine.py:71,84`).
4. `supersedes` frontmatter → fact→fact edges. A `supersedes` slug found in
   neither `curated/` nor `.tombstones/` renders as a **ghost fact node**
   (`resolved: False`) — the same never-fabricate/never-drop ladder as session
   edges, since `supersedes` is overwritten on re-upsert and targets get
   pruned.
5. Session nodes additionally sourced from `store.list_raw(...,
   unconsumed_only=False)` so sessions with no fact edges still appear.
6. Once Slice B lands, journal `fold.edges` entries enrich/confirm frontmatter
   edges and supply session metadata without touching raw files; journal
   `fold.superseded` pairs likewise supply fact→fact edges that outlive
   tombstone pruning and multi-generation supersession (rendered
   dimmed/historical). Precedence: journal-attributed > frontmatter.
   **Run-granular ("this fact was touched in a pass that consumed sessions
   S1..Sn") edges are deliberately NOT rendered** — an unattributed fact in a
   90-raw backlog pass must not sprout 90 edges. Unattributed facts render as
   honest orphans.

Supporting store change in this slice: a small additive
`store.list_tombstoned(root, project)` helper — no store API enumerates
`.tombstones/` today (`list_curated` reads only `curated/`), and the
`include_tombstoned` toggle needs one; the Memory view (app-shell plan Phase 2c)
reuses it.

The fact→proposal/skill half does **not** live in `core/graph.py`: the webui
route layer (presentation may import both core and mid-tier) composes
`memory_graph()` with `proposals.load_all_proposals` +
`corpus.EvidenceRef.from_frontmatter`/`resolve_evidence` — exactly the
`_evidence_rows` pattern already in `webui/routes.py:163-190`. All three
evidence kinds map to edges: `curated` → fact→proposal, **`raw` →
session→proposal** (load-bearing: in the real dev store two of four proposals —
including an accepted skill — carry *only* raw-kind evidence and would
otherwise be disconnected), `proposal` → proposal→proposal. If the CLI ever
needs the same composition, lift it into a named single-purpose mid-tier module
(the `adapters/recall_common.py` pattern) — never a lateral import.

**Rejected:** any heuristic backfill (a Jaccard text-similarity pass writing
`raw/` entries into `provenance`). All three judges killed it: backfilled
entries would be frontmatter-indistinguishable from curator-asserted ones (any
`extra_frontmatter` marker is dropped on the next upsert, `store.py:291-299`),
permanent under the §1 merge contract, and would pollute the exact channel the
ranker treats as scoring ground truth. Orphan facts render as orphans.

### Slice B — write-side hardening (one reviewed commit, small)

Two changes in `curator/engine.py`, both deterministic, LLM response schema
byte-for-byte unchanged (D9, `parse_plan_json`, ADR-0002 all untouched):

**B1 — validate `from_raw` against the batch.** `_apply_upserts`
(`engine.py:98-122`) gains a `batch: set[str]` parameter (built from the
`raw_docs` already in scope at `engine.py:179`). Provenance is constructed from
`from_raw ∩ batch`; dropped names are counted. One sentence added to
PLAN_SYSTEM prose ("list only filenames present in raw_captures of this
request") — §2.1 explicitly permits prompt-text changes; the deterministic
filter is the enforcement. This **is** a spec MUST edit (§2 step 4 currently
fixes `provenance = ["raw/"+name for name in from_raw]` with no validation) →
spec edit + ADR required. Fixture audit already done during research: every
current FakeBrain plan fixture (`test_curator.py`, `test_cli_curate.py`,
`test_cross_agent.py`) cites raw filenames it actually writes into the tmp
store, so no existing test changes behavior — the hallucinated-`from_raw` case
is a new adversarial test, not a retrofit.

**B2 — the fold journal.** `_log_pass` (`engine.py:153-158`) gains a
`fold: dict | None = None` keyword — **never** new keys on the summary dict,
which the CLI prints as JSON (`cli/__init__.py:176-228`, line 226 dumps every
key). `tests/test_cli_curate.py` passes unmodified, but it currently asserts
only `status`/`upserts` — B2's tests add an **exact-key-set assertion** on the
printed summary so shape drift actually fails. Applied passes append:

```json
"fold": {
  "v": 1,
  "consumed": [{"file": "...", "session_id": "...", "agent": "claude", "captured_at": "..."}],
  "edges": {"<fact-slug>": ["<validated raw filename>", ...]},
  "superseded": [{"slug": "old-fact", "by": "new-fact"}],
  "tombstoned": ["dead-fact"]
}
```

- `consumed` embeds full session identity **at consumption time**, so journal
  edges outlive any future raw-retention policy or a fresh clone with
  gitignored `raw/`.
- `edges` = the validated `from_raw ∩ batch` map; an empty list means
  "upserted this pass, unattributed" (rendered as an orphan, see A6).
- `superseded` pairs are recorded **only where the soft-delete actually
  applied** (a superseded slug that was re-upserted the same pass, or pinned,
  is not tombstoned — `engine.py:215-229` — and must not be journaled as if it
  were). This is the only record that survives multi-generation supersession
  and tombstone pruning.
- Semantics by pass status: `ok`/`partial`/valid-but-empty plan → `fold`
  present (`_log_pass` runs after the step-8 try/except); `error` (D9 abort)
  and noop → no `fold`; dry-run logs nothing (unchanged).

**Supporting moves:**

- Move the `.curator-log.jsonl` filename constant down to `core/store.py` so
  the `core/graph.py` reader and the curator writer share it legally (today
  the name is curator-private; core reading a curator-owned format would be a
  cross-layer format dependency).
- Spec-document the fold record (additive §2 clause) — reading it from core
  makes it a contract, and AGENTS.md forbids silent code/spec divergence.
- State and test the invariant: **the journal may have gaps** (crash window
  between `mark_consumed` at `engine.py:231-233` and `_log_pass` at ~:262);
  **the frontmatter∪journal union is the contract.** Frontmatter backstops
  exactly the fact-granular edges of a lost pass.

### ADR-0013 (one ADR covers the slice-B package)

Records: the §2 step-4 validation change; the fold-record read contract; the
explicit judgment that additive JSONL fields + additive read service need **no
D11 schema bump** (the seed-importer `extra_frontmatter` precedent — recorded
deliberately because G1 shows the schema guard is already contentious); the
rejection of heuristic backfill; and the deferral of the `sources:` frontmatter
key. Numbering: the ADR index jumps 0008→0010 (0009 is treated as burned) and
**0012 is the web UI surface ADR** landing with the Phase 1 branch — take
**0013**.

### Deferred, with a blueprint on file

Write-time `sources:` frontmatter stamping (full session metadata embedded in
each curated file, with supersession inheritance) was the most durable
candidate design, but its own backfill analysis proved the read-time ladder
covers every current store with zero writes — the extra machinery hedges a
raw-retention policy that doesn't exist. Deferred until raw pruning or
store-git-sync is real. If revived, the judged design's load-bearing details
are recorded in ADR-0013's alternatives section verbatim: `sources` must be a
store-managed core key (unknown keys are dropped on upsert), inheritance must
run only over facts whose soft-delete actually applied, must exclude the
`user-directed` sentinel (else a pinned fact's successor is permanently
pinned), and any provenance inheritance changes ranker breadth/recurrence
semantics (ADR-0007 D21) and must be named, not discovered.

## Tests

- `tests/test_curator.py` (+6–8): hallucinated `from_raw` dropped + count
  surfaced; fold present on ok/partial/empty-plan with consumed metadata; fold
  absent on error/noop; dry-run still unlogged; superseded pairs only where
  applied (adversarial: superseded slug re-upserted same pass; pinned target);
  CLI-printed summary unchanged (exact key set, not just spot keys).
- `tests/test_graph.py` (new, ~8–10): resolved edges from provenance +
  raw-frontmatter join; missing-raw filename-grammar degradation
  (`resolved=False`, edge kept); sentinels produce no session edges; pinned
  detection; supersedes edges incl. the ghost node for a pruned target;
  tombstoned inclusion toggle (via the new `list_tombstoned` helper); journal
  `superseded` pairs rendered; a raw-only-evidence proposal gains
  session→proposal edges (route-layer composition test); fail-soft on
  malformed files; journal-over-frontmatter precedence; no run-granular edges.
- Store: constant relocation is import-path-only (no behavior test needed
  beyond existing suite passing).
- Full `scripts/ci.py` gate (ruff / format / mypy / pytest), 3-OS matrix,
  before push. Every new MUST gets a named test.

## Sequencing

1. **Slice B** first, on `main`, through the Codex review relay — it is
   independent of the uncommitted webui branch and smallest-blast-radius
   (~40 changed lines in `engine.py`, spec edit, ADR-0013, tests).
2. **Slice A** second (`core/graph.py` + tests) — also branch-independent.
3. The graph **UI** (routes + template + renderer) stacks on the webui branch —
   scheduled as Phase G of the app-shell plan.

## Sensitivity note

`fold.consumed` embeds full session ids; the journal is the same sensitivity
class as `raw/` itself. It is served only on the loopback-bound UI, and the
seeded dev store (`~/neurobase-dev`) remains BWE-internal — never published,
never committed.
