---
name: codexprompt
description: Draft the short prompt the user pastes into Codex to kick off a code-review-relay review. Use when the user says "give me the codex prompt", "/codexprompt", or otherwise asks to initiate/hand off a review to Codex once a review baton is ready.
---

# Codex handoff prompt

You are drafting the **Router's** copy-paste message for step 2 of the relay
("Relay (Router): switch to Codex; point it at the review file") — see
[`docs/code-review-relay.md`](../../../docs/code-review-relay.md) for the full
protocol. This skill does not re-explain the protocol to Codex; Codex reads
that file itself. Your only job is to point it at the right place.

## What to gather

1. Current branch (`git branch --show-current`).
2. The review baton for this branch: look under `docs/reviews/` for a file
   whose frontmatter `branch:` matches. If more than one exists, use the most
   recently modified.
3. From that baton's frontmatter: `slug`, `diff`, `status`.

**If no baton exists for the current branch's work:** say so plainly and stop
— don't fabricate one. Writing the brief is the `code-review-relay` skill's
job ("Author prepares"), not this one's. Point the user at that skill instead.

**If the baton's `status` is already `awaiting-review` from a prior round**
(i.e. this is a re-relay after resolving findings), say that plainly too —
the prompt is the same either way.

## Output

Emit **only** a single fenced code block containing the prompt — nothing
before or after but a one-line lead-in ("Paste this into Codex:"). Keep it
short: role line, the file/branch/diff pointers, and a one-line procedural
reminder. Do not restate the checklist or the full protocol — Codex gets that
from `docs/code-review-relay.md` and `AGENTS.md` directly.

Template:

```
You're the Reviewer in this repo's Claude<->Codex code-review relay (see
AGENTS.md "Code review relay" section and docs/code-review-relay.md for the
full protocol).

Review file: docs/reviews/<slug-file>.md
Branch: <branch>
Diff: <diff command from frontmatter>

Read the brief in that file, then run the diff and review the actual changes
— verify its claims rather than trusting them. Append your findings under
"Reviewer findings" in that same file, end with a verdict, and set the file's
status field to match. Don't fix anything.
```

Fill in the bracketed values from what you gathered. Nothing else.
