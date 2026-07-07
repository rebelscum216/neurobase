---
slug: phase-3-curator
status: awaiting-review
author: claude
reviewer: codex
branch: phase-3-curator
diff: git diff main...phase-3-curator
created: 2026-07-07
---

# Review: Phase 3 — curator engine + linkify

## Brief  _(Author — Claude)_

**Intent.** Implement the curator (spec §2) — the thinking loop that folds raw
captures into a small, current, non-redundant fact set — plus linkify (spec
§6). This is the load-bearing "optimize for deletion" quality bar (AGENTS.md
principle #4) and the first real consumer of the Phase 2 brain contract.

**Scope.** Branch `phase-3-curator` (on `main`@`4ef6c48`), `git diff
main...phase-3-curator`. Key files:
- `src/neurobase/curator/engine.py` — `curate(root, project, brain, *,
  dry_run, resynth, tombstone_grace_days)` running the §2 sequence; the plan
  (§2.1) and node (§2.2) prompts; `is_stale` (D8 `--if-stale` gate);
  `read_fact_count_trend` (the bloat alarm from `.curator-log.jsonl`).
- `src/neurobase/core/linkify.py` — the §6 idempotent `lineage:auto` fenced
  `[[wikilink]]` blocks (curated `## Lineage`, node `## Synthesized from`).
- `src/neurobase/core/store.py` — new `list_curated` helper (active facts,
  slug-sorted).
- `src/neurobase/cli/__init__.py` — live `curate` command; `status` fact-count
  trend line.
- `tests/test_{curator,linkify,cli_curate}.py` — 37 new tests.

**Focus areas.**
- **The §2 hard rules.** (a) A plan that won't parse ⇒ ABORT, leave *every*
  raw unconsumed (D9). (b) A *valid-but-empty* plan `{"upserts":[],
  "tombstones":[]}` IS consumed (idempotence) — distinct from (a). (c) Node
  synthesis / index failing *after* raws were consumed ⇒ `partial`, not
  `error`, keeping the applied state (the node self-heals since it's a pure
  function of `curated/`). Do the code paths actually deliver all three?
  (`test_plan_error_aborts_and_leaves_raw_unconsumed`,
  `test_valid_but_empty_plan_consumes_raw`,
  `test_node_synthesis_failure_is_partial_but_keeps_applied_state`.)
- **Step-4 supersession subtlety.** "For each superseded slug: tombstone it
  **unless that slug was itself re-upserted this pass**." Implemented by
  collecting superseded slugs across upserts, then tombstoning each not in the
  upserted set. Also `supersedes` is filtered of self. Correct?
  (`test_superseded_slug_reupserted_this_pass_is_not_tombstoned`,
  `test_supersedes_filtered_of_self`.)
- **linkify byte-for-byte frontmatter.** linkify must touch only the body —
  it reads the raw file text and reconstructs `---\n{frontmatter}\n---\n\n
  {body}` keeping the frontmatter substring verbatim (NOT via `store.write_doc`,
  which re-serializes YAML). Is the frontmatter genuinely preserved
  byte-for-byte, and is the fenced-block replacement genuinely idempotent (no
  stacking across reruns)? (`test_frontmatter_preserved_byte_for_byte`,
  `test_idempotent_no_stacking`, `test_raw_and_tombstones_never_modified`.)
- **Fail-safe skips.** Bad slug in an upsert ⇒ skip (not crash); empty
  slug/body ⇒ skip; tombstone/supersede of a missing fact ⇒ skip. All
  non-fatal, matching the "the model occasionally emits a bad slug" spec note.

**Known risks / tradeoffs.**
- Node synthesis when there are zero active facts writes a placeholder node
  (`# (no active facts)`) WITHOUT calling the brain, rather than asking the
  model to synthesize from nothing. Deliberate; flag if you'd rather it always
  call the brain.
- `_strip_outer_fence` defensively removes a single surrounding ```...``` fence
  from node text (the model is told not to fence, but sometimes does). Narrow
  heuristic — only strips a fence wrapping the *entire* output.
- The pass log (`.curator-log.jsonl`) is append-only and unbounded. Fine at a
  dev's scale; no rotation. `read_fact_count_trend` reads the whole file each
  `status` call (cheap at this scale).
- linkify's block-strip regex matches `<!-- lineage:auto ... -->` ...
  `<!-- /lineage:auto -->` non-greedily across the body. If a user hand-writes
  those exact markers into a body, linkify would treat them as its own block.
  Considered acceptable (they're clearly labelled "generated — edits here are
  overwritten").
- `--dry-run` returns the plan without applying/consuming; it does NOT write a
  curator-log line (no state changed). Intentional.

**How to verify.** `uv sync && uv run pytest && uv run ruff check . && uv run
ruff format --check . && uv run mypy src tests`. Live (the Phase-3 "Done
when"): `uv tool install . --force`, enable a scratch repo, hand-write raw
`*.md` captures under `.../memory/raw/`, `neurobase curate` → curated facts
with provenance, a regenerated `nodes/<project>-status.md`, `index.md`, and
`[[wikilinks]]`; a second `curate` no-ops. This was run manually (2 raws from
2 agents → 2 deduped facts) and is not part of the committed suite (it hits a
real brain backend); the deterministic tests use a fake brain.

**Out of scope.** `adapters/` (Phase 4/5 — the scribes that *produce* raw
captures and the recall that *injects* nodes; today raws are hand-written for
tests/demo). `recommender/`, `mcp/`. No spec-§11 fixture tests yet (those are
scribe-facing, land with Phase 4/5).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** approve | changes-requested — _one-line rationale._
