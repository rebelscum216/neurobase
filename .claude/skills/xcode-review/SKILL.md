---
name: xcode-review
description: Use when handing implementation work to Codex for independent code review, or preparing/resolving a Claude⇄Codex review handoff in any repo. Triggers include "/xcode-review", "hand this to codex", "relay to codex", "prep for review", "code review relay", "ready for review", "package this for review", "give me the codex prompt", and resolving reviewer findings that came back from Codex. This is the cross-agent review relay — it is not about Apple Xcode, iOS builds, or Swift tooling.
---

# Code Review Relay — Author role (Claude)

You are the **Author** in the relay: you implement, package a review request, and
resolve findings. Codex is the **Reviewer** and does not fix. The human is the
**Router**, carrying the baton between the two CLIs.

## Resolve the protocol first

The relay adapts to the repo. Before doing anything else:

1. **Protocol.** If the repo has `docs/code-review-relay.md`, that is the source of
   truth — read it, and let it override anything here. Otherwise use the bundled
   [code-review-relay.md](code-review-relay.md) next to this file.
2. **Template.** Use the repo's `docs/reviews/TEMPLATE.md` if present, otherwise copy
   the bundled [TEMPLATE.md](TEMPLATE.md) into `docs/reviews/` and use that. Create
   `docs/reviews/` if it doesn't exist.
3. **Git is required.** If `git rev-parse --is-inside-work-tree` fails, stop and offer
   to `git init` (with `-b main`) and commit a baseline. The relay reviews a *diff*;
   with no version control there is no change to review, only a snapshot, and that is
   a materially weaker review. Say so rather than silently degrading.
4. **Repo rules.** Find the hard rules this repo holds above "it works" — an invariants
   table in `AGENTS.md`, a spec with `MUST` clauses, a build plan whose contract
   sections govern changes. Name them in the brief and in the Codex prompt so the
   reviewer treats a violation as a **blocker** rather than a matter of taste.

## Preparing a handoff

1. **Branch.** Make sure the work is committed to a **feature branch**, not `main`.
   Create one if needed.
2. **Write the baton.** Copy the template to `docs/reviews/<YYYY-MM-DD>-<slug>.md` and
   fill the **Brief**: intent · scope (branch + exact `git diff` range + key files) ·
   focus areas · known risks/tradeoffs · how to verify · out of scope. State the exact
   diff command (e.g. `git diff main...HEAD`), and set the frontmatter `slug`,
   `branch`, `diff`, and `created` fields.
3. **Set `status: awaiting-review`.**
4. **Emit the Codex prompt — this is a required part of the hand-off, not optional.**
   In the *same* turn, output the copy-paste block for Codex as a single fenced code
   block, inline, using the template below. Fill the bracketed values from the baton's
   frontmatter. Do not restate the checklist or protocol — Codex reads those from the
   protocol doc and its own `AGENTS.md` Reviewer section.
   - **Hard rule:** the fenced block containing the actual prompt MUST be present in
     the message. "Prompt's ready" or any reference to a prompt without the block
     immediately above it is a failed turn. If you're about to write the closing line
     and the block isn't already above it, stop and emit the block first.

   ```
   You're the Reviewer in this repo's Claude<->Codex code-review relay (see your
   global ~/.codex/AGENTS.md Reviewer section, plus <protocol-doc> for the full
   protocol).

   Review file: docs/reviews/<slug-file>.md
   Branch: <branch>
   Diff: <diff command from frontmatter>
   Rules that outrank "it works" in this repo: <repo rule docs, or "none">

   Read the brief in that file, then run the diff and review the actual changes
   — verify its claims rather than trusting them. Append your findings under
   "Reviewer findings" in that same file, end with a verdict, and set the file's
   status field to match. Don't fix anything.
   ```
5. **Relay it. Semi-auto (Phase R) is the default** — run it unless the user has
   asked for manual, or the preflight in Phase R says the `codex` CLI is missing.

   - **Semi-auto relay (default).** Run **Phase R**. Do not arm Phase W. The step-4
     block is still required in your message — it is the record of what the reviewer
     was asked, and the user's fallback if the invocation fails.
   - **Manual relay (fallback).** Only when the user asked for it, or the CLI
     preflight failed. Arm the auto-watch (Phase W), then end the turn telling the
     user, in plain terms, to switch to Codex and paste the block above.

   **Manual relay does not start anything.** Nothing invokes Codex until a human
   pastes that block into another CLI. If the user walks away, the review never
   begins and the watch just times out looking at a baton nobody touched — which
   reads, hours later, like a reviewer that hung. That failure is the reason
   semi-auto is the default. When you do fall back to manual, say plainly in the
   closing line that no review starts until they paste it.

   You do **not** run the review yourself in either mode — an independent reviewer is
   the whole point. The closing line is only valid once the block from step 4 is
   actually present above it.

## Phase W — the auto-watch (manual-relay fallback only)

Use this **only** in manual relay — when the user asked for it, or the Phase R
preflight found no `codex` CLI. Never alongside Phase R.

Note what this watch can and cannot tell you: it observes the *baton*, not Codex. A
`REVIEW_TIMEOUT` means nobody wrote findings — which is equally consistent with a
reviewer that stalled and a reviewer that was never started. Don't report a timeout
as a stuck or slow review. Check whether the review ever began: the baton's mtime
against when you wrote it, and whether `.git/codex-review-last.txt` exists.

Run this as a **backgrounded** Bash command (`run_in_background: true`) with the baton
path substituted. It watches the baton's own `status:` field — Codex flips it to
`changes-requested`/`approved` when it finishes — so there's no mailbox and nothing
outside the repo. When it exits, the harness re-enters this session; proceed to
"Resolving findings".

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
- On `REVIEW_TIMEOUT` → do not say the review stalled until you've checked whether it
  ever started (above). If the baton is untouched, say plainly that nothing picked it
  up, and offer to install the CLI and switch to Phase R rather than re-arming a watch
  that will time out again.
- **Manual fallback** (if Codex forgets to set `status`, or the watch misbehaves): the
  user says "findings are ready" and you read the baton directly. The relay still
  works — the watch is a convenience, not a dependency.

## Phase R — semi-auto relay (invoke Codex directly) — **the default**

Removes the human from the *relay* step, not from the *fix* decision. Their only
intervention becomes "go" or "pause" once findings are in.

Independence is preserved: `codex exec` is a genuinely separate agent and session.
What changes is who carries the baton, not who reviews.

### Preflight — run this first, every time

```bash
command -v codex >/dev/null 2>&1 && codex --version || echo "NO_CODEX_CLI"
```

`NO_CODEX_CLI` means Phase R cannot run. Do **not** emit a `codex exec` call that
will fail. Fall back to manual relay (Phase W), and tell the user in the same turn
that semi-auto is unavailable and why, with the one-line fix:

```bash
npm install -g @openai/codex
```

Having the Codex desktop app installed does **not** satisfy this — a running
`ChatGPT.app/Contents/Resources/codex` process is the GUI app, not the CLI, and
`pgrep codex` will match it and mislead you. `command -v` is the only check that
answers the right question.

Run this **backgrounded** (`run_in_background: true`) — reviews take minutes, and the
process exiting is what re-enters this session:

```bash
codex exec -C "$(git rev-parse --show-toplevel)" \
  -s workspace-write \
  -o .git/codex-review-last.txt \
  "$(cat <<'PROMPT'
<paste the exact step-4 prompt block here>
PROMPT
)" < /dev/null
```

- **`< /dev/null` is required, not optional.** Backgrounded without it, `codex
  exec` blocks on "Reading additional input from stdin…" and hangs forever: the
  process stays alive at 0% CPU, writes nothing, never touches the baton, and
  never creates the `-o` file. It looks exactly like a slow review. This cost a
  52-minute stall on 2026-07-29 before anyone checked.
- **A live-looking process is not progress.** If a review seems slow, check
  whether it is actually working before reporting it as such: CPU time, whether
  the `-o` file exists yet, and the baton's mtime. All three are zero/unchanged
  for a stdin hang.

- `-s workspace-write` is required — the Reviewer writes its findings into the baton.
  It also means Codex *can* edit source despite being told not to, which is why the
  integrity check below is mandatory rather than optional.
- **Do not also arm Phase W.** Process exit is the signal; two signals is noise.
- `-o` keeps Codex's final message as an out-of-band record, outside the baton.
- Codex inherits its own model and effort from `~/.codex/config.toml`. Don't override
  unless the user asks.

### Integrity check — run this before reading findings

```bash
git status --porcelain
```

**Only the baton may be modified.** If any other path changed, the Reviewer broke the
don't-fix rule: `git checkout --` those paths, tell the user plainly that it happened,
and treat the findings with corresponding suspicion. Never silently keep a reviewer's
edits to source — the Author resolves findings, and a review that quietly rewrote the
code under review is not a review.

Then confirm the baton's `status` actually flipped. If Codex exited without setting
it, the review is incomplete: say so and offer to re-run. Do not infer a verdict from
a half-written findings section.

### The gate

Present the verdict and every finding ranked by severity, each with your proposed
disposition (fix · push back · defer) and a one-line reason. **Then stop.** Do not
touch code until the user says go; they may scope it ("go except F2"). On pause, leave
the branch and the baton exactly as they are.

On go: apply the fixes, add regression tests per the discipline below, commit as a
**follow-up** commit, bump `status: awaiting-review`, re-emit the step-4 block, and
re-run Phase R for the next round. Loop until the verdict is `approve`.

## Resolving findings (when it comes back)

Read the baton. Present the verdict and each finding under **Reviewer findings**
ordered by severity. For each: **fix it**, **push back** with a clear reason, or
**defer** to a tracked note/issue. Mark each finding `resolved | wontfix | deferred`
in the file. A `blocker`-severity spec violation is not optional — fix it.

When a finding names a concrete repro, don't just patch and move on: write a
regression test for it, then temporarily `git stash` the fix and confirm the test
actually fails against the pre-fix code before restoring the fix and committing.
An "obvious" fix that was never seen to fail may be closing nothing.

If your changes are material, bump `status: awaiting-review`, then **return to step 4**
— re-emit the Codex prompt and relay it again the same way you did the first round
(Phase R by default; Phase W only if you fell back to manual). Land fixes as a
**follow-up commit**, never `amend`/`rebase` the commit under review mid-loop.
Otherwise set `status: approved` once only `nit`/`deferred` items remain, and the
branch is ready to merge.

## Don't

- Don't review your own work in the same breath — that's Codex's job.
- Don't invent findings or a verdict on the reviewer's behalf.
- Don't skip the branch/brief steps because a change "looks small."
- Don't end a hand-off turn without the fenced Codex prompt block in it.
