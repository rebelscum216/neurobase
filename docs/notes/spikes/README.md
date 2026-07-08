# Spike write-ups

Raw investigation notes for the de-risking spikes defined in
[build-plan §5](../../neurobase-build-plan.md) (table S1–S6). Each spike has an
**exit criterion**; a write-up here records the method, what was observed, and
whether the criterion was met — then feeds a numbered [ADR](../../adr/README.md)
that states the resulting decision.

## The spikes

| ID | Question | Exit criterion | Status (per plan) |
|---|---|---|---|
| S1 | Codex capture wiring: which hook fires, payload shape, rollout path | One correct raw file per real session | ✅ Closed → ADR-0001 |
| S2 | Codex injection: does `session_start` accept `additionalContext`? | Injection works, or fall back to `AGENTS.override.md` block | ✅ Closed → ADR-0005 |
| S3 | Clean-machine install | One-liner works; cold-start < 60s | Open; [2026-07-08 local isolated smoke recorded](2026-07-08-s3-install-smoke.md) |
| S4 | PyPI/GitHub naming | — | ✅ Closed → `neurobase-cli` |
| S5 | `claude -p` JSON contract for the curator | Parse success ≥ 9/10 with lenient parser | ✅ Closed → ADR-0002 |
| S6 | Hook latency budget | Start+end overhead < 500ms warm | ✅ Closed → ADR-0003 |

## Naming

`SN-short-slug.md` — e.g. `S1-codex-capture-wiring.md`, `S5-claude-p-json-reliability.md`.

See the ADRs for closed spike outcomes. S3 remains open until a true fresh
macOS user account or clean container run validates the published-style
one-liner.
