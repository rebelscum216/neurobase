# Architecture Decision Records

An ADR captures **one consequential decision**: the context, the choice, and its
consequences. They are how Neurobase keeps a durable, greppable trail of *why* — so
a future contributor (or agent) doesn't re-litigate a settled question.

## When to write one

- A **spike outcome** (S1–S6 in the [build plan](../neurobase-build-plan.md) §5) —
  the plan requires each spike to be "written up as `docs/adr/000x-*.md`."
- A **change to the locked decision table** (D1–D13, build-plan §3).
- Any other consequential architectural or contract choice made during the build.

Routine implementation choices don't need an ADR — use a [working note](../notes/README.md)
for investigation, and reserve ADRs for decisions someone might later question.

## How

1. Copy [`0000-template.md`](0000-template.md) to `NNNN-short-slug.md` — `NNNN` is
   the next zero-padded number, `short-slug` is kebab-case
   (e.g. `0003-codex-injection-fallback.md`).
2. Fill it in. Keep it short; link the spike note, the spec section, or the decision
   ID it resolves.
3. An ADR is **immutable once `Accepted`.** To change a decision, write a new ADR
   that supersedes the old one (set the old one's status to `Superseded by NNNN`).

## Status values

`Proposed` · `Accepted` · `Superseded by NNNN` · `Rejected`

## Index

| # | Title | Status | Resolves |
|---|---|---|---|
| [0001](0001-codex-capture-wiring.md) | Codex capture wiring — turn-completion event name + notify payload | Accepted | S1 |
| [0002](0002-claude-cli-json-reliability.md) | `claude -p` JSON contract reliability for the curator | Accepted | S5 |
| [0003](0003-hook-latency-budget.md) | Hook latency budget | Accepted | S6 |
| [0004](0004-codex-injection-fallback.md) | Codex injection — hook `additionalContext` doesn't reach the model | Superseded by 0005 | S2 |
| [0005](0005-codex-injection-confirmed.md) | Codex injection — `additionalContext` does reach the model (corrects 0004) | Accepted | S2 |

All four Phase 0 spikes (S1, S2, S5, S6) are now closed. ADR-0004 was caught
and reversed by Codex's own review of this repo's relay process — see
ADR-0005 for the correction.
