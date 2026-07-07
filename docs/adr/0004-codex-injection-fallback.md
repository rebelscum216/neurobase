# ADR-0004: Codex injection — hook `additionalContext` doesn't reach the model

- **Status:** Accepted
- **Date:** 2026-07-07
- **Resolves:** S2
- **Supersedes:** none

## Context

Spec §5 left Codex's injection mechanism conditional: mirror the Claude
adapter (a `SessionStart` hook emitting
`{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}`
on stdout, per §3) *if* Codex actually forwards that into the model's context;
else fall back to a managed fenced block in repo-root `AGENTS.override.md`.
Exit criterion: determine which.

Getting the hook to fire at all took two extra discoveries beyond what the
build plan anticipated, both worth recording alongside the answer:

1. A project-scoped `<repo>/.codex/hooks.json` is **not** auto-discovered by
   convention (contrary to the spec's prior assumption). It only loads once
   the project's config table explicitly points at it:
   ```toml
   [projects."<repo-path>"]
   trust_level = "trusted"
   hooks = ".codex/hooks.json"
   ```
   Without the `hooks = ` key, Codex never registers the hook — no
   `[hooks.state]` entry appears, no trust prompt, no invocation, nothing.
2. Once wired, the hook is invoked via **stdin JSON**, not argv — closely
   mirroring the Claude Code shape:
   ```json
   {"session_id":"<uuid>","transcript_path":"<rollout path>",
    "cwd":"<path>","hook_event_name":"SessionStart","model":"gpt-5.5",
    "permission_mode":"default","source":"startup"}
   ```
   `transcript_path` here is the rollout JSONL path directly — useful, though
   not required now that S1 already established rollout-discovery-by-glob.

## Decision

**The hook fires and Codex parses its `hookSpecificOutput.additionalContext`
correctly enough to render it in the interactive TUI's transcript — but it is
never forwarded into the model's actual input.** Live-verified 2026-07-07,
interactively, in a trusted+hook-wired scratch repo:

- The TUI displayed a `SessionStart hook (completed)` entry showing
  `hook context: NEUROBASE_S2_PROBE_MARKER_7f3a: the secret probe word is
  PINEAPPLE-42.` — proof the hook ran and its output was parsed.
- Asked directly *"what is the probe word?"* the model answered `NONE`.
- Asked again, explicitly referencing *"the SessionStart hook context shown
  above"* — still `NONE`.

That second question is the decisive one: the content is visibly present in
the human-facing transcript, immediately above the model's answer, and the
model still has no access to it. This rules out "the model just didn't think
to mention it" — the hook's output is UI-only, not part of the model's
context window, on `codex-cli 0.142.5`.

**Codex's injection therefore uses spec §5's fallback exclusively:** a fenced
managed block in repo-root `AGENTS.override.md` (same fence discipline as
`core/linkify.py`'s blocks), rewritten by the recall step every run, added to
`.git/info/exclude` per §5. This is not a fallback-of-last-resort anymore —
it's simply how Codex injection works, full stop, until a future Codex
version changes this behavior.

## Consequences

- `adapters/codex/` never needs a `SessionStart`-hook-based injection code
  path; only the `AGENTS.override.md` writer/rewriter is needed. Simpler than
  the two-path design spec §5 originally allowed for.
- The `session_start` hook is still needed for **capture** wiring (S1) — this
  ADR only concerns the injection side. Nothing about S1 changes.
- `docs/neurobase-spec-appendix.md` §5 updated to state the `AGENTS.override.md`
  path as the actual (not conditional) mechanism, and to record the
  `hooks = "<path>"` project-config requirement for any Codex hook (capture
  or otherwise) to be discovered at all — this affects `init`'s Codex hook
  installer (Phase 6), which must write that key, not just drop the
  `hooks.json` file.
- Should a future Codex release start forwarding `additionalContext` to the
  model, this ADR would need a successor revisiting whether to switch back —
  not urgent, since the override-file path works and is simpler to reason
  about besides.

## Alternatives considered

- **Keep polling/retrying the hook path** (different field name, different
  event, etc.) — rejected for now; the evidence (TUI shows it, model doesn't)
  is specific enough to the *transport*, not the *format*, that further
  format tweaking is unlikely to change the outcome. Revisit only if a Codex
  changelog claims this now works.
