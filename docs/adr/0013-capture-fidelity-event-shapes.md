# ADR-0013: Capture-fidelity event shapes

- **Status:** Accepted
- **Date:** 2026-07-14
- **Resolves:** S-cf1, S-cf2, S-cf3
- **Supersedes:** none

## Context

Tier-1 capture fidelity depends on event shapes that were not covered by the
v0.1 sanitized fixtures. We inspected current local Claude and Codex JSONL
transcripts structurally, without printing message bodies or tool arguments.

Claude subagents currently use an assistant `tool_use` named `Agent` (the older
`Task` name remains safe to accept). Its `id` matches a later user
`tool_result.tool_use_id`; result content can be a string or an array of text
blocks. Edit/Write tools carry `input.file_path`, and Bash carries
`input.command`. A compaction summary is a user event with top-level
`isCompactSummary: true` and string message content.

Codex activity appears in `response_item` payloads. Calls are represented as
`function_call` or `custom_tool_call`, correlated to corresponding output items
by `call_id`. Current observed names include `exec_command`, `apply_patch`, and
collaboration calls. Unlike Claude's Agent result, a subagent's final report is
not one clean, stable result event suitable for Tier-1 capture.

## Decision

Claude's richer skim accepts both `Agent` and legacy `Task`, correlates their
results by tool-use id, extracts file and command activity from the verified
input keys, and treats compact summaries as assistant highlights rather than
typed prompts. Codex ships assistant highlights and the summary-selection fix
now. Codex activity extraction remains a follow-up because parsing command and
patch strings safely needs a separate bounded format contract; Codex subagent
reports remain deferred.

The richer skim also forces one body-format rule (spec §4): **every bullet
indents its continuation lines.** Prompts (now 1,200 chars), highlights, and
subagent reports (1,500 chars) are routinely multi-line markdown. Rendered as a
bare `- {text}`, the second line of a pasted stack trace or a markdown-formatted
assistant message lands at column 0 — so a `## Final assistant summary` inside
captured content becomes a real heading in the raw document, and the curator
reads that structure as if the scribe had written it. A live spike against the
largest local transcript reproduced exactly this: seven forged `##` sections in
one 20 KB raw. The two-space continuation indent keeps every value inside its
own list item.

## Consequences

The sanitized §11.1 fixture grows to cover Agent correlation, activity, and
compaction. Tier-1 capture remains deterministic and no-LLM. Supporting both
Claude tool names avoids coupling capture to a single CLI release. Codex
activity is feasible, but does not block the high-value highlights change.

ADR-0003's latency follow-up was re-measured against the largest local Claude
transcript available (8,924,926 bytes). Ten deterministic parse runs averaged
38.8 ms / peaked at 42.9 ms on the first measurement, and 51.0 ms / 59.1 ms on
an independent re-run by the Author on a loaded machine. The spread is machine
load, not input: both are an order of magnitude inside the 500 ms hook budget,
and the parse stays one pass over events already being read. That headroom — not
either point estimate — is the finding.

The same live spike wrote a real 20 KB raw (7.7% of one 256 KiB plan request),
consistent with the plan's ~30 KB worst-case estimate: richer capture does not
threaten the ADR-0012 budget at realistic session sizes.

## Alternatives considered

- **Implement only the proposed `Task` name** — rejected because it does not
  match the current live transcript.
- **Treat compact summaries as user prompts** — rejected because they are
  generated context, not text the user typed.
- **Parse every Codex response item immediately** — deferred until the output
  and truncation contract is explicit.
