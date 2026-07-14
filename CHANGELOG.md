# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-07-14

Initial public release. Built in nine phases per
[docs/neurobase-build-plan.md](docs/neurobase-build-plan.md); see
[AGENTS.md](AGENTS.md) for the full phase-by-phase state and
[docs/how-it-works.md](docs/how-it-works.md) for a module-by-module tour.

### Added

- **Store + config core.** A markdown-native store (`raw/` → `curated/` →
  `nodes/`, wikilinked, Obsidian-readable), project registry with
  git-common-dir resolution, and a redaction table (private keys, cloud API
  keys, tokens, `KEY=`/`SECRET=`-shaped env lines) applied before anything
  touches disk.
- **Brain backends.** Pluggable execution over the user's own agent
  subscription or API key — `claude-cli`, `codex-cli`, or `anthropic-api`,
  auto-detected with a config override. Neurobase never stores or transmits
  credentials.
- **The curator.** An LLM-driven fold of raw captures into a small,
  non-redundant, current fact set — upserts, explicit supersession, and
  tombstones, optimized for deletion over accumulation, with node
  synthesis and wikilink regeneration on every run.
- **Claude Code adapter.** Deterministic SessionEnd capture and SessionStart
  recall hooks, a consent-first installer that diffs/backs up/writes
  `settings.json`, and fenced ownership so Neurobase only ever touches the
  hook entries it created.
- **Codex CLI adapter — cross-agent memory.** Rollout-based capture (including
  the VS Code IDE-wrapper split and per-turn overwrite trick), SessionStart
  injection verified to reach the model as a real input message, and a
  consent-first `hooks.json`/`config.toml` installer. A Claude session's
  learnings now show up in a Codex session, and vice versa.
- **Guided lifecycle.** `neurobase init` (guided or per-agent), `doctor`
  (shim/store/project/brain/hook/MCP/trust matrix with remedies), and
  `uninstall` (surgical removal of only Neurobase-owned entries, optional
  `--purge-store`).
- **MCP server.** `neurobase mcp serve` exposes `memory_search`,
  `memory_read_node`, `memory_list_projects`, `memory_remember`, and
  `recommendations_list` over stdio to any MCP client, with an always-valid
  `resources/list` and fail-soft tools throughout.
- **The recommender.** Cross-session, cross-agent pattern mining
  (`neurobase recommend run`) that proposes promoting recurring patterns into
  standard `SKILL.md` / `AGENTS.md`/`CLAUDE.md` artifacts — ranked, redacted,
  reviewed one at a time (`list` / `show` / `edit` / `accept` / `reject`),
  never auto-installed. A ledger tracks accept/edit/reject/survival, surfaced
  via `status --recommender` (precision, edited-rate, 30-day survival,
  recurrence reduction).
- **`neurobase seed`.** Bulk-import existing markdown notes or Claude
  auto-memory as curated facts, redacted on the way in.

### Known gaps

- The store-schema guard (`ensure_store_metadata`) is enforced per call site,
  not at the store boundary — see `G1` in [docs/known-gaps.md](docs/known-gaps.md).
- SQLite-backed search, a native scheduler, an Ollama backend, and a third
  agent adapter are tracked in the build-plan
  [backlog](docs/neurobase-build-plan.md#backlog-post-010-in-rough-order),
  not this release.

[0.1.0]: https://github.com/rebelscum216/neurobase/releases/tag/v0.1.0
