# ADR-0007: Recommender proposal and activation model

- **Status:** Accepted
- **Date:** 2026-07-08
- **Resolves:** Phase 8 recommender contract
- **Supersedes:** none

## Context

The build plan already names the recommender as Neurobase's novel contribution:
mine the local cross-agent memory corpus and propose durable `SKILL.md`,
`AGENTS.md`, and `CLAUDE.md` artifacts. The next design question is how those
recommendations should be represented, reviewed, accepted, and later turned on
or off, especially because a future local UI should expose the same workflow.

The constraints are Neurobase's core constraints: local-first, no telemetry,
human consent before editing agent behavior, git-diffable state, and standard
agent artifacts rather than a bespoke runtime lock-in.

## Decision

Recommendations are stored as markdown proposal files under
`<root>/proposals/<slug>.md`, with machine-readable frontmatter and
human-readable evidence/body sections. User decisions and metrics are appended
to `<root>/recommender/ledger.jsonl`. Accepting a proposal emits a managed
standard artifact: either a `SKILL.md` folder or a fenced rule block in
`AGENTS.md`/`CLAUDE.md`. Accepted proposals can be enabled or disabled. Skills
are disabled by moving the Neurobase-owned skill directory into
`.neurobase-disabled/`; rules are disabled by removing only the matching owned
fenced block. The future UI must use the same proposal store, ledger, and
consent-first mutation paths as the CLI.

## Consequences

The recommender has a durable local data model before the UI exists. Users can
inspect proposals in any editor, diff them in git, and recover history from the
ledger. The UI can be a client of the same model rather than a separate product
surface. Disable/re-enable semantics are explicit, testable, and reversible.

Implementation must enforce ownership carefully: Neurobase may only move skill
directories or edit rule blocks it created from accepted proposals. The proposal
schema now needs tests for status transitions, `enabled` state, `emitted_path`,
`disabled_path`, ledger events, and idempotent enable/disable behavior.

## Alternatives considered

- **Auto-install recommendations** — rejected. It violates the consent-first
  principle and would make the recommender feel like hidden agent mutation.
- **Keep recommendations only in a SQLite database** — rejected for v1. It would
  be less inspectable and would not match Neurobase's markdown-first store.
- **Treat the future UI as the source of truth** — rejected. The CLI and MCP
  surfaces need the model first, and the UI should not gain a separate mutation
  path.
- **Disable skills with a frontmatter flag inside `SKILL.md`** — rejected for
  v1 because agent loaders may ignore that flag. Moving the owned directory out
  of the active skills path is explicit and loader-independent.
