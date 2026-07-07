# Spike write-ups

Raw investigation notes for the de-risking spikes defined in
[build-plan §5](../../neurobase-build-plan.md) (table S1–S6). Each spike has an
**exit criterion**; a write-up here records the method, what was observed, and
whether the criterion was met — then feeds a numbered [ADR](../../adr/README.md)
that states the resulting decision.

## The spikes

| ID | Question | Exit criterion | Status (per plan) |
|---|---|---|---|
| S1 | Codex capture wiring: which hook fires, payload shape, rollout path | One correct raw file per real session | Narrowed — turn-completion event name + notify payload remain |
| S2 | Codex injection: does `session_start` accept `additionalContext`? | Injection works, or fall back to `AGENTS.override.md` block | Open |
| S3 | Clean-machine install | One-liner works; cold-start < 60s | Open |
| S4 | PyPI/GitHub naming | — | ✅ Closed → `neurobase-cli` |
| S5 | `claude -p` JSON contract for the curator | Parse success ≥ 9/10 with lenient parser | Narrowed — 10-run reliability check remains |
| S6 | Hook latency budget | Start+end overhead < 500ms warm | Open |

## Naming

`SN-short-slug.md` — e.g. `S1-codex-capture-wiring.md`, `S5-claude-p-json-reliability.md`.

_(No spike write-ups yet — Phase 0 runs S1, S2, S5, S6.)_
