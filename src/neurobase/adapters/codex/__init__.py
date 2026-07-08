"""Codex adapter: per-turn rollout scribe + SessionStart recall injection.

Live (Phase 5 core): hook-based capture (`scribe.py`, spec §5 — rollout parsing,
session-keyed per-turn overwrite) and SessionStart recall (`recall.py`, mirrors §3
per ADR-0005). Deferred: the `AGENTS.override.md` injection fallback (documented
only, spec §5) and the `init --agent codex` installer (hooks.json + config.toml
wiring + trust gate, spec §7). Shaped by spikes S1/S2.
"""
