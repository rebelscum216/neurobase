# Neurobase

**A local-first memory layer that follows you across your coding agents.**

Your coding agents forget everything between sessions. Neurobase doesn't just
remember — it captures your Claude Code and Codex CLI sessions automatically,
curates them into a small, current fact set, builds a browsable markdown wiki, and
injects that memory back into future sessions. On top of that loop sits the piece
nobody ships: a **recommender** that mines your cross-agent history for recurring
patterns and proposes promoting them into standard **SKILL.md** and
**AGENTS.md/CLAUDE.md** files — human-in-the-loop, never auto-installed.

It all runs on your machine, on the agent subscriptions you already pay for, with
**zero cloud dependency and zero telemetry — permanently.**

> **Status: pre-alpha (Phase 0 — repo bootstrap).** Not yet installable. The design
> is fully specified in [`docs/`](docs/README.md); the code is being built phase by
> phase. Watch this space.

## How it works

```
hooks capture (auto)  →  curator folds raw into a small durable fact set
      →  nodes + wiki regenerate  →  hooks inject memory at session start (auto)
      →  MCP tools answer on-demand recall  →  recommender surfaces skill/rule
         proposals  →  you approve  →  emitted as SKILL.md / AGENTS.md
```

- **Deterministic capture.** Hooks record sessions with no LLM in the loop; secrets
  are redacted before anything is written.
- **A curator that deletes.** An LLM folds raw captures into a *small,
  non-redundant, current* fact set — optimizing for supersession, not accumulation.
- **Markdown truth.** Wikilinked, Obsidian-readable, git-friendly. No vector or
  graph database in the core.
- **Cross-agent.** A Codex session's learnings show up in your next Claude Code
  session, and vice versa.
- **The recommender.** The novel contribution: cross-session, cross-agent pattern
  mining that proposes portable, standard-format skills and rules.

## Documentation

- **[AGENTS.md](AGENTS.md)** — start here if you're building on the repo (human or agent).
- **[docs/](docs/README.md)** — the full index: the phased build plan, the
  authoritative behavioral spec, the architecture rationale, ADRs, and working notes.

## Install

Not published yet. When it ships (planned command / package):

```bash
uv tool install neurobase-cli     # command: `neurobase`
```

(`neurobase-cli` because `neurobase` is taken on PyPI — decision D2. `pip install`
will also work; `uv` recommended, not required.)

## License

[Apache-2.0](LICENSE). Copyright © 2026 The Neurobase Authors.

---

<sub>This is a **Python** project (`neurobase-cli`). The `neurobase` name on npm is a
defensive reservation only — the `package.json`/`index.js` here are a placeholder,
not part of the build.</sub>
