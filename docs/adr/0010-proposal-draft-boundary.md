# ADR-0010: Proposal bodies use a managed artifact-draft region

- **Status:** Accepted
- **Date:** 2026-07-10
- **Resolves:** Phase 8 Workstream F/G proposal edit and emitter boundary
- **Supersedes:** none

## Context

Proposal bodies contain both human review prose and the artifact draft that
`recommend accept` installs. The original §12 example displayed the draft as a
blockquote but did not define how edit or emitters could identify it safely.
Treating the whole body as the artifact would install rationale and evidence.

## Decision

Every proposal body contains exactly one draft region bounded by
`<!-- neurobase:draft:start -->` and `<!-- neurobase:draft:end -->`.
`recommend edit` replaces only the region contents; emitters consume only those
contents. Missing, reversed, or duplicate markers fail closed. An edited draft
is redacted before persistence and produces exactly one `edited` ledger event.

## Consequences

- Draft replacement preserves the surrounding review prose byte-for-byte.
- Emitters avoid brittle parsing of headings or blockquotes.
- Damaged markers block edit and accept until the proposal is repaired.
- Pre-Workstream-F proposal fixtures must be regenerated with markers.

## Alternatives considered

- **Use the whole body** — would install review metadata as agent instructions.
- **Parse a blockquote under a heading** — ambiguous after free-form edits.
- **Store draft markdown in YAML** — awkward for multiline human editing.
