# ADR-0024: `reopen` — the one sanctioned `rejected → proposed` transition

- **Status:** Proposed
- **Date:** 2026-07-27
- **Revises:** the §12.7 "a decided proposal is never silently reopened" clause —
  narrows it from "never" to "never *silently*; only by an explicit, ledgered
  `reopen`".
- **Relates to:** ADR-0020 (`revert` is the analogous explicit transition off
  `accepted`); the Web UI "Reconsider" action (spec §14).

## Context

Proposals are durable once decided. §12.7 makes this load-bearing in two ways:

1. The **miner never resets** a decided proposal. When `recommend run` re-mines a
   pattern that already has an `accepted`/`rejected`/`superseded` proposal, it
   does not flip it back to `proposed` — otherwise every run would re-surface
   things the user already judged, and rejection would carry no weight.
2. `accept`/`edit`/`reject` on a decided proposal are hard, named-status errors.

That durability is correct for the *miner*, but it left a real user need
unserved: **a rejection made in error, or one the user later reconsiders, had no
way back.** The only recourse was hand-editing `status:` in the proposal file,
which bypasses the ledger and leaves the history lying about what happened. The
Web UI made this sharp — a "Reconsider" affordance on a rejected proposal has no
backing operation.

`revert` (ADR-0020) is the same shape on the other side: an explicit,
ledger-recording transition *off* a decided state (`accepted → proposed`) that
the miner would never make on its own. `reopen` is its mirror for `rejected`.

## Decision

Add `reopen_proposal(root, slug)` (and `recommend reopen <slug>`, and a Web UI
"Reconsider" button): flip a **`rejected`** proposal back to `proposed`, stamp
`updated_at`, and append exactly one `reopened` ledger event.

Deliberately narrow:

- **Only `rejected` reopens.** `accepted` has its own paths (`revert` /
  re-check); a `superseded` proposal was replaced by a *newer* proposal, so
  reopening it would resurrect a duplicate of something already superseded.
  Both are refused with a named-status error — the same shape as §12.7's other
  blocked-status rules.
- **The prior rejection stays in the ledger** and in the per-type reject feedback
  the miner reads (`corpus.load_ledger_summary`). Reconsidering does not rewrite
  history: the proposal *was* rejected once, and that signal still informs the
  aggregate. `reopen` records a new event on top; it does not retract the old one.
- **The miner does not re-decide it.** Once `proposed` again, the next
  `recommend run` treats it as a live proposal and updates it in place (§12.6
  dedupe/supersede) rather than minting a duplicate — so `reopen` puts the
  proposal back in the review queue without forking it.

The invariant §12.7 states becomes: *the miner never resets a decided proposal;
`rejected → proposed` happens only via an explicit `reopen`, and only from
`rejected`.*

## Consequences

- One new proposal-store operation and one new ledger event kind (`reopened`),
  parallel to `reverted`. No new frontmatter fields; `status`/`updated_at` are
  the existing ones.
- Survival/precision metrics (§12.9) key on current `status`, so a reopened
  proposal drops out of the decided set cleanly while the ledger keeps the full
  `proposed → rejected → reopened` history.
- The Web UI's reject↔reconsider loop is now closed: a mistaken reject is
  one click to undo, ledgered, with no file surgery.
- **Not** an un-reject-then-forget: `reopen` only changes status. It never
  touches artifacts (there are none for a rejected proposal) and never edits the
  draft — the user re-reviews the same proposal.
