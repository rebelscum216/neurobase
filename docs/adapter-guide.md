# Adapter guide: adding a third coding agent

Neurobase ships two adapters — `adapters/claude/` and `adapters/codex/` — for
Claude Code and Codex CLI. This guide documents the seam a **third** agent
(the backlog names Gemini CLI and Cursor as candidates) would plug into. It's
a description of the existing contract, not new code — everything below is
implemented today for the two shipped adapters; read their source alongside
this guide as the worked example.

If your target agent supports **AGENTS.md** (the cross-agent instruction
standard) and an MCP-client mode, the read side is already free: point it at
`neurobase mcp serve` (spec §13) and it can call `memory_search` /
`memory_read_node` / `memory_remember` with zero adapter code. An `adapters/`
package is only needed for **automatic** capture and injection — the
hooks-based half of the loop.

## What an adapter package owns

Per the [layer contract](architecture.md), a new `adapters/<agent>/` package
depends only on `core/` and `brain/` (via `adapters/recall_common.py` for
injection) and implements three pieces:

### 1. Scribe (capture — spec §4/§5 are the worked contracts)

A function that turns "a session/turn just ended" into **zero or one**
`raw/*.md` write. The hard rules, non-negotiable regardless of agent:

- **Deterministic. No LLM in this path.** Parsing the transcript/rollout is
  plain code — the curator, not the scribe, is where an LLM gets involved.
- **The scribe function itself is allowed to raise** — parsing, config
  loading, and the write can all fail, and both shipped scribes say so in
  their own docstrings ("callers should treat any exception as 'capture
  nothing'"). The **hard, non-negotiable guarantee is the hook entry
  point's**, not the scribe function's: the CLI's hook dispatcher wraps
  every call in `except Exception: pass` and always exits 0, so a scribe
  bug degrades to "captured nothing this time," never a wedged session
  teardown. Write your scribe function as plain, testable code that can
  raise on bad input; put no exit-0/exception-swallowing logic inside it —
  that belongs solely at the hook boundary in `cli/`.
- **Redact before writing** (`core/redact.py:redact`, spec §10/D13) — no
  exceptions, no "redact later."
- **Opt-in.** Write only if the resolved project's memory tree already
  exists (i.e., the project ran `neurobase enable`) — a scribe must never
  create a store as a side effect of a hook firing.
- **Empty capture writes nothing.** No prompts and no summary ⇒ skip the
  write entirely; don't write an empty raw file.
- **Bounds.** Truncate to the shared defaults (spec §8: last 25 prompts,
  600 chars each, 4000-char summary) unless the agent's own transcript
  shape genuinely requires different numbers — if so, that's a config
  addition, not a silent per-adapter divergence.

What varies per agent is entirely upstream of those rules: how you get from
"a hook fired" to "a list of (prompt, summary, metadata) tuples." Claude
parses a SessionEnd JSONL transcript path; Codex parses a per-turn rollout
file and has no SessionEnd equivalent at all (hence the per-turn overwrite
trick in spec §5 — `captured_at` pinned to session start so repeated firings
resolve to one raw file). Your agent's transcript format is the actual
research question; the fixture discipline in spec §11 (real captured
examples, not assumed shapes) is how both existing adapters got this right —
do the same: capture a real transcript before writing the parser.

### 2. Recall (injection — spec §3)

Reuse `adapters/recall_common.py` rather than reimplementing it — it already
does the agent-agnostic half: assemble the project's nodes, join with the
proven header framing, cap at 6000 chars (default) by dropping whole trailing
nodes rather than truncating mid-node. What your adapter supplies is only the
transport: how your agent's session-start hook expects output shaped, and how
it reads `cwd` from its own hook payload to resolve a project.

The one rule that matters more than the shape: **fail-safe.** Any error, no
resolved project, or no nodes ⇒ emit nothing and exit 0. Recall silently
doing nothing is an acceptable failure mode; recall crashing a session start
is not.

### 3. Installer (spec §7)

Every installer, regardless of agent, goes through the same choreography
(`core/backups.py` + the CLI's consent/diff pattern, not reimplemented
per-adapter):

1. Compute the exact config change (absolute shim path to
   `neurobase hook <agent> <event>`).
2. Show the diff, ask for consent.
3. Back up the original file under `<store root>/backups/<ts>/` with a
   manifest — before the first write, not after.
4. Write atomically. Idempotent: running `init` again with the same intent
   is a no-op, not a duplicate entry.
5. **Fenced ownership**: a hook entry is Neurobase-owned iff its command
   string contains `<shim>/neurobase hook` — `uninstall` must be able to
   remove exactly the entries Neurobase created and nothing the user or
   another tool wrote. If your agent's config format doesn't cleanly support
   command-string fencing, that's a real design problem to solve before
   shipping the adapter, not an edge case to skip.

Expect agent-specific gotchas here — Codex alone has two (event-name casing
rewritten to CamelCase on load, and `hooks.json` requiring an explicit
`config.toml` reference to even be discovered; see spec §7). Budget
discovery time for your target agent's equivalent surprises; both existing
adapters found theirs the hard way, via a live spike against a real install
before writing the installer, not by reading that agent's docs alone.

## Suggested build order

1. **Spike first, adapter-guide-driven docs second.** Capture one real
   transcript/rollout from the target agent by hand and write it up as a
   fixture (see spec §11 for the existing Claude/Codex fixtures as the
   template) before writing any parsing code.
2. Scribe, tested against that fixture — no live hook required yet.
3. Recall, wired through `adapters/recall_common.py`.
4. Installer, tested against a scratch copy of the agent's real config file
   (never the developer's live config) to confirm the diff/backup/fence
   behavior before pointing it at a real install.
5. Live end-to-end: install, run a real session, confirm capture and next-
   session recall — the same live-verification bar every phase of this
   project has held itself to (see `AGENTS.md`'s "Current state" for
   examples of what that verification looked like for Claude and Codex).

## What you should not need to touch

`core/`, `brain/`, `curator/`, `recommender/`, and `mcp/` are all
agent-agnostic already. A correctly-scoped third adapter is additive: one new
`adapters/<agent>/` directory, one new `cli/` hook dispatch entry, and
(if the agent supports it) one new MCP registration path in `init` — nothing
in the mid tier or core should need to change to accommodate a new agent.
