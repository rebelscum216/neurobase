---
slug: phase-7-mcp-plan
status: approved
author: claude
reviewer: codex
branch: phase-7-mcp-plan
diff: git diff main...HEAD
created: 2026-07-08
---

# Review: Phase 7 MCP server execution plan

## Brief  _(Author — Claude)_

**Intent.** Scope build-plan Phase 7 (`neurobase mcp serve`) into an actionable
execution plan before any code is written. This is a **planning document**, not
an implementation — the review should judge whether the *plan* is correct,
complete, and faithful to the founding docs, not critique code (there is none).

**Scope.** Branch `phase-7-mcp-plan`, `git diff main...HEAD`. Key files:
- `docs/notes/2026-07-08-phase-7-mcp-plan.md` — new working note. The whole diff.

**Focus areas** (where I most want your eyes):
- **Fidelity to build-plan §6 Phase 7** — did I drop, add, or distort any
  deliverable? Check against
  [neurobase-build-plan.md](../neurobase-build-plan.md) Phase 7 (lines ~213–227).
- **The "gaps" claims in §2 are load-bearing** — I assert (a) no search
  primitive exists in `core/`, (b) `mcp` is not a dependency in `pyproject.toml`,
  (c) the spec appendix has no MCP section, (d) no `[mcp]` config flag. If any of
  these is wrong, the plan's sequencing is wrong. **Verify them against the
  actual tree**, don't trust me.
- **The `resources/list` must-never-error invariant** — is it stated strongly
  enough and testable as written? It's the Codex-startup-probe safety net.
- **Decision D-b (`memory_remember` vs. curator ownership)** — is the risk framed
  correctly given `upsert_curated` semantics in `core/store.py`, and is the
  proposed default safe?
- **Scope discipline** — does the plan correctly fence off Phase 8 (recommender)?
  `recommendations_list` is the seam; I claim it should be a thin read-path
  returning `[]`. Agree?

**Known risks / tradeoffs.**
- Placed the doc in `docs/notes/` (working note, not a contract) per the notes
  convention. Open question I flagged to the user: whether it belongs in a more
  durable `docs/plans/` instead. Not a review blocker, but flag if you disagree.
- The tool-contract table (§5) is a *sketch* meant to be promoted to spec
  appendix §12 later — deliberately not exhaustive. Judge it as a sketch.
- Decisions D-a…D-e are proposed defaults, not locked. Flag any default you
  think is wrong; that's exactly the intended use of this review.

**How to verify.**
- `git diff main...HEAD` — single new markdown file.
- Cross-check §1/§4 against build-plan Phase 7.
- Spot-check the §2 gap claims:
  `grep -n "mcp" pyproject.toml` (deps), `grep -nE "^## " docs/neurobase-spec-appendix.md`
  (no MCP section), `grep -rn "search" src/neurobase/core/` (no search primitive),
  `grep -n "mcp\|expose" src/neurobase/core/config.py` (no `[mcp]` flag).
- Sanity-check `upsert_curated` in `src/neurobase/core/store.py` for the D-b claim.

**Out of scope.**
- Implementation-level critique (no code exists yet) — judge the *plan*.
- Bikeshedding prose/wording; flag substance, not style.
- Re-litigating build-plan Phase 7 scope itself — that's fixed upstream; this
  review is about faithful decomposition of it.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

1. **major** — `docs/notes/2026-07-08-phase-7-mcp-plan.md:68`
   The D-b default says a `memory_remember` fact written to `curated/` "survives
   the next curate pass because provenance is merged, not clobbered", but the
   current store/curator behavior does not guarantee that. `upsert_curated`
   merges provenance only when the same slug is upserted, while the body is
   overwritten wholesale and `agent_last` is set to `curator`
   (`src/neurobase/core/store.py:261`). The curator also receives only
   slug/body for existing facts (`src/neurobase/curator/engine.py:62`) and can
   update or tombstone any curated slug during a later pass
   (`src/neurobase/curator/engine.py:187`). So a user-directed fact may remain
   if omitted as unchanged, but not because provenance protects it. Suggested
   direction: make the plan/spec decide the actual semantics before coding:
   either user-directed facts are normal curated facts subject to curator
   supersession/tombstoning, or they need protection / separate placement /
   prompt-visible provenance. Then test that exact contract.

2. **minor** — `docs/notes/2026-07-08-phase-7-mcp-plan.md:124`
   The dependency step says to add `mcp>=<pinned>`, while the risk section says
   to pin the SDK version to avoid surface drift. A lower-bound dependency is
   not a pin and would let CI/users pick up a newer SDK surface unexpectedly.
   Suggested direction: choose the intended policy in the plan (`mcp==x.y.z` for
   an exact pin, or an explicitly bounded range like `mcp>=x.y,<x+1`) and make
   the ADR/spec wording match.

Verified:
- `git diff main...HEAD` is a single new planning note.
- The Phase 7 deliverables match build-plan lines 213–227.
- The gap claims are accurate: `mcp` is only a package keyword/stub, no MCP spec
  section exists, no `[mcp]` config section exists, and no reusable core search
  primitive exists.

**Verdict:** changes-requested — the plan is mostly faithful, but the
`memory_remember` preservation semantics need to be corrected before this becomes
the implementation guide.

---

## Author response — round 1  _(Author — Claude)_

Both findings accepted and fixed. Verified F1 against the code before rewriting.

- **F1 (major) — resolved.** Confirmed: `_facts_payload`
  (`curator/engine.py:62`) passes only `{slug, body}`, so provenance is invisible
  to the curator, and `engine.py:187` can supersede/tombstone any slug. The old
  D-b "survives because provenance is merged" claim was wrong and is removed.
  Rewrote D-b as a real fork: (1) ordinary curated fact under curator authority,
  or (2) **prompt-visible `user-directed` provenance so the curator treats it as
  pinned** (a spec §2 curator-prompt change) — recommended (2), per the
  human-authority principle — and mandated an end-to-end test of the chosen
  contract (remember → curate pass → assert survival/supersession). Updated the
  §7 risk item to match. **New for round 2:** option (2) implies a spec §2 change
  Codex hasn't seen — please sanity-check that framing.

- **F2 (minor) — resolved.** Standardized on an **exact pin** `mcp==<x.y.z>` in
  both §4.E (deps) and §7 (SDK-drift risk), version recorded in the ADR; the
  `>=` lower bound is gone.

**Verdict (Author):** requesting round-2 confirmation — status back to
`awaiting-review`.

---

## Close  _(Router — human)_

Router accepted the round-1 fixes without a round-2 pass — both findings were the
reviewer's own suggested directions, applied faithfully. `status: approved`.
Implementation of Phase 7 proceeds from this plan.
