# 2026-07-16 — Claude user-scoped hook smoke

Goal: verify the intended install-once model for Claude hooks: a user-scoped
`~/.claude/settings.json` hook runs globally, and Neurobase remains per-repo
opt-in because the scribe no-ops unless the cwd resolves to an enabled project.

Setup:

- `uv run neurobase init --agent claude --user --yes`
- `uv run neurobase doctor` reported:
  `✓ claude hooks: user /Users/andrewsmith/.claude/settings.json points at
  /Users/andrewsmith/Projects/neurobase/.venv/bin/neurobase`
- The repo-local `.claude/settings.json` created during the first diagnostic pass
  was removed; the smoke uses the user hook, not a project hook.

Live smoke:

1. A fresh Claude session in `/Users/andrewsmith/Projects/neurobase` was given
   the plain conversational marker `phase-c-claude-user-hook-716`, with explicit
   instruction not to write files.
2. Clearing the chat initially did not produce a Neurobase raw; after the user
   completed the session boundary, the marker appeared in a Claude raw.
3. Verification:

```text
/Users/andrewsmith/neurobase/projects/neurobase/memory/raw/2026-07-16T16-55-25Z_claude_49828b8b.md
```

That raw contains the marker in both the captured prompt and assistant summary.

Curate follow-up:

```text
{"status": "ok", "raw": 143, "batches": 1, "distilled": 1, "fallback": 142, "upserts": 1, "superseded": 0, "tombstones": 0, "pruned_tombstones": 0, "active_facts": 13}
```

After the pass, the marker raw had `consumed: true`. The regenerated project node
records the durable hook fact ("Claude + Codex user-scope hooks ... captures
produced from both") but does not preserve the literal marker, which is expected:
the curator treated the smoke-test token as non-durable session noise rather than
a fact worth recalling verbatim.

Result: the user-scoped Claude `SessionEnd` hook captured a real Claude session
for the enabled `neurobase` project, and a real curate pass consumed it. This
closes the previously-deferred in-vivo Claude capture smoke.
