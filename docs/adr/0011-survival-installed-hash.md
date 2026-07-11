# ADR-0011: Ledger `accepted` events carry an `installed_hash`

- **Status:** Accepted
- **Date:** 2026-07-11
- **Resolves:** Phase 8 Workstream H survival's "modified since acceptance" check
- **Supersedes:** none

## Context

§12.9's survival metric must tell an untouched accepted artifact apart from
one that was hand-edited or deleted after acceptance. Existence alone answers
"is it there," not "is it what we installed" — and there was no content
signature anywhere in the proposal/ledger schema to compare against. Adding a
new required field to proposal frontmatter was rejected: that §12.1 schema had
just been hardened through five adversarial review rounds on the immediately
prior branch (phase-8-workstream-e-ranker-proposals), and a new required key
would force every existing proposal file and the schema validator to change
in lockstep for a concern that belongs to the ledger's append-only history,
not the proposal's mutable current state.

## Decision

`accept_proposal` accepts an additive, optional `installed_hash: str | None =
None` parameter. When given, it is written as an optional field on the
ledger's `accepted` event (§12.2) — sha256 of the artifact's exact bytes at
the moment of acceptance. `recommend accept` (the CLI) always computes and
passes this hash from the just-rendered artifact body. `status --recommender`'s
survival check (§12.9) uses the *most recent* `accepted` event for a slug
(accept is idempotent and re-runnable per §12.7, so there may be more than
one): past `survival_window_days`, a present artifact whose current bytes no
longer hash to the stored `installed_hash` is `not_survived`; one whose bytes
match is `survived`. An `accepted` event with no `installed_hash` (written
before this feature existed) falls back to existence-only checking — it can
confirm the artifact is present, but not whether it was later modified. This
is a documented, permanent limitation for pre-ADR-0011 acceptances, not a bug.

## Consequences

- No change to proposal frontmatter or its §12.1 validator — the hash lives
  entirely in the ledger, which already carries optional per-event fields
  (`reason`, `candidate_type`, `target`).
- A pre-existing `ledger.jsonl` with hash-less `accepted` lines keeps working;
  those proposals simply get a weaker (existence-only) survival signal.
- `docs/neurobase-spec-appendix.md` §12.2's ledger field table and §12.9's
  survival paragraph both cross-reference this ADR.

## Alternatives considered

- **Add a hash field to proposal frontmatter (§12.1)** — rejected: would
  reopen a schema just hardened through five review rounds, for a concern
  (was this artifact modified since a past event) that is inherently about
  ledger history, not current proposal state.
- **Re-derive "modified" by diffing against the proposal's stored draft** —
  rejected: the draft can itself be edited after acceptance via `recommend
  edit` without re-accepting, so the draft is not a stable reference point for
  "what did we actually write to disk."
- **Skip modified-detection entirely (existence-only survival for everyone)**
  — considered, and remains the honest fallback for pre-ADR-0011 data, but
  rejected as the *design target*: it would silently report `survived` for an
  artifact a user had already hand-edited into something unrecognizable.
