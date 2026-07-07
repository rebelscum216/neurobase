# ADR-0001: Codex capture wiring — turn-completion event name + notify payload

- **Status:** Accepted
- **Date:** 2026-07-07
- **Resolves:** S1
- **Supersedes:** none

## Context

> **Note (added after [ADR-0005](0005-codex-injection-confirmed.md)):** the
> `session_start`/`stop` casing below reflects what this ADR's live test
> observed at the time — lowercase, and it fired correctly. ADR-0005 later
> found Codex silently rewrites this to `SessionStart`/`Stop` on disk after
> loading it once, and that CamelCase is the form the installer should write.
> Both statements are true; see ADR-0005 and spec §7 for the full picture.

Spec §5 (Codex scribe contract) and §11.2/§11.4 already had the Codex rollout
JSONL structure and hook event names (`session_start`/`stop`, live-verified per
§7) pinned, but two things remained open per the build-plan §5 spike table:

1. The turn-completion `event_msg`'s literal `payload.type` string (§11.2 had
   it as a placeholder: `"<turn-completion — literal name = S1>"`).
2. The `notify` fallback's argv[1] JSON payload fields (§11.4 was
   "research-reported, NOT live-verified").

Exit criterion: one raw file per real session, correct prompts/summary/meta,
with the per-turn firing absorbed by the session-keyed overwrite (already
designed into §5 independent of these two unknowns).

## Decision

Both unknowns are now live-verified against `codex-cli 0.142.5`:

**Turn-completion event.** An `event_msg` with `payload.type == "task_complete"`
fires at the end of a turn, carrying `turn_id`, `last_agent_message`,
`completed_at`, `duration_ms`, `time_to_first_token_ms`. (A paired
`task_started` `event_msg` — `turn_id`, `started_at`, `model_context_window`,
`collaboration_mode_kind` — fires at turn start; not needed by the scribe but
useful if hook-latency instrumentation wants a turn-duration cross-check.)
Verified by running `codex exec "reply with exactly: ok"` and inspecting the
resulting rollout at
`~/.codex/sessions/2026/07/07/rollout-2026-07-07T14-02-47-*.jsonl`.

**Notify payload.** Verified by overriding `notify` for a single invocation
only (`codex exec -c 'notify=["<capture-script>"]' "..."` — no edit to the
user's persisted `~/.codex/config.toml`) and inspecting the script's captured
argv. The payload is delivered as **argv[1]**, a JSON string, with no stdin
body:

```json
{"type":"agent-turn-complete","thread-id":"<uuid>","turn-id":"<uuid>",
 "cwd":"<path>","client":"codex_exec","input-messages":["<prompt>"],
 "last-assistant-message":"<reply>"}
```

This confirms spec §11.4's expected field names (`type`, thread/turn id,
`input-messages`, `last-assistant-message`) and adds `cwd` and `client`.
Critically, **no rollout/transcript path is present** — this confirms §5's
rollout-discovery-by-glob (newest `rollout-*.jsonl` by mtime, cross-checked
against `session_meta.session_id`/`id`) is required whenever `notify` is the
active fallback, not an edge case to skip.

The primary hooks.json (`session_start`/`stop`) wiring stays the recommended
path per §7; `notify` remains the documented legacy fallback.

## Consequences

- `docs/neurobase-spec-appendix.md` §11.2 and §11.4 updated in place to mark
  both fixtures verified with the concrete values above (see diff alongside
  this ADR).
- `adapters/codex/scribe.py` can special-case `payload.type == "task_complete"`
  directly instead of guessing across candidate names.
- `adapters/codex/` notify-path handler can parse the argv[1] JSON payload with
  a known, closed field set instead of a defensive/partial parser.

## Alternatives considered

- **Rely on a path carried in the notify payload** — rejected; live-verified to
  be absent. Rollout discovery-by-glob is mandatory for the notify path, per
  §5.
