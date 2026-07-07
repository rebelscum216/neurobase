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

_(none yet — the first ADRs will come from Phase 0 spikes S1, S2, S5, S6.)_

| # | Title | Status | Resolves |
|---|---|---|---|
| — | — | — | — |
