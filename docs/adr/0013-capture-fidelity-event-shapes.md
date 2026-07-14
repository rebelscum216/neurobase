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

The richer skim also forces two rules about how captured content reaches the
body, because **captured content is untrusted markdown** and the curator reads
the body's structure. Prompts (now 1,200 chars), highlights, and subagent
reports (1,500 chars) are routinely multi-line markdown; a live spike against
the largest local transcript produced a 20 KB raw carrying **seven forged `##`
sections**, including a content-supplied `## Final assistant summary`.

1. **Escape both heading syntaxes, then indent bullet continuations** (spec §4).
   Indentation alone is insufficient — CommonMark parses a heading indented up
   to three spaces — so escaping is the load-bearing half. Escaping only ATX
   (`#`) is *also* insufficient: Setext underlines (`===` / `---`) promote the
   line above them to a heading retroactively, and nothing about the promoted
   line looks like a heading. Both are escaped. This applies to section bodies
   and to the hook-supplied `reason`, not just bullets: §5's IDE context block
   is the sharpest case, since it precedes `## Prompts` and a heading forged
   there shadows every section after it.
2. **Redact each captured value *before* rendering it, not the finished
   document** (spec §10). D13's env rule is line-anchored, so a `"- "` bullet
   prefix shifts the text off column 0 and shields it: `bullet()` then
   `redact()` left `- API_TOKEN=<secret> uv run pytest` in `raw/` **unredacted**.
   The command digest introduced here is precisely the channel where that shape
   lives, which made a latent D13 gap newly exploitable.

Fixing (2) surfaced a second gap in the D13 table itself, independent of this
branch: the env rule only ever matched a *line-initial* assignment, so
`export API_TOKEN=<secret>` — the single most common way a secret appears in a
shell command — was never redacted at all. Closing it took three attempts, and
the two failures are the instructive part:

- **Casing is not the lever.** The first attempt made the new rule
  case-sensitive to avoid swallowing `sort(key=…)`. That bought the false
  positive at the price of a false negative: `export api_token=<secret>` stayed
  exposed, and shell variable names are not required to be uppercase.
- **A keyword is not shell syntax, and one assignment is not a command.** The
  second attempt keyed on the keyword anywhere, and scrubbed the single
  assignment after it. It redacted ordinary prose ("we export api_token=x in the
  docs") and SQL, *and* still leaked the ordinary multi-assignment forms
  (`env PATH=/bin api_token=… pytest`, `env -u OLD api_token=… pytest`).

§10's rules are therefore keyed on **context and position**: a keyword in
*command position* (line start or after a shell separator) opens a segment, and
every assignment within that segment is scrubbed case-insensitively. Separately,
`redact_command` handles the channel we *know* is a shell command — §4's
activity digest — with no keyword required at all, which is what lets the global
table stay conservative. The line-anchored rule also now preserves the indent it
consumes, so redaction cannot reflow a body's structure.

D13 is also confirmed as a **whole-raw** guarantee, not body-only: scribes scrub
the informational frontmatter they write (`cwd`, `branch`), but never
`session_id`, which keys the raw filename and the §5 per-turn overwrite.

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
