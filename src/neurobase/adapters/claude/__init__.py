"""Claude Code adapter: SessionEnd scribe + SessionStart recall.

`scribe.py` (spec §4) parses a finished session's transcript into one redacted
raw capture; `recall.py` (spec §3) assembles the project's status nodes into
`additionalContext` at session start. Both are driven by the `neurobase hook
claude session-end|session-start` entry points and always exit 0.

The `init --agent claude` installer (settings.json read/diff/write + consent +
backup, spec §7) lands with the Phase-4 install step / Phase 6.
"""
