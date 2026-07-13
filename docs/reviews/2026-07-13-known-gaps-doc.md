---
slug: known-gaps-doc
status: awaiting-review
author: claude
reviewer: codex
branch: docs-known-gaps
diff: git diff main...HEAD -- AGENTS.md docs/known-gaps.md docs/README.md docs/neurobase-build-plan.md
created: 2026-07-13
---

# Review: known-gaps tracker + project-doc-schema backlog entry

## Brief  _(Author — Claude)_

**Intent.** Two things, both prompted by *your own* finding in the
[how-it-works review](2026-07-12-how-it-works-doc.md):

1. **Give defects a home.** That review surfaced a real code gap
   (`status --recommender` skips the D11 schema guard) with nowhere to file it:
   `adr/` is for decisions, `notes/` for scratch thinking, `reviews/` for batons,
   and the build-plan Backlog for *unbuilt features*. Nothing covered "code that
   already shipped and is wrong." New file `docs/known-gaps.md` fills that hole,
   seeded with that gap as **G1**.
2. **Backlog a product idea.** The user asked whether ADRs are a Neurobase
   feature. They are not — the app has no concept of them. But teaching the
   recommender to ingest structured project docs as first-class evidence is a
   genuinely good idea, so it's recorded in the build-plan Backlog (post-0.1.0).

This is **documentation-only**. No source, tests, or behavior touched. The bar is
factual accuracy + whether the taxonomy actually holds up.

**Scope.** Branch `docs-known-gaps` (one commit, `87c473c`), diff command above.
Key files:
- `docs/known-gaps.md` (new, 70 lines) — the tracker: routing table, conventions,
  status vocabulary, graduation path to GitHub Issues, and entry **G1**.
- `docs/neurobase-build-plan.md` (+20) — "Project doc schema" Backlog entry.
- `docs/README.md` (+1) — index row under Process.
- `AGENTS.md` (+4) — routing rule under "Where to put things".

**Focus areas.** In priority order:

1. **Is G1 factually right?** This is the one load-bearing technical claim.
   I assert: `status()` (`cli/__init__.py:100-102`) returns through
   `_print_recommender_metrics` *before* `_check_store_schema` at line 108, and
   the metrics path never calls the guard. I also assert the risk is bounded —
   that `metrics.compute_metrics` is **strictly read-only**, so it cannot *mutate*
   an incompatible store (which is what D11 exists to prevent), and can only print
   wrong numbers. **Please verify that read-only claim independently** — if metrics
   writes *anything* (it does read artifact bytes for the survival hash), my
   severity rating of `minor` is wrong and should be `major`.
2. **Does the taxonomy hold?** Four homes now (adr / notes / reviews / known-gaps)
   plus the build-plan Backlog. Is the boundary between them unambiguous from the
   AGENTS.md + docs/README.md routing alone? Would *you*, cold, file a new item
   correctly? If two categories overlap, say which and collapse them — a taxonomy
   nobody can apply consistently is worse than none.
3. **Should G1 just be fixed instead of documented?** Honest question. The fix
   looks like a one-line move plus a test. There's a real argument that writing
   70 lines of prose about a 1-line bug is the wrong trade, and I should have
   opened a code branch instead. I chose to document because it surfaced in a
   docs-only review where a code change was out of scope — but if you think that's
   bureaucratic, say so and I'll fix the code instead.
4. **The Backlog entry's claims.** It asserts the app has "zero concept" of ADRs
   (I verified: every `ADR-####` in `src/` is a comment citing a doc; the data
   model is `skill`/`rule` over `raw`/`curated`/`nodes`). It proposes a new
   `EvidenceRef` kind for structured docs — is that coherent with the spec §12.4
   corpus/evidence contract, or does it break an invariant? It also flags an open
   ADR-worthy fork (schema *scaffolded* by init vs. merely *recognized* if
   present) — is that the right fork?

**Known risks / tradeoffs.**
- **Meta-question I couldn't resolve myself:** is *creating a new docs category*
  itself a "design decision" that warrants an ADR under AGENTS.md's own rule
  ("a new design decision → an ADR")? I didn't write one, judging it a process
  convention rather than an architecture decision. That may be wrong. If you think
  it needs an ADR, that's a legitimate `major`.
- `known-gaps.md` deliberately does **not** use a numbered-directory pattern like
  `adr/` and `reviews/`, because there is exactly one entry. That's a bet that a
  single scannable file beats ceremony at this scale; it inverts if gaps pile up.
- The naming avoids `issues/` (collides with the GitHub Issues that Phase 9 ships)
  and `backlog.md` (collides with build-plan's Backlog, which means features).
  If you see a better name, now is the cheap moment to say so.
- The Backlog entry makes a forward-looking product claim that brushes the charter
  ("never auto-install"). I tried to respect it by flagging *recognized-if-present*
  as likely more in keeping than *scaffolded*. Push back if that's off.

**How to verify.**
```bash
git diff main...HEAD -- AGENTS.md docs/known-gaps.md docs/README.md docs/neurobase-build-plan.md

# G1's core claim — does status() return before the guard?
sed -n '86,112p' src/neurobase/cli/__init__.py
grep -n "_check_store_schema" src/neurobase/cli/__init__.py

# G1's bounding claim — is the metrics path really read-only?
grep -nE "write|upsert|append|open\(|unlink|replace" src/neurobase/recommender/metrics.py

# The Backlog entry's claim — does the app know what an ADR is?
grep -rniE "\badr\b" src/ | grep -v "^.*#" | head
```

**Out of scope.**
- This baton file itself — that's why the diff command names the four content files.
- The pre-existing uncommitted edit to
  `docs/notes/2026-07-09-phase-8-recommender-plan.md` — not part of this change.
- The `docs-how-it-works` branch (already approved by you; merges independently).
- **Fixing G1's code.** If your verdict is "just fix it," say so and I'll open a
  separate branch — but don't treat the unfixed code as a finding *against this
  diff*, which is documentation.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(none yet)_

**Verdict:** _pending_
