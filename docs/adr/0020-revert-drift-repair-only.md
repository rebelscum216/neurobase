# ADR-0020: `recommend revert` is drift repair only, not an un-accept

- **Status:** Accepted
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

- **D43 — Revert is defined only when no live managed artifact remains.** The
  question `revert` asks is whether reverting would orphan a still-installed
  Neurobase artifact — *not* whether the target file's bytes survived verbatim.
  Those are different questions, and conflating them was the round-2 defect
  (review F1): a rule lives as a fenced block *inside* an AGENTS.md/CLAUDE.md, so
  a user edit *outside* the block changes the whole-file hash while the managed
  rule stays live. Keying revert on that hash let `accept → edit-outside →
  revert → reject` orphan the live rule. So revert is gated on **liveness**,
  judged by the slug-scoped artifact re-derived from the proposal:
  `proposals.artifact_state` returns **live** (the rule's opening sentinel is
  present, or a `SKILL.md` exists at the canonical path — whatever
  else changed around it), **missing** (no candidate target exists), **orphaned**
  (a candidate exists but no longer holds our block/skill), or **unresolvable**
  (target can't be re-derived, *or* a candidate can't be read). Revert is allowed
  **only** on `missing` or `orphaned`; `live` and `unresolvable` are refused
  (the first would orphan a live artifact, the second cannot *prove* one is gone).

  Liveness detection is built to **fail closed**, in three ways:
  1. **Every plausible location is checked**, not just the one a fresh render
     would target: `emitters.candidate_target_paths` returns the recorded
     `installed_path` (never ignored when non-null) *plus* the re-derived target
     under **every** registered project root. `_project_root` picks `roots[0]` for
     rendering, but a repo moved and re-registered under the same slug leaves
     `[old, new]` — concluding "gone" from the stale first root would orphan the
     live artifact at the new one.
  2. **Skill liveness is existence, not frontmatter parsing.** A file at the
     canonical `<...>/skills/<slug>/SKILL.md` counts as live; its frontmatter is
     never parsed (the classifier still opens and decodes candidates, since a read
     failure is itself a verdict — see 3). This is the round-4 correction, and it is deliberate: three
     successive reviews each found a different *text representation* of a still
     owned, still installed skill (CRLF, then CR-only, a UTF-8 BOM, whitespace
     after the fence) that preserved the ownership values while defeating the
     parser — every one of them making a live skill look gone and orphanable.
     Parsing was the wrong question. Existence has no such surface. The cost is
     that a skill the user *replaced* with their own content is non-revertable
     until they delete the file — the same "remove it by hand first" rule revert
     already documents. A **rule**'s liveness is the presence of its opening
     **sentinel**, `<!-- neurobase:rule:<slug>` (`emitters.rule_sentinel`): the
     identity token is defined once, tolerates whitespace after `<!--` and
     *anything* after the slug, refuses to match a longer slug, and can never
     match the closing `<!-- /neurobase:rule:…` marker. Matching the whole
     generated opening comment — parenthetical included — was the round-5 defect:
     changing `generated by` to `generated  by` left the sentinel and the rule
     body intact yet read as gone. The same sentinel locates the block for the
     *write*, so accept and liveness can never disagree, and a block whose
     parenthetical was hand-edited is replaced rather than duplicated.
  3. **Any read failure is classified, not raised.** A candidate that cannot be
     read — a directory, a permissions error (`OSError`), or bytes that are not
     valid UTF-8 (`UnicodeError`) — yields `unresolvable`. The rule is "could not
     read it", not "raised a particular exception type".

  `_owned_skill` survives for the accept flow's `foreign` warning and D44's
  never-claim-a-foreign-file exclusion, where a false negative is merely
  conservative rather than orphaning. It now parses by logical lines (BOM
  skipped, all newline forms, whitespace-tolerant fences) so it is correct in its
  own right — but nothing about the orphan invariant depends on it.
  The artifact on disk is still never touched on any path — to retire a live
  artifact the user removes the managed block/skill by hand first, which puts the
  proposal in the `orphaned`/`missing` state revert then repairs. This closes F1
  structurally (a live artifact is non-revertable, so `accept → revert → reject`
  cannot start, *however* the surrounding file was edited) and, with D44, F2.
  Survival (§12.9) keeps its own separate whole-file-hash comparison — the two
  questions are answered by two functions, never one shared classifier (round-2
  review P2 was the regression from sharing them).

- **D44 — A matching-but-unrecorded artifact is recordable without a write.**
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

  Because this path writes no artifact, it can only be honest if **both sides of
  the preview still hold at the moment it records**, and both are re-verified at
  that mutation boundary (`StalePreviewError`):
  - the **target bytes** must still equal the previewed render, else the recorded
    hash would disagree with disk (review P1-DATA-INTEGRITY-001); and
  - the **proposal must still render identically** — reloaded and re-rendered, its
    path, target, `after` bytes and ownership outcome all compared. Deliberately a
    re-render, not a field comparison: `emitters.prepare` consumes the draft,
    `type`, `target`, `project`, `candidate_type` *and* the active redaction
    config, so any chosen subset leaves a way to change the render while the
    compared fields match — round 4 reproduced exactly that by changing only
    `candidate_type` (which feeds the skill description fallback), a change a
    concurrent `recommend run` refresh makes routinely.

  The validated document is then **bound to the write** via
  `accept_proposal(expect=…)`: that function does its own independent reload, so
  without the binding the version that becomes `accepted` need not be the version
  just checked. It is a defensive equality guard, not a lock — it closes the
  reachable check-then-write gap but cannot serialize two concurrent writers;
  full serialization awaits the per-project write lock (ADR-0017, separate
  branch). Any drift records nothing: CLI exits 1, the web POST returns 409.

- **F4 — the record.** This ADR is that required record: revert adds a public
  command, a `reverted` ledger event (now carrying the liveness `reason`), and an
  `accepted → proposed` transition that ADR-0007 D14/D15 did not contemplate.
  Those decisions said an accepted proposal is never metadata-transitioned while
  its artifact stays in place; that remains true — D43 permits the transition
  **only** once no live managed artifact remains. This ADR revises that clause to
  the drift-repair carve-out rather than reversing it; ADR-0007 stays otherwise
  intact, and v1 still has no uninstall-by-command.

## Consequences

- **The `accept → revert → reject` backdoor is closed at its root**: the sequence
  cannot begin, because revert refuses a *live* managed artifact. The strength of
  that claim rests on how liveness is detected, and this ADR took four review
  rounds to get there — the honest summary is that liveness is now decided by
  signals with no representation surface (a file existing; a marker substring)
  rather than by parsing, and that every ambiguity resolves to refusal. Verified
  by real CLI regressions for: an edit outside the block, an edit inside a
  still-present block, an accepted/null-path record with a live target, an owned
  skill under CRLF / CR-only / BOM / fence-whitespace, a moved+re-registered
  project, an unreadable and a non-UTF-8 target, and genuinely missing/orphaned
  artifacts. What is *not* claimed: proof of exhaustiveness over all inputs, or
  safety under concurrent writers (see D44's note on the write lock).
- **Liveness is deliberately conservative.** Every ambiguity resolves to "refuse":
  an unreadable candidate, an unresolvable project, an unknown proposal type. The
  cost is that a genuinely-gone artifact can occasionally need the user to fix the
  registry before revert will repair the record; the benefit is that no ambiguity
  can ever orphan live behavior. Given revert exists precisely to make the store
  honest, refusing when we cannot tell is the only defensible default.
- **Retiring a live accepted proposal is a two-step, user-visible act:** remove
  the managed block/skill by hand, then `revert` (now `orphaned`/`missing`) and
  `reject`. Both refusal messages say so. This is the deliberate cost of not
  shipping an uninstall-by-command in v1.
- **`revert` (liveness) and `metrics` survival (verbatim bytes) are separate by
  design.** `proposals.artifact_state` classifies liveness by the rule sentinel /
  a skill file's existence;
  `metrics._survival_one` keeps its own existence-first whole-file-hash check.
  Round 2 tried to share one classifier and broke the legacy existence-only
  survival fallback (review P2-REGRESSION-002); they are two questions and stay
  two functions. `record_acceptance` re-reads its target at the write boundary so
  a stale preview can't stamp a hash that disagrees with disk (P1-DATA-INTEGRITY-001).
- **Spec §12.7 and `docs/known-gaps.md` (G2 → `fixed`) are updated** to the
  liveness boundary and the installed-but-unrecorded accept path. A real
  un-accept (uninstall through backup/consent) remains deferred, exactly as
  ADR-0007 left it — this ADR does not add one. `docs/how-it-works.md`'s
  `recommend_accept` code-map entry is **not** updated here: it was already stale
  from the earlier install-service (D-1) refactor, and is tracked as a separate
  follow-up rather than partially patched under this ADR.
- **Round-3 tests drive the real install service**, not `accept_proposal`
  directly — the gap that let F1/F2 pass round 1. The `accept → revert →
  {reject, re-accept}` flows are pinned through the CLI and the web UI POST.

## Alternatives considered

- **Also add a real un-accept** (uninstall a healthy artifact via
  diff/consent/backup). Rejected for this ADR: it is a larger change that
  reverses ADR-0007's deliberate "no uninstall-by-command in v1" and was not what
  the reported drift needed — the user had *deleted* the file, i.e. the `missing`
  case D43 already handles. Left as future work if demand appears; nothing here
  precludes it.
- **Keep the unconditional revert but block `reject` after it.** Rejected: it
  spreads the invariant across two commands' state machines (revert would have to
  mark the proposal so reject could refuse), where D43 keeps one honest predicate
  — "is a live managed artifact still on disk?" — enforced at the one write path.
- **Gate revert on a whole-file hash (the round-2 attempt).** Rejected after
  review F1: a rule block is a fenced region inside a possibly-shared file, so
  "the file changed" is not "the managed artifact is gone." Liveness by
  the sentinel-or-existence signal is the only one that actually answers the
  orphaning question.
- **Delete/uninstall the artifact inside revert.** Rejected: revert is a
  store-record correction; making it mutate the user's files would be the
  destructive un-accept under a name that promises the opposite, and would
  collide with the standing "don't delete — resolve on read" lesson from the
  Slice B review.
