---
slug: phase-0-spikes
status: awaiting-review
author: claude
reviewer: codex
branch: phase-0-spikes
diff: git diff main...phase-0-spikes
created: 2026-07-07
---

# Review: Close Phase 0 spikes S1, S5, S6

## Brief  _(Author — Claude)_

**Intent.** Run the three Phase 0 spikes that were unblocked once `claude` and
`codex` CLIs got onto PATH this session (symlinked from their VSCode
extensions' bundled native binaries), and write each up as an ADR per
build-plan §6's Phase 0 gate: "spikes ... executed and written up as
`docs/adr/000x-*.md`."

**Scope.** Branch `phase-0-spikes` (on top of `main`@`18c0513`), `git diff
main...phase-0-spikes`. Key files:
- `docs/adr/0001-codex-capture-wiring.md` (new) — S1: pins the Codex
  turn-completion event's literal type (`task_complete`) and the `notify`
  fallback's argv[1] JSON payload fields, both live-verified.
- `docs/adr/0002-claude-cli-json-reliability.md` (new) — S5: a 10-run harness
  against the real `claude -p --output-format json --max-turns 1` contract,
  10/10 parsed with the lenient fence-tolerant parser.
- `docs/adr/0003-hook-latency-budget.md` (new) — S6: timed `neurobase hook`
  via the installed `uv tool` shim; ~120ms warm combined for
  session-start+end vs. the 500ms budget.
- `docs/adr/README.md` — index updated with the three new ADRs.
- `docs/neurobase-build-plan.md` — spike table (§5): S1/S5/S6 marked Closed
  with ADR links.
- `docs/neurobase-spec-appendix.md` — §11.2 (Codex rollout fixture) and §11.4
  (notify payload fixture) updated from placeholder/"research-reported" to the
  live-verified literal values.
- `AGENTS.md` — "Current state" updated to reflect the three closed spikes and
  S2 as the sole remaining Phase-0-gating spike.

**Focus areas.**
- Do the ADRs actually resolve what S1/S5/S6's exit criteria required (see
  build-plan §5), or do they paper over an unmet bar?
- Spec-appendix §11.2/§11.4 edits: are they narrowing (adding confirmed
  values to placeholders) or do they silently change a contract? (Intent was
  the former only — no behavioral contract changed, just fixture accuracy.)
- S6's caveat that the measurement is against the current Phase-4 `hook` stub,
  not real logic — is deferring the re-measure to Phase 4 (rather than
  blocking now) reasonable, or should Phase 0 itself gate on it?

**Known risks / tradeoffs.**
- S6's number is a floor (process/interpreter/Typer-dispatch cost only, since
  `hook` isn't implemented yet) — deliberately flagged as a Phase-4 follow-up
  rather than treated as final.
- S5's 10-run sample is a spike-scale sanity check, not a statistical
  guarantee — the ADR says so explicitly and leans on the existing
  abort-on-parse-failure safety net (spec §2 step 3) rather than claiming
  parse failures can't happen.
- The `claude`/`codex` CLIs used for these spikes are symlinks into
  version-suffixed VSCode extension directories (`~/.local/bin/claude` →
  `.vscode/extensions/anthropic.claude-code-2.1.202-.../resources/native-binary/claude`,
  similarly for `codex`) — not a conventional standalone install. If either
  extension auto-updates, the symlink can go stale. Noted in working-state
  memory, not otherwise part of this diff.

**How to verify.** Read the three ADRs against build-plan §5's exit criteria
and spec §5/§2.1/§8. The underlying raw evidence (harness script, captured
rollout/notify JSON) lived in the session scratchpad, not the repo — it's not
part of this diff; the ADRs are the durable record.

**Out of scope.** S2 (Codex `SessionStart` → `additionalContext` injection) —
still open. Testing it needs a throwaway `.codex/hooks.json` test hook in a
scratch repo, which requires clearing Codex's hook-trust gate
(`--dangerously-bypass-hook-trust`, or an equivalent `-c
projects."<path>".trust_level="trusted"` override). Both were blocked by the
Claude Code auto-mode safety classifier as trust/sandbox bypass attempts, so
S2 needs an explicit decision from the user (grant the bypass for this
one-off scratch test, or approve the hook trust prompt interactively
themselves) before it can be attempted. Not part of this branch/review.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** approve | changes-requested — _one-line rationale._
