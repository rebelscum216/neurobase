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
| [0006](0006-codex-hook-command-tokenization-and-trust.md) | Codex hooks — string-with-args `command`, stdin JSON payload, trust re-fires on edit | Accepted | Phase 5-init |
| [0007](0007-recommender-contract.md) | Phase 8 recommender — proposal/ledger shape, ranking, and consent-first review (D14–D21) | Accepted | Phase 8 recommender |
| [0008](0008-phase-7-mcp-server.md) | Phase 7 MCP server — shape + decisions (D-a…D-e, user-directed pin, SDK pin) | Accepted | Phase 7 MCP server |
| [0010](0010-proposal-draft-boundary.md) | Proposal bodies use a managed artifact-draft region | Accepted | Phase 8 Workstream F/G |
| [0011](0011-survival-installed-hash.md) | Ledger `accepted` events carry an `installed_hash` for survival's modified-detection | Accepted | Phase 8 Workstream H |
| [0012](0012-curator-plan-payload-batching.md) | Curator plan payloads use byte-budgeted sequential batches | Accepted | S-cf4 / D22 |
| [0013](0013-capture-fidelity-event-shapes.md) | Live capture event shapes for richer deterministic skims | Accepted | S-cf1 / S-cf2 / S-cf3 |
| [0014](0014-transcript-distill-curation.md) | Transcript-distill curation — Tier-2 capture fidelity (raw pointer + curate-time distill) | Accepted | S-cf5 / D15 / D16 / D17 |
| [0015](0015-store-chokepoint-handle.md) | Store chokepoint — a validated `StoreHandle` every path must obtain (D23–D26) | Accepted | G1 / D11 / spec §10 |
| [0016](0016-store-schema-2-project-records-profiles.md) | Store schema 2 — project-record registry, logical profiles, forward-compatible identifiers (D27–D31) | Accepted | Hardening §12/§16/§25 |
| [0017](0017-egress-policy-gate.md) | Central egress policy — one `authorize_egress()` gate every brain call passes (D32–D35) | Accepted | Hardening §9 |
| [0018](0018-hook-receipts-degraded-state.md) | Hook receipts and degraded-state reporting — make fail-soft visible (D36–D38) | Accepted | Hardening §10 |
| [0015](0015-webui-surface.md) | Web UI — loopback server as a peer presentation layer (spec §14) | Accepted | Web UI Phase 1 |

ADRs 0016–0018 are the three **Phase-0** decisions promoted from the
[hardening plan](../notes/2026-07-21-hardening-plan-draft.md) (per the
[ratified decisions](../notes/2026-07-21-hardening-decisions.md)); they were
**Accepted** via a 3-round Claude↔Codex relay (baton:
[`docs/reviews/2026-07-21-phase-0-hardening-adrs.md`](../reviews/2026-07-21-phase-0-hardening-adrs.md)).

All four Phase 0 spikes (S1, S2, S5, S6) are now closed. ADR-0004 was caught
and reversed by Codex's own review of this repo's relay process — see
ADR-0005 for the correction. ADR-0006 records the follow-on command-tokenization
and trust-gate spike that unblocked the `init --agent codex` installer.
