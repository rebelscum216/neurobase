# Code Review Relay — Claude ⇄ Codex

A defined process for handing implementation work from an **Author** agent (Claude)
to an independent **Reviewer** agent (Codex) and back, with a durable, greppable
trail. This file is the **single source of truth** for the process; the Claude
`xcode-review` skill and the [AGENTS.md](../AGENTS.md) Reviewer section are thin
pointers to it, so the protocol never drifts between agents.

## Why relay at all

An independent reviewer catches what the author is blind to — the value comes
precisely from the author and reviewer being **different agents with different
context**. Don't let one agent play both roles; that defeats the purpose.

## Roles

| Role | Who | Does |
|---|---|---|
| **Author** | Claude | Implements the work, packages a review request, resolves findings. |
| **Reviewer** | Codex | Independently reviews the *diff*, reports ranked findings, gives a verdict. **Does not fix.** |
| **Router** | You (human) | Carries the baton between the two CLIs; owns the final merge decision. |

## The baton

One markdown file per review at **`docs/reviews/<YYYY-MM-DD>-<slug>.md`**, created
from [`docs/reviews/TEMPLATE.md`](reviews/TEMPLATE.md). It holds the Author's brief
and the Reviewer's findings in one place, tracked by a `status` field. Because it's
a file, both agents see it, `git` records it, and (once Neurobase dogfoods this repo)
the recommender can learn from the accumulated reviews.

## Protocol

1. **Author prepares (Claude).**
   - Commit the work to a **feature branch** — never review on `main`.
   - Create the review file from the template; fill the **brief**: intent, scope
     (branch + exact `git diff` range + key files), focus areas, known
     risks/tradeoffs, how to verify, and what's explicitly out of scope.
   - State the exact diff command the reviewer should run
     (e.g. `git diff main...HEAD`).
   - Set `status: awaiting-review`. Tell the Router to relay to Codex.

2. **Relay (Router).** Switch to Codex; point it at the review file.

3. **Reviewer reviews (Codex).**
   - Read the brief, then **run the diff and review the actual code — not the
     description.** Verify the brief's claims rather than trusting them.
   - Assess against the [checklist](#reviewer-checklist-this-repo).
   - Append findings under **Reviewer findings** — each: `severity`
     (`blocker` | `major` | `minor` | `nit`), `file:line`, the issue, and a
     suggested direction (not a patch).
   - End with a **verdict** (`approve` | `changes-requested`) + a one-line
     rationale. Set `status` to match.

4. **Return (Router).** Switch back to Claude.

5. **Author resolves (Claude).** For each finding: **fix**, **push back** with a
   reason, or **defer** to a tracked note/issue. Mark each finding
   `resolved` | `wontfix` | `deferred` in the file. If changes are material, bump
   `status: awaiting-review` and re-relay (step 2).

6. **Close.** When the verdict is `approve` (or only `nit`/`deferred` items remain),
   set `status: approved` and merge the branch. Record any deferrals as issues or
   working notes so they aren't lost.

Status flow: `draft → awaiting-review ⇄ changes-requested → approved`.

## Reviewer checklist (this repo)

- **Correctness** — does it do what the brief claims? Edge cases, error paths.
- **Spec adherence** — does it violate any `MUST` in
  [neurobase-spec-appendix.md](neurobase-spec-appendix.md)? **That is a blocker.**
  Are the tuned defaults (§8) and on-disk formats (§10) respected?
- **Tests** — do they enforce the contracts the change touches? Added/updated?
- **Security & safety** — redaction intact; no secrets reach `raw/`; hooks stay
  deterministic and **exit 0** on every path.
- **Simplicity & reuse** — dead code, duplication, over-engineering.
- **Provenance discipline** — no code lifted from the prior private implementation;
  the build stays spec-derived (AGENTS.md principle #2).

## Conventions

- **Author ≠ Reviewer** — keep them as separate CLI sessions.
- **Review the diff, not the brief** — the brief orients; the code is the truth.
- **A blocking spec violation always wins** over "it works."
- The relay is agent-symmetric in principle; today it's specialized Claude→Codex
  because that's the working pattern. To reverse direction, the roles simply swap.
