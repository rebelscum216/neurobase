# Neurobase docs

The map of everything written about Neurobase. Start with
[AGENTS.md](../AGENTS.md) at the repo root for the operating guide; this index is
the deeper table of contents.

## Canonical documents (the founding bundle)

These three are self-contained: together they contain everything needed to build,
with no external code or private repos required.

| Document | What it is | Status |
|---|---|---|
| [neurobase-build-plan.md](neurobase-build-plan.md) | The phased build plan (Phases 0–9 + backlog), locked decisions D1–D13, spikes S1–S6, risk register. **Follow it.** | Living |
| [neurobase-spec-appendix.md](neurobase-spec-appendix.md) | Authoritative behavioral contracts for every subsystem — store, curator, scribes, recall, linkify, hooks, on-disk formats, captured fixtures. **This is the law.** | Living |
| [neurobase-architecture-options.md](neurobase-architecture-options.md) | The researched rationale behind every locked decision (12-agent research sweep). Consult for the "why." | Frozen (discovery) |

**Reading order:** build-plan → spec-appendix → architecture-options.

## Process

| Document | What it is |
|---|---|
| [code-review-relay.md](code-review-relay.md) | The defined **Claude ⇄ Codex code-review handoff** process — roles, protocol, reviewer checklist. Single source of truth; the Claude `code-review-relay` skill and the AGENTS.md Reviewer section point to it. |
| [reviews/](reviews/README.md) | The trail of review handoffs — one baton file per review, from [`TEMPLATE.md`](reviews/TEMPLATE.md). |

## Decision & spike quick-reference

- **Locked decisions** live in build-plan §3 (table D1–D13). Any change to them
  requires an [ADR](adr/README.md).
- **Spikes** (de-risking experiments) live in build-plan §5 (table S1–S6). Each
  spike's outcome is recorded as an ADR and, if it's investigative, a working note.

| Spike | Question | Status (per plan) |
|---|---|---|
| S1 | Codex capture wiring (which hook, payload, rollout path) | Narrowed |
| S2 | Codex injection (`additionalContext` vs `AGENTS.override.md` fallback) | Open |
| S3 | Clean-machine install (`uv tool install`, cold-start < 60s) | Open |
| S4 | PyPI/GitHub naming | ✅ Closed → `neurobase-cli` |
| S5 | `claude -p` JSON contract for the curator | Narrowed |
| S6 | Hook latency budget (< 500ms combined warm) | Open |

## Directories

| Path | Purpose |
|---|---|
| [adr/](adr/README.md) | Architecture Decision Records — spike outcomes, decision-table changes, any consequential choice. Numbered, immutable once accepted. |
| [notes/](notes/README.md) | Working notes — scratch thinking, investigation logs, running scratchpads. Not contracts. |
| [notes/spikes/](notes/spikes/) | Raw spike write-ups feeding the ADRs. |

## Conventions

- Prose wraps ~80 columns (git-diff-friendly), matching the founding docs.
- The spec appendix is the single source of truth for contracts and tuned defaults;
  when code and spec would diverge, change the spec first (and log an ADR).
- Dates in notes and ADRs are absolute (`2026-07-07`), never "yesterday."
