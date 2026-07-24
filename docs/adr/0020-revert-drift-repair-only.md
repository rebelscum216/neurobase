# ADR-0020: `recommend revert` is drift repair only, not an un-accept

- **Status:** Proposed
- **Date:** 2026-07-24
- **Resolves:** G2 (`docs/known-gaps.md`); review findings F1/F2/F4 in
  `docs/reviews/2026-07-23-g2-g3-recommender-fixes.md`
- **Supersedes:** none (revises the ADR-0007 D14/D15 lifecycle clause; see below)

## Context

Accepting a proposal records three things: `status: accepted` and an
`installed_path` in the proposal frontmatter, and a ledger `accepted` event
carrying the artifact's `installed_hash` (ADR-0011). §12.7 deliberately makes
`reject` a hard error on an already-`accepted` proposal: v1 has no
uninstall-by-command (ADR-0007 D14/D15), so allowing `reject` there would flip a
proposal's metadata to `rejected` while its real managed artifact stayed
installed and now out of sync with its own record. That is the load-bearing rule
G2 must not reopen.

G2 is the drift the same rule leaves unrepairable: the user removes or edits the
installed artifact by hand, so the store still reads `accepted` with a dangling
`installed_path`, and nothing can make the record honest again. A first
`recommend revert` (commit `cb40448`) flipped any `accepted` proposal back to
`proposed` and cleared `installed_path`, artifact untouched. Codex's review found
it reopened the very backdoor §12.7 forbids:

- **F1 (major).** On a **healthy** artifact (present, unchanged), `accept →
  revert → reject` exits 0 at every step and ends `rejected` with the managed
  artifact still live on disk — exactly the orphaning `reject`-on-`accepted`
  exists to prevent. Reproduced through the CLI.
- **F2 (major).** `accept → revert → accept` was a no-op ("Already up to date")
  that left the proposal `proposed`, because revert left the bytes intact and
  both accept surfaces short-circuit whenever the render already matches disk.
  Re-acceptance was unreachable.

The question this ADR settles: what is the boundary that makes revert repair the
drift without becoming the un-accept ADR-0007 deferred?

## Decision

- **D39 — Revert is defined only on demonstrated drift.** `revert` is allowed on
  an `accepted` proposal only when its artifact is in a state that proves the
  recorded acceptance is no longer true of disk: **missing** (the recorded
  `installed_path` does not resolve to readable bytes), **modified** (present but
  its content no longer matches the latest `accepted` event's `installed_hash`),
  or **unrecorded** (`accepted` with no `installed_path` to point at). A
  **healthy** artifact (present, byte-identical to the recorded hash) is refused,
  and so is an **unverifiable** one (present, but from a pre-`installed_hash`
  acceptance, so drift cannot be *proven* — the same events `metrics` survival
  treats existence-only). The classification is one function,
  `proposals.artifact_state`, keyed on the same `installed_path` + latest-hash
  comparison `metrics._survival_one` already makes, so "drifted enough to revert"
  and "not survived" can never diverge. The artifact on disk is still never
  touched on any path — to retire a healthy artifact the user removes it by hand
  first, which puts the proposal in the `missing` state revert then repairs.
  This closes F1 (a healthy artifact is non-revertable, so `accept → revert →
  reject` cannot start) and, together with D40, F2.

- **D40 — A matching-but-unrecorded artifact is recordable without a write.**
  "The render already equals disk" now means two different things. If the
  proposal already reads `accepted`, it is §12.7's idempotent no-op — nothing to
  write, nothing to record. If it does not (the state a drift-repair revert
  leaves behind, once the user restores the bytes), the artifact is installed
  while the store denies it: acceptance is recorded through a new
  `install.record_acceptance` path that writes and backs up nothing but stamps
  `status: accepted`, `installed_path`, and a fresh `installed_hash`. A
  **foreign** target (a file Neurobase never owned) is excluded — matching bytes
  must never silently claim ownership of a user's own file. This makes
  re-acceptance reachable after any revert and closes F2 without weakening the
  no-op contract.

- **F4 — the record.** This ADR is that required record: revert adds a public
  command, a `reverted` ledger event (now carrying the drift `reason`), and an
  `accepted → proposed` transition that ADR-0007 D14/D15 did not contemplate.
  Those decisions said an accepted proposal is never metadata-transitioned while
  its artifact stays in place; that remains true — D39 permits the transition
  **only** once the artifact is demonstrably no longer in place unchanged. This
  ADR revises that clause to the drift-repair carve-out rather than reversing it;
  ADR-0007 stays otherwise intact, and v1 still has no uninstall-by-command.

## Consequences

- **The `accept → revert → reject` backdoor is closed structurally**, not by
  convention: the sequence cannot begin, because revert refuses a healthy
  artifact. A proposal and its disk artifact can no longer contradict each other
  through any supported command sequence — verified by a CLI regression.
- **Retiring a healthy accepted proposal is a two-step, user-visible act:**
  remove the artifact by hand, then `revert` (now `missing`) and `reject`. Both
  refusal messages say so. This is the deliberate cost of not shipping an
  uninstall-by-command in v1.
- **`revert` and `metrics` survival now share one drift classifier**
  (`proposals.artifact_state`), so a future change to how drift is detected
  updates both at once. The `latest_accepted_event` helper moved to
  `proposals` for the same reason (a re-accepted proposal has several `accepted`
  events; both consumers must compare against the newest).
- **Spec §12.7 and `docs/how-it-works.md` are updated** to the drift-repair
  semantics and the installed-but-unrecorded accept path; **G2 flips to
  `fixed`.** A real un-accept (uninstall through backup/consent) remains
  deferred, exactly as ADR-0007 left it — this ADR does not add one.
- **Round-2 tests drive the real install service**, not `accept_proposal`
  directly — the gap that let F1/F2 pass round 1. The `accept → revert →
  {reject, re-accept}` flows are pinned through the CLI and the web UI POST.

## Alternatives considered

- **Also add a real un-accept** (uninstall a healthy artifact via
  diff/consent/backup). Rejected for this ADR: it is a larger change that
  reverses ADR-0007's deliberate "no uninstall-by-command in v1" and was not what
  the reported drift needed — the user had *deleted* the file, i.e. the `missing`
  case D39 already handles. Left as future work if demand appears; nothing here
  precludes it.
- **Keep the unconditional revert but block `reject` after it.** Rejected: it
  spreads the invariant across two commands' state machines (revert would have to
  mark the proposal so reject could refuse), where D39 keeps one honest predicate
  — "is the artifact still what we recorded?" — enforced at the one write path.
- **Delete/uninstall the artifact inside revert.** Rejected: revert is a
  store-record correction; making it mutate the user's files would be the
  destructive un-accept under a name that promises the opposite, and would
  collide with the standing "don't delete — resolve on read" lesson from the
  Slice B review.
