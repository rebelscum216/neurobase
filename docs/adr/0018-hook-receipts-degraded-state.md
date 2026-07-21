# ADR-0018: Hook receipts and degraded-state reporting — make fail-soft visible

- **Status:** Proposed
- **Date:** 2026-07-21
- **Resolves:** hardening plan §10 ([plan draft](../notes/2026-07-21-hardening-plan-draft.md), [ratified decisions](../notes/2026-07-21-hardening-decisions.md)); Issue 4
- **Supersedes:** none — builds on the `StoreHandle` of [ADR-0015](0015-store-chokepoint-handle.md) and the schema-2 layout of [ADR-0016](0016-store-schema-2-project-records-profiles.md)

## Context

Hooks intentionally catch every error and exit zero (D12, spec §11) so a broken
capture or recall never breaks the user's agent session. The cost is that capture,
curation, and recall can be broken for a long time while the user assumes they work —
there is no signal at all. This is not hypothetical for this project: the 2026-07-17
runaway incident ran for days partly because failure states were invisible until
someone went looking.

This is the last of the three Phase-0 trust-boundary items (after store validation,
ADR-0015, and egress policy, ADR-0017). The plan (§10) frames two layers — receipts
+ detection, and degraded-state surfacing — and the ratified decisions (2026-07-21)
kept the deterministic core, added the AI diagnosis call, and deliberately chose
**minimal surfacing**: `status` only, nothing that forces attention or breaks CI.

## Decision

**D36 — Every hook attempt writes a bounded receipt; hooks still exit zero.** Each
hook event (`session-start`, `session-end`/`stop`, `notify`) appends one receipt to a
bounded ring:

```text
<root>/health/hook-events.jsonl
```

```json
{
  "event_id": "01J...",           // stable ULID (ADR-0016 D29)
  "event": "session-end",
  "agent": "codex",
  "agent_version": "...",
  "project": "neurobase",
  "started_at": "...", "finished_at": "...", "duration_ms": 81,
  "status": "capture-failed",
  "error_type": "rollout-not-found",   // normalized, never raw exception text
  "error_fingerprint": "sha256:...",   // hash of detail that may hold paths/content
  "bytes_written": 0
}
```

- **Exit-zero is unchanged** (the hard acceptance criterion): a receipt write is
  wrapped so that even a *failed* receipt write cannot raise out of the hook — it
  bumps a receipt-write-failure counter instead. The receipt is the *only* new I/O on
  the hook path and must stay within the ADR-0003 latency budget.
- **No raw exception text is stored** (§10.2.1): error types are normalized to a
  closed vocabulary and any detail that could carry a sensitive path or content is
  hashed, not written — consistent with redaction-before-write everywhere else.
- Retention is bounded by max-events / max-bytes / max-age (`[health]` config),
  rotated atomically. Append-only, ULID-keyed (ADR-0016 D29), so a future sync could
  aggregate receipts across machines.

**D37 — Deterministic health checks + degraded state.** A single
`<root>/health/state.json` holds overall + per-subsystem health, computed by
**deterministic** checks over the receipt ring (§17.4 forbids a model deciding a
failure threshold):

- N consecutive capture failures for an agent; no successful capture despite recent
  hook events; no successful recall in N session starts; growing unconsumed-raw
  backlog; repeated curator `partial`/`error`; last-success older than a threshold;
  hook shim path no longer exists.
- A subsystem (`capture:codex`, `recall:claude`, …) flips to `degraded`/`failed`
  with `consecutive_failures`, `last_success_at`, `last_failure_at`; a configured
  number of successes clears it.

**D38 — Surface through `status` only; add an egress-gated `doctor --explain`.** The
ratified surfacing choice is deliberately quiet:

- **`neurobase status`** reports last successful capture/recall per agent, unconsumed
  backlog, and any degraded subsystem. This is the one passive surface.
- **`neurobase doctor --explain`** may send a **sanitized aggregate** (check results,
  error *types*, agent versions, config summary — no raw content) to a configured
  model for an ordered remediation plan. This is the one AI call in the health
  subsystem; it routes through `authorize_egress` with purpose `health-diagnose`
  ([ADR-0017](0017-egress-policy-gate.md) D33) and is **explicit-command-only**
  (never hook-triggered, §23.1). Deterministic checks remain the source of truth; the
  model only explains them.
- **Explicitly NOT shipping** (ratified): `doctor` does **not** exit nonzero on
  degraded health (keeps it non-scriptable-as-failure, won't break CI); **no**
  operational warning is injected into the next session's recall; **no** OS
  notification. Degraded state is *visible if you look* — via `status` or
  `doctor --explain` — but nothing forces attention.

## Consequences

- **A broken adapter becomes discoverable without verbose logs**, which is the whole
  acceptance criterion (§10.3.3): `status` shows it, `doctor --explain` diagnoses it,
  and hooks still always exit zero.
- **The AI diagnosis is the health subsystem's first egress.** It is why ADR-0017's
  `health-diagnose` purpose exists and why the gate had to land first — a `local-only`
  project's `doctor --explain` returns the raw deterministic report (gate denies the
  model call) rather than failing.
- **Minimal surfacing is a real trade-off, chosen on purpose.** Because `doctor`
  keeps a zero exit on degraded health, CI/automation cannot treat "degraded" as a
  failure without parsing `status`; and because nothing is injected or notified, a
  silent-but-recorded failure still requires the user to look. Accepted: the plan's
  goal is *visibility*, not nagging, and the louder surfaces can be added later
  without reworking the receipts/state substrate.
- **Bounded by construction.** Receipts are a size/age-capped ring; `state.json` is a
  fixed-size summary. No health file grows without bound (§10.3.3).
- **Spec appendix** updates: §11 records that every hook attempt leaves a bounded,
  redaction-safe receipt while preserving exit-zero; §10 documents the `health/`
  layout, the deterministic checks, and the `status` / `doctor --explain` surfaces
  (and the deliberate absence of nonzero-exit / injection / notification). **ADR
  proposes; spec is law.**
- **Observability groundwork for later phases.** The receipt ring and `state.json`
  are the first of the local ledgers §21 envisions (`judgments/calls.jsonl`,
  `facts/events.jsonl`, `evaluations/results.jsonl` arrive with their phases), all
  sharing the ULID + append-only conventions ADR-0016 D29 sets.

## Alternatives considered

- **Ship the louder surfaces too** (nonzero `doctor` exit, injected warning, OS
  notification — Issue 4 Solution B, full form) — rejected (ratified): each adds
  surface (CI-breakage risk, recall-budget spend, macOS-only notification plumbing)
  for attention-forcing the user did not ask for. The substrate supports adding them
  later with no rework.
- **Deterministic-only, no `doctor --explain`** — rejected (ratified): the ratified
  call was to ship the AI diagnosis now, egress-gated. It is explicit-command-only and
  gated, so it adds real remediation help without putting a model on the hook path.
- **Store raw exception text in receipts for richer diagnosis** — rejected: exception
  strings routinely carry absolute paths and sometimes content. Normalized error
  types + a hashed fingerprint give aggregation and dedup without a new egress-of-
  sensitive-data vector, consistent with the project's redaction posture.
- **One combined health file instead of ring + state** — rejected: the receipt ring
  is append-only evidence (mergeable, ULID-keyed); `state.json` is a recomputable
  summary. Collapsing them loses the append-only audit trail or unbounds the summary.
