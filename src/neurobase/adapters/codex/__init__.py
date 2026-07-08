"""Codex adapter: per-turn rollout scribe + SessionStart recall + installer.

Live: hook-based capture (`scribe.py`, spec §5 — rollout parsing, session-keyed
per-turn overwrite), SessionStart recall (`recall.py`, mirrors §3 per ADR-0005),
and the `init --agent codex` installer (`install.py`, spec §7 — hooks.json +
surgical `~/.codex/config.toml` `[projects.*]` wiring + the trust-gate reminder,
ADR-0006). Deferred: the `AGENTS.override.md` injection fallback and the `notify`
legacy fallback (both documented only, spec §5). Shaped by spikes S1/S2 + the
2026-07-08 command-tokenization/trust spike (ADR-0006).
"""
