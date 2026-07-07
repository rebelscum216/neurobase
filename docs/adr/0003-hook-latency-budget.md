# ADR-0003: Hook latency budget

- **Status:** Accepted
- **Date:** 2026-07-07
- **Resolves:** S6
- **Supersedes:** none

## Context

Exit criterion: `neurobase hook` session-start + session-end overhead combined
**< 500ms warm**, timed via the installed `uv tool` shim (the real invocation
path — hooks call the absolute shim, per decision D4).

## Decision

Timed `neurobase hook claude session-start` and `neurobase hook claude
session-end` via `~/.local/bin/neurobase` (a `uv tool install`-managed
venv's Python entry-point script), using `/usr/bin/time -p`: 8 warm runs of
each, plus one first-invocation-in-a-fresh-shell run.

**Result:** warm ≈ 60ms per call → **≈120ms combined** for session-start +
session-end, well inside the 500ms budget (~4x headroom). First invocation in
a fresh shell was ≈110ms (OS page cache for the venv/interpreter was already
warm from prior `neurobase --help`/`version` calls this session, not a
true post-reboot cold start — see caveat).

Caveats:
- `hook` is still a Phase-4 stub — it exits 1 on any args (Typer rejects the
  unexpected `claude session-start` positional args) rather than running real
  logic. This measurement is therefore the **process/interpreter/Typer-dispatch
  floor**, not the eventual cost of transcript parsing + redaction + atomic
  file write. That floor is, however, the dominant and hardest-to-shrink cost
  the exit criterion is really probing (Python interpreter start via the
  `uv tool` shim) — the actual hook logic (regex redaction, one file read,
  one atomic write) is expected to add low-single-digit milliseconds.
- Could not measure a genuinely dropped-page-cache cold start (no passwordless
  `sudo purge` available in this environment).

**The budget holds**, with enough margin that Phase 4's real logic is very
unlikely to blow it. Re-measure once Phase 4 lands the real `hook` command to
confirm.

## Consequences

- No design change needed now. Add a Phase-4 "done when" follow-up: re-run
  this timing against the real `hook claude session-start`/`session-end`
  implementations and record the result (update this ADR's Decision section
  or file a new one if the numbers move meaningfully).
- If a future re-measure blows the budget, the two levers noted for later
  (not needed now): trim synchronous work at session-end (e.g. defer
  redaction-heavy steps), or — only as a last resort, since it adds moving
  parts contrary to the plan's simplicity bias — a persistent daemon/socket
  instead of a cold process per hook firing.

## Alternatives considered

- n/a — this is a measurement spike, not a design choice between options.
