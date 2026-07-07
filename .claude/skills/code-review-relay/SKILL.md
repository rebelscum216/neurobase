---
name: code-review-relay
description: Use when handing implementation work to Codex for independent review, or preparing/resolving a Claude↔Codex review handoff in the neurobase repo. Triggers include "hand this to codex", "prep for review", "code review relay", "ready for review", "package this for review", and resolving reviewer findings that came back from Codex.
---

# Code Review Relay — Author role (Claude)

You are the **Author** in the relay. The authoritative protocol, roles, and reviewer
checklist are in [`docs/code-review-relay.md`](../../../docs/code-review-relay.md) —
read it. This skill is the actionable summary of *your* half.

## Preparing a handoff

1. **Branch.** Make sure the work is committed to a **feature branch**, not `main`.
   Create one if needed.
2. **Write the baton.** Copy [`docs/reviews/TEMPLATE.md`](../../../docs/reviews/TEMPLATE.md)
   to `docs/reviews/<YYYY-MM-DD>-<slug>.md` and fill the **Brief**:
   intent · scope (branch + exact `git diff` range + key files) · focus areas ·
   known risks/tradeoffs · how to verify · out of scope. State the exact diff
   command (e.g. `git diff main...HEAD`).
3. **Hand off.** Set `status: awaiting-review`. Then tell the user, in plain terms,
   to switch to Codex and point it at the review file — you do **not** run the
   review yourself (independent reviewer is the whole point).

## Resolving findings (when it comes back)

For each entry under **Reviewer findings**: **fix it**, **push back** with a clear
reason, or **defer** to a tracked note/issue. Mark each finding
`resolved | wontfix | deferred` in the file. A `blocker`-severity spec violation is
not optional — fix it. If your changes are material, bump `status: awaiting-review`
and ask the user to relay again; otherwise set `status: approved` once only
`nit`/`deferred` items remain, and the branch is ready to merge.

## Don't

- Don't review your own work in the same breath — that's Codex's job.
- Don't invent findings or a verdict on the reviewer's behalf.
- Don't skip the branch/brief steps because a change "looks small."
