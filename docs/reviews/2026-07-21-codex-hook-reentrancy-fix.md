---
slug: codex-hook-reentrancy-fix
status: approved
author: claude
reviewer: codex
branch: fix-codex-hook-reentrancy-ignore-user-config
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: suppress Codex hook reentrancy via `--ignore-user-config`

## Brief  _(Author — Claude)_

**Intent.** Close a real gap found by live testing, follow-up to the
2026-07-17 Claude usage runaway incident
(`docs/notes/2026-07-17-claude-usage-runaway-incident.md`). That incident's
P0 fix was an environment marker (`NEUROBASE_INTERNAL_CALL=1`) that every
hook subprocess must see and honor before it captures a transcript or spawns
automatic curation, to stop Neurobase's own headless brain calls from
re-entering its own hooks. The incident report explicitly flagged this as
unproven and requiring a live spike before Claude's `SessionStart` hook could
be re-enabled.

This session ran that live spike for real, against disposable stores, for
both agents:

- **Claude**: confirmed safe. A real `claude -p` call with
  `NEUROBASE_INTERNAL_CALL=1` set produced no captured raw — the marker
  genuinely propagates to Claude's own hook subprocess.
- **Codex**: confirmed **broken**. The identical test — a real `codex exec`
  call with the marker set — still produced a captured raw. Codex does not
  propagate the marker to its own hook subprocess at all, so the existing P0
  fix gives Codex zero protection against the incident's exact recursive-
  capture failure mode.

A third live spike found a working fix: `codex exec --ignore-user-config`
skips loading `~/.codex/config.toml` entirely. All Codex hook wiring (both
user- and project-scoped) lives in that file — a project-scoped
`hooks.json` is not self-discovered by existing on disk; `config.toml` must
reference it. So skipping that file's load means Codex never discovers any
hook to fire, full stop — a stronger guarantee than a marker-gated no-op,
since the hook subprocess never runs at all. Confirmed live: a control
`codex exec` call captured a raw as expected; the identical call plus
`--ignore-user-config` captured nothing, with no marker involved.

**Scope.** Branch `fix-codex-hook-reentrancy-ignore-user-config`,
`git diff main...HEAD`. Key files:
- `src/neurobase/brain/codex_cli.py` — `_once()` now builds
  `["codex", "exec", "--ignore-user-config", "--json", prompt]` instead of
  `["codex", "exec", "--json", prompt]`. Added a module docstring paragraph
  explaining why the flag is load-bearing (not an optimization) and pointing
  at the incident doc.
- `tests/test_brain_codex_cli.py` — updated
  `test_invokes_expected_command`'s command-shape assertion for the new
  argv position, added
  `test_invokes_with_ignore_user_config_to_suppress_hook_reentrancy` pinning
  the flag specifically so it can't silently regress.
- `docs/notes/2026-07-17-claude-usage-runaway-incident.md` — three new
  sections recording all three live spikes ("Live spike: Claude marker
  propagation", "Live spike: Codex marker propagation (FAILED)", and the
  "Follow-up: RESOLVED" note under the Codex section), plus updated
  acceptance-criteria/open-questions checkmarks.

**Focus areas.**
- Is `--ignore-user-config` actually the right fix, or does it paper over
  the real bug? (i.e. should Codex's marker-propagation failure itself be
  reported upstream / tracked as a Codex bug, separately from working around
  it here?)
- Side effects of skipping `~/.codex/config.toml` for every internal Codex
  brain call: the call no longer sees the user's configured model, reasoning
  effort, sandbox policy, or MCP servers. I judged this acceptable/desirable
  for a one-shot internal instruction call (the curator/planner shouldn't
  need tool access or the user's interactive model prefs anyway) — but push
  back if that reasoning has a hole.
- `--ignore-user-config`'s doc string says "auth still uses `CODEX_HOME`" —
  worth confirming this doesn't have some interaction with `CODEX_HOME`
  overrides or multi-profile setups that the tests don't cover.
- Whether the incident doc's three new sections are accurate and
  proportionate, or overwrought for what's ultimately a two-line code fix.

**Known risks / tradeoffs.**
- This was tested against one real `codex-cli` version
  (`codex-cli 0.145.0-alpha.18`) on one machine. `--ignore-user-config`'s
  behavior on other versions isn't verified beyond the CLI's own `--help`
  text and this live spike.
- `NEUROBASE_INTERNAL_CALL` is left in place for Codex too, even though it's
  now known Codex itself never reads it — kept as harmless defense-in-depth
  since Neurobase's own `run_hook()` dispatch still checks it. Not removed,
  not required to make this fix work.
- The spawn-suppression half (a Codex-triggered `SessionStart` not spawning
  a detached `curate --if-stale`) was not separately live-tested with a
  pre-seeded stale raw — reasoning is that `--ignore-user-config` prevents
  *any* hook from firing at all (not just gates one), so if capture is
  provably suppressed, spawn is too, since there's no hook invocation left
  to reach the spawn code path. Worth checking this reasoning holds.

**How to verify.**
- `uv run pytest tests/test_brain_codex_cli.py -q` — all pass, including the
  two updated/new tests.
- `uv run python scripts/ci.py` — full local gate (ruff, ruff format, mypy,
  pytest+coverage). Was green before this hand-off (1082 tests passed, 1
  skipped, 91.21% coverage).
- Read `src/neurobase/brain/codex_cli.py`'s diff directly — it's a small,
  contained change.
- Optionally: re-run the live spike yourself if Codex CLI is available
  (scratch repo + scratch `NEUROBASE_ROOT` store + `neurobase init --agent
  codex --cwd <scratch> --yes`, approve the hook trust prompt once via
  `codex` in that dir, then compare `codex exec --json <prompt>` vs.
  `codex exec --ignore-user-config --json <prompt>` for raw capture in the
  scratch store).

**Out of scope.**
- The Claude `SessionStart` re-enable decision — unaffected by this branch,
  still a separate, paused product decision for the user.
- The `v0.1.0` release go/no-go — unrelated, separately still open.
- P1 observability (`doctor`/`status` surfacing pass-budget/lock state) and
  quarantining the incident's self-generated raw junk — both pre-existing,
  explicitly deferred items, not touched here.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

### F1 — nit — `docs/notes/2026-07-17-claude-usage-runaway-incident.md:570`

The follow-up note says "Both Claude's and Codex's hook wiring for a project
lives entirely in `config.toml`", but Claude project hooks are installed in
`.claude/settings.json`; the config-table/self-discovery rationale here applies
to Codex. This does not undermine the fix, because the `--ignore-user-config`
claim only needs to suppress Codex's own hook discovery, and the live spike plus
the new argv shape support that. Suggested direction: narrow the sentence to
"Codex's user- and project-scoped hook wiring" so the incident note does not
overstate the Claude side.

- **resolution:** resolved — reworded to "Codex's user- and project-scoped
  hook wiring lives entirely in `config.toml`", dropping the incorrect claim
  about Claude.

Verification run:
`codex exec --help` on the locally installed `codex-cli 0.145.0-alpha.18`
confirms `--ignore-user-config` is an `exec` option and says auth still uses
`CODEX_HOME`. `uv run pytest tests/test_brain_codex_cli.py -q` passed with 10
tests. `uv run python scripts/ci.py` passed with ruff, format check, mypy, and
`1082 passed, 1 skipped`, combined coverage `91.21%`.

**Verdict:** approve — the command now includes the load-bearing
`--ignore-user-config` flag, the regression test pins it, and I found no
correctness-blocking issue in the hook-reentrancy fix.
