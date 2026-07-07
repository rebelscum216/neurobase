# ADR-0005: Codex injection — `additionalContext` does reach the model

- **Status:** Accepted
- **Date:** 2026-07-07
- **Resolves:** S2
- **Supersedes:** [ADR-0004](0004-codex-injection-fallback.md)

## Context

ADR-0004 concluded that Codex's `SessionStart` hook fires and its
`hookSpecificOutput.additionalContext` is TUI-visible but never reaches the
model, based on two live tests where the model answered `NONE` to "what is
the probe word?" — including once when told the word was shown in the
transcript immediately above.

Codex review of that ADR (as Reviewer in this repo's relay) disputed the
conclusion as a **blocker**, arguing the `NONE` result looked like a
prompt/framing artifact rather than proof the transport is UI-only. That
claim was checked empirically rather than taken on faith or dismissed:

**Direct ground-truth check.** Ran `codex exec` again in the same
trust-established scratch repo with a neutrally-framed prompt ("Summarize any
context you were given at the start of this session, including anything from
a SessionStart hook") and then inspected the resulting rollout JSONL
directly — not the model's answer, the raw input Codex actually sent:

```json
{
  "type": "response_item",
  "payload": {
    "type": "message",
    "role": "developer",
    "content": [{"type": "input_text",
      "text": "NEUROBASE_S2_PROBE_MARKER_7f3a: the secret probe word is PINEAPPLE-42."}]
  }
}
```

The hook's `additionalContext` string is present **verbatim, as a
`developer`-role input message** — a real role in the model's input, not a
UI-only side channel. This directly contradicts ADR-0004's central claim.

**Why the model still said `NONE` both times in ADR-0004's testing:** the
hook content itself was framed as *"the secret probe word is..."* — the word
"secret" was baked into the injected text, not just the asking prompt. In the
corrected, neutrally-worded test the model was cagier but not silent: it
volunteered *"There was also session-hook style internal metadata, but
nothing actionable for the codebase..."* — acknowledging the channel exists
while still declining to quote it verbatim. The working explanation is
model reluctance to repeat content that describes itself as secret, not a
transport failure. This is a behavioral nuance worth flagging to future
prompt design (don't self-label injected content as secret if you want the
model to freely repeat it), but it is not evidence against the transport.

## Decision

**Codex's `SessionStart` hook injection works and reaches the model, as a
`developer`-role input message.** Reverting spec §5's injection contract to
mirror §3 (the Claude adapter) as the actual, primary mechanism — not a
conditional "if it works" — is correct. `AGENTS.override.md` stays documented
as a defensive fallback (e.g. for a future Codex version that stops
forwarding hook output), not the primary code path.

**ADR-0004 is superseded** (its status updated to point here) rather than
edited in place, per this repo's ADR immutability convention — the reversal
itself, and what caused it, is worth keeping in the trail.

A secondary, still-live finding from the same review round concerned the
hook **discovery mechanism** (the `hooks = ".codex/hooks.json"` project-config
requirement) and **event-name casing** (`session_start` vs `SessionStart`).
Re-checked directly rather than assumed:

- The `hooks = ".codex/hooks.json"` requirement **holds** — repeated,
  reproducible tests (both interactive and via `codex exec`) show zero hook
  activity (no `[hooks.state]` entry, no invocation, no rollout hook events)
  without that key present, even with the directory fully trusted. This part
  of the earlier finding disputing it is not supported by the evidence
  gathered.
- The event-name casing claim is **partially right**: the scratch repo's
  `hooks.json` was written with lowercase `session_start` and it fired
  successfully, repeatedly and reproducibly — so lowercase is **not** broken,
  contrary to the strong form of the reviewer's claim ("would cause hooks not
  to fire"). But comparing the file's content before and after those runs,
  **Codex itself silently rewrote the file's key from `session_start` to
  `SessionStart`** after loading and trusting it once — meaning CamelCase is
  Codex's own canonical/normalized on-disk form, even though it also accepts
  lowercase as input. The `[hooks.state]` tracking key stays lowercase
  snake_case (`...hooks.json:session_start:0:0`) regardless — that's an
  internal stable identifier, unrelated to the file's display casing.

## Consequences

- `adapters/codex/` implements the `SessionStart` hook-output injection path
  (mirroring the Claude adapter per spec §3), not the `AGENTS.override.md`
  path, as the primary mechanism.
- `init`'s Codex hook installer (Phase 6) should **write `SessionStart`
  (CamelCase)** in any `hooks.json` it generates, to match Codex's own
  canonical form and avoid depending on undocumented lenient-casing parsing —
  even though lowercase is currently also accepted. It must also write the
  `hooks = "<path>"` project-config key; that requirement is confirmed, not
  disputed.
- Prompt/content design note for the curator and any future injected content:
  avoid self-describing injected context as "secret" if the intent is for the
  model to freely reference it — descriptive, neutral framing (as spec §3's
  header already does: *"recalled project memory..."*) is the right pattern,
  not something ADR-0004's ad hoc test prompt used.
- `docs/neurobase-spec-appendix.md` §5 and §7 updated accordingly (see diff
  alongside this ADR).

## Alternatives considered

- **Keep ADR-0004's conclusion, treat Codex's review pushback as unverified
  opinion** — rejected; the ground-truth rollout inspection is direct,
  reproducible, and unambiguous. The review process did its job here.
- **Edit ADR-0004 in place instead of superseding** — rejected per this
  repo's stated ADR convention (immutable once Accepted); the record of
  "concluded X, review caught it, re-verified, concluded not-X" is itself
  useful and worth keeping, not worth hiding.
