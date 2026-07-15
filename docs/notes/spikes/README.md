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

## Capture-fidelity spikes (Part II plan §C)

De-risking spikes for the capture-fidelity work
(`neurobase-capture-fidelity-plan.md`, Part II §C). S-cf1–S-cf3 were resolved
structurally in [ADR-0013](../../adr/0013-capture-fidelity-event-shapes.md);
S-cf4/S-cf5 gate the Phase C (Tier-2 distill) contracts.

| ID | Question | Exit criterion | Status |
|---|---|---|---|
| S-cf1 | Claude `tool_use`/`tool_result` correlation + Task-result shape | Fixture §11.1 extended | ✅ Closed → ADR-0013 |
| S-cf2 | Compaction-summary event shape | Decide harvest-as-highlight | ✅ Closed → ADR-0013 |
| S-cf3 | Codex `response_item` structure — activity/subagent parity | Parity feasible? | ✅ Closed (deferred) → ADR-0013 |
| S-cf4 | Real `claude -p` argv ceiling on macOS | Measured ceiling + margin for 300 K | ✅ Closed → [S-cf4](S-cf4-argv-ceiling.md) |
| S-cf5 | Distill quality: one real transcript through DISTILL_SYSTEM v1 | Digest-vs-skim recorded; prompt gaps enumerated | ✅ Closed → [S-cf5](S-cf5-distill-quality.md) |

S-cf4/S-cf5 feed the Phase C ADR (A2 frontmatter + A3 curate sequence). S-cf5
found `DISTILL_SYSTEM` v1 needs digest-size enforcement, an untrusted-data fence
against transcript-borne role hijacking, and output-shape validation before the
contract freezes.

## Naming

`SN-short-slug.md` — e.g. `S1-codex-capture-wiring.md`, `S5-claude-p-json-reliability.md`.

See the ADRs for closed spike outcomes. S3 remains open until a true fresh
macOS user account or clean container run validates the published-style
one-liner.
