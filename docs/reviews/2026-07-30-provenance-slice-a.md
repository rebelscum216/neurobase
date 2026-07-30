---
slug: provenance-slice-a
status: awaiting-review   # draft | awaiting-review | changes-requested | approved
author: claude
reviewer: codex
branch: feat/provenance-slice-a
diff: git diff origin/main...HEAD
created: 2026-07-30
---

# Review: Provenance Slice A — the read-only session→fact graph

## Brief  _(Author — Claude)_

**Intent.** Build `core/graph.py`: a deterministic, LLM-free, **read-only** join
that answers "which conversations fed which memory". It joins two records the
store already keeps — a curated fact's `provenance`/`supersedes` frontmatter
(spec §1) and the curator's per-pass `fold` journal record (spec §2, ADR-0022) —
into sessions, facts, session→fact edges, and fact→fact (supersession) edges.
Nothing here writes and nothing here infers. This is the data layer the Web UI's
Phase G canvas-graph home surface consumes; the fact→proposal half of that graph
is composed later in the webui route layer, not here.

**Scope.** Branch `feat/provenance-slice-a`, `git diff origin/main...HEAD`
(single commit `4baa135`, PR #10). Key files:

- `src/neurobase/core/graph.py` — **new, 463 lines.** The whole feature:
  `memory_graph(handle, project=None, *, include_tombstoned=False)` plus the four
  frozen node/edge dataclasses and `parse_raw_filename`.
- `src/neurobase/core/store.py` — **new `list_tombstoned(root, project)`**, the
  first and only enumerator of `.tombstones/`.
- `src/neurobase/core/store_handle.py` — the handle passthrough for it.
- `scripts/check_store_chokepoint.py` — `list_tombstoned` added to
  `STORE_FORBIDDEN` so the new accessor is covered by the ADR-0015 guard.
- `docs/neurobase-spec-appendix.md` — §1 gains the paragraph specifying that
  `list_tombstoned` discharges the curated-wins exclusion MUST itself; §10's
  chokepoint accessor list gains `list_tombstoned`.
- `docs/architecture.md` — `graph.py` recorded in the `core/` inventory with its
  layering constraint.
- `tests/test_graph.py` — **new, 477 lines, 29 tests.** `tests/test_store.py` —
  +5 for `list_tombstoned`.

**Focus areas.** Where I most want your eyes, roughly in order:

1. **The degradation ladder** (module docstring, rungs 1–4). The claim is that
   this reader *degrades to coarse, never fabricates, and never drops*. Rung 3
   builds a **skeleton** session node from the filename grammar alone and marks
   the edge `resolved=False`; `parse_raw_filename` deliberately refuses to return
   `sid8` because it is a truncated, stripped digest of the session id, not the
   id. Is there any input where the ladder silently drops an edge, or where a
   skeleton leaks a value it cannot actually know?
2. **Spec §1's tombstone resolution MUST.** The exclusion (a slug in *both*
   `curated/` and `.tombstones/` is active; the tombstone is residue) is
   implemented inside `store.list_tombstoned` rather than in each caller, and the
   spec paragraph was edited to say so. Verify the implementation actually
   discharges the MUST for every path, and that moving the rule into the
   enumerator is a faithful reading of §1 rather than a convenient one.
3. **`include_tombstoned` vs ghosts** (`_project_graph`, the `known`/`included`
   split). A slug that is *known but excluded by the toggle* must yield no node
   and no edge; only a slug that exists in neither `curated/` nor `.tombstones/`
   becomes a `ghost`. Without the split, `include_tombstoned=False` would promote
   every tombstoned fact into a ghost. Both `add_session_edge` and `add_fact_edge`
   depend on this; `add_fact_edge`'s three-way guard is the subtlest code in the
   diff.
4. **Journal-vs-frontmatter precedence.** Journal attribution is the validated
   record (§2 step 4) and wins when a pass recorded both; the dedup key is
   `(project, session, fact)` / `(project, old, new)` and the "does the new source
   beat the existing one" condition is written as a negated compound. Check that
   condition for order-dependence — the result must not depend on whether the
   frontmatter or the journal pass ran first.
5. **Fail-soft scoping.** Deliberately asymmetric: in the all-projects sweep a
   corrupt registry slug or unreadable tree contributes nothing, but an
   **explicitly named** invalid project still raises `store.InvalidSlugError`
   (swallowing it would turn a caller's typo into a silently empty graph). Is the
   `except OSError` in the sweep too broad — does it swallow anything that should
   surface?
6. **Layering + chokepoint.** `core/graph.py` must not import `recommender/`
   (upward import, `docs/architecture.md`), and takes a `StoreHandle` rather than
   a raw `root` precisely so it is not exempt from the ADR-0015 CI guard. Also
   confirm the guard edit is complete — is `list_tombstoned` reachable by any
   spelling the guard doesn't key on?

**Known risks / tradeoffs.**

- **Two deliberate deviations from the 2026-07-16 provenance plan**, both forced
  by what landed since, both documented in the module docstring: `memory_graph`
  takes a `StoreHandle`, not `root: Path` (ADR-0015 chokepoint); and `SessionNode`
  carries `files: tuple[str, ...]`, not a single `file`, so a conversation with
  several captures is one node rather than N disconnected ones.
- **Run-granular edges are deliberately NOT rendered** (provenance plan A6). Only
  `fold.edges` — validated per-fact attribution — produces journal edges. Joining
  `fold.consumed` to every fact a pass touched would sprout 90 edges off one
  unattributed fact in a 90-raw backlog pass. Unattributed facts render as honest
  orphans. This is a choice, not an oversight — but tell me if the orphan
  outcome is worse than the alternative I rejected.
- **Session grouping keys on `session_id or file`** (`_session_key`). Two captures
  of the same conversation where one raw's frontmatter lacks `session_id` will
  therefore split into two nodes. I believe that is the honest outcome (nothing on
  disk ties them together) but it is a real fidelity limit worth a second opinion.
- **`agent` for a multi-agent session** is `sorted(agents)[0]` — arbitrary but
  deterministic. If a session ever legitimately spans agents, the node under-reports.
- **No cache.** One frontmatter parse per file per call, matching the webui's
  existing per-request re-read posture (measured in the Phase G plan: ~19 ms and
  ~44 KiB of JSON for the dev store). Ordering is total and stable so two graphs
  can be diffed.

**How to verify.**

```
git diff origin/main...HEAD
uv run pytest tests/test_graph.py tests/test_store.py -q
make ci                      # full gate, includes check_store_chokepoint
```

The commit claims the full gate is green, and green again with `claude` and
`codex` hidden from `PATH`. Verify rather than trust — and treat any `MUST` in
[`docs/neurobase-spec-appendix.md`](../neurobase-spec-appendix.md) that this
violates as a **blocker**, per the relay checklist. Same for the layering rule in
[`docs/architecture.md`](../architecture.md) and AGENTS.md principle #2 (the
build stays spec-derived; no code lifted from the prior private implementation).

**Out of scope.**

- **The Web UI Phase G route and canvas renderer.** Not in this diff. `/graph`
  does not exist yet and the rail entry is still an inert `soon` stub. Display
  derivation (session titles, body snippets, prompt counts) belongs to the route
  layer by design — `core/graph.py` stays identity-only.
- **Proposal/skill nodes and evidence-kind edges.** Composed in the webui route
  layer from `recommender/`, which `core/` may not import.
- **Slice B.** Already on `main` (PR #7); this builds on its journal, it does not
  revisit it.
- **The pre-existing `list_curated` / `prune_tombstones` behaviour**, except where
  `list_tombstoned` must agree with it.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(none yet)_
