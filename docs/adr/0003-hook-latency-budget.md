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
each.

**Correction (same day):** the first measurement pass timed the `hook` stub
in a state that **exited 1** on any args, because Typer rejected the
`claude session-start` positional args before the command body ever ran —
review caught that this violates spec §4/§5's fail-safe MUST ("every code
path exits 0 — never wedge an agent's session teardown or startup"), which
applies to the stub too, not just the eventual Phase-4 implementation. Fixed
`src/neurobase/cli/__init__.py`'s `hook` command to accept and ignore any
`<agent> <event>` args (`context_settings={"allow_extra_args": True,
"ignore_unknown_options": True}`) and exit 0 unconditionally, then re-measured
against the corrected, now fail-safe-conformant stub.

**Result:** warm ≈ 40ms per call → **≈80ms combined** for session-start +
session-end, still comfortably inside the 500ms budget (~6x headroom).

Caveats (unchanged):
- `hook` is still a Phase-4 stub — no transcript parsing, redaction, or file
  write happens. This measurement is the **process/interpreter/Typer-dispatch
  floor**, not the eventual real-logic cost. That floor is, however, the
  dominant and hardest-to-shrink cost the exit criterion is really probing —
  the actual hook logic (regex redaction, one file read, one atomic write) is
  expected to add low-single-digit milliseconds.
- Could not measure a genuinely dropped-page-cache cold start (no passwordless
  `sudo purge` available in this environment).

**The budget holds**, with enough margin that Phase 4's real logic is very
unlikely to blow it. Re-measure once Phase 4 lands the real `hook` command to
confirm — this ADR's number is a floor, not a substitute for that
re-verification.

## Consequences

- The exit-0 fix lands now, not deferred to Phase 4 — a hook stub that can
  exit non-zero is a live fail-safe violation the moment `init` wires it up,
  regardless of when the real logic behind it lands.
- Add a Phase-4 "done when" follow-up: re-run this timing against the real
  `hook claude session-start`/`session-end` implementations and record the
  result (update this ADR's Decision section or file a new one if the numbers
  move meaningfully).
- If a future re-measure blows the budget, the two levers noted for later
  (not needed now): trim synchronous work at session-end (e.g. defer
  redaction-heavy steps), or — only as a last resort, since it adds moving
  parts contrary to the plan's simplicity bias — a persistent daemon/socket
  instead of a cold process per hook firing.

## Alternatives considered

- n/a — this is a measurement spike, not a design choice between options.
