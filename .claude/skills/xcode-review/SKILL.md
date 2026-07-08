---
name: xcode-review
description: Use when handing implementation work to Codex for independent review, or preparing/resolving a Claude↔Codex review handoff in the neurobase repo. Triggers include "/xcode-review", "hand this to codex", "prep for review", "code review relay", "ready for review", "package this for review", "give me the codex prompt", and resolving reviewer findings that came back from Codex.
---

# Code Review Relay — Author role (Claude)

You are the **Author** in the relay. The authoritative protocol, roles, and reviewer
checklist are in [`docs/code-review-relay.md`](../../../docs/code-review-relay.md) —
read it. This skill is the actionable summary of *your* half, and it also emits the
Codex hand-off prompt for you (so one trigger both arms the baton **and** gives you
the block to paste).

## Preparing a handoff

1. **Branch.** Make sure the work is committed to a **feature branch**, not `main`.
   Create one if needed.
2. **Write the baton.** Copy [`docs/reviews/TEMPLATE.md`](../../../docs/reviews/TEMPLATE.md)
   to `docs/reviews/<YYYY-MM-DD>-<slug>.md` and fill the **Brief**:
   intent · scope (branch + exact `git diff` range + key files) · focus areas ·
   known risks/tradeoffs · how to verify · out of scope. State the exact diff
   command (e.g. `git diff main...HEAD`), and set the frontmatter `slug`, `branch`,
   `diff`, and `created` fields.
3. **Set `status: awaiting-review`.**
4. **Emit the Codex prompt — this is a required part of the hand-off, not optional.**
   In the *same* turn, output the copy-paste block for Codex as a single fenced
   code block, inline, using the template below. Fill the bracketed values from the
   baton's frontmatter (`slug` → the review filename, `branch`, `diff`). Do not
   restate the checklist or protocol — Codex reads those from
   `docs/code-review-relay.md` and `AGENTS.md` itself.
   - **Hard rule:** the fenced block containing the actual prompt MUST be present in
     the message. "Prompt's ready" or any reference to a prompt without the block
     immediately above it is a failed turn. If you're about to write the closing
     line and the block isn't already above it, stop and emit the block first.

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
5. **Arm the auto-watch (Phase W)**, then end the turn telling the user, in plain
   terms, to switch to Codex and paste the block above — you do **not** run the
   review yourself (independent reviewer is the whole point). The closing line is
   only valid once the block from step 4 is actually present above it.

## Phase W — the auto-watch (hands-free pickup)

Run this as a **backgrounded** Bash command (`run_in_background: true`) with the
baton path substituted. It watches the baton's own `status:` field — Codex flips it
to `changes-requested`/`approved` when it finishes — so there's no mailbox and
nothing outside the repo. When it exits, the harness re-enters this session;
proceed to "Resolving findings".

```bash
BATON="docs/reviews/<YYYY-MM-DD>-<slug>.md"
DEADLINE=$(( $(date +%s) + 1800 ))   # 30-min timeout
until grep -qE '^status:[[:space:]]*(changes-requested|approved)' "$BATON"; do
  [ "$(date +%s)" -ge "$DEADLINE" ] && { echo "REVIEW_TIMEOUT $BATON"; exit 0; }
  sleep 5
done
sleep 2   # settle guard, in case Codex is still writing findings
echo "REVIEW_READY $BATON (status=$(grep -m1 '^status:' "$BATON"))"
```

- On `REVIEW_READY` → go to "Resolving findings".
- On `REVIEW_TIMEOUT` → tell the user no review landed in 30 min; offer to re-arm.
- **Manual fallback** (if Codex forgets to set `status`, or the watch misbehaves):
  the user says "findings are ready" and you read the baton directly. The relay
  still works — the watch is a convenience, not a dependency.

## Resolving findings (when it comes back)

Read the baton. Present the verdict and each finding under **Reviewer findings**
ordered by severity. For each: **fix it**, **push back** with a clear reason, or
**defer** to a tracked note/issue. Mark each finding `resolved | wontfix | deferred`
in the file. A `blocker`-severity spec violation is not optional — fix it.

If your changes are material, bump `status: awaiting-review`, then **return to step
4** — re-emit the Codex prompt and re-arm the watch for the next round (land fixes
as a **follow-up commit**, never `amend`/`rebase` the commit under review mid-loop).
Otherwise set `status: approved` once only `nit`/`deferred` items remain, and the
branch is ready to merge.

## Don't

- Don't review your own work in the same breath — that's Codex's job.
- Don't invent findings or a verdict on the reviewer's behalf.
- Don't skip the branch/brief steps because a change "looks small."
- Don't end a hand-off turn without the fenced Codex prompt block in it.
