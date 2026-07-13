# Neurobase

**A local-first memory layer that follows you across your coding agents.**

Your coding agents forget everything between sessions, and what one learns is
invisible to the next. Neurobase fixes both. It captures your Claude Code and
Codex CLI sessions automatically, curates them into a small, current fact set,
builds a browsable markdown wiki, and injects that memory back into future
sessions — for **either** agent. On top of that loop sits the piece nobody else
ships: a **recommender** that mines your cross-agent history for recurring
patterns and proposes promoting them into standard **SKILL.md** and
**AGENTS.md/CLAUDE.md** files — human-in-the-loop, never auto-installed.

It all runs on your machine, on the agent subscriptions you already pay for, with
**zero cloud dependency and zero telemetry — permanently.**

> **Status: pre-alpha — installable from source, not yet published to PyPI.**
> The full loop is implemented for Claude Code and Codex CLI: deterministic
> capture, an LLM curator that folds and deletes, cross-agent recall,
> consent-first hook installers, an MCP server, and the v1 recommender
> (mine → rank → propose → accept/reject/edit → metrics).
>
> Still ahead of `0.1.0`: PyPI publication and a trusted-publishing release
> workflow, `SECURITY.md` (redaction policy + trust boundary), an adapter guide,
> `CONTRIBUTING` + issue templates, a `CHANGELOG`, and a clean-machine install
> verification. Treat the current install path as "works on the maintainer's
> machine," not "battle-tested."

## How it works

```
hooks capture (auto)  →  curator folds raw into a small durable fact set
      →  nodes + wiki regenerate  →  hooks inject memory at session start (auto)
      →  MCP tools answer on-demand recall  →  recommender surfaces skill/rule
         proposals  →  you approve  →  emitted as SKILL.md / AGENTS.md
```

- **Deterministic capture.** Hooks record sessions with no LLM in the loop, and
  always exit cleanly so they can never wedge a session. Secrets are redacted
  before anything is written.
- **A curator that deletes.** An LLM folds raw captures into a *small,
  non-redundant, current* fact set — optimizing for supersession, not accumulation.
- **Markdown truth.** Wikilinked, Obsidian-readable, git-friendly. No vector or
  graph database in the core.
- **Cross-agent.** A Codex session's learnings show up in your next Claude Code
  session, and vice versa — both agents' captures fold into one fact set.
- **On-demand recall.** An MCP server exposes search/read/remember tools to any
  MCP client, alongside the automatic injection.
- **The recommender.** The novel contribution: cross-session, cross-agent pattern
  mining that proposes portable, standard-format skills and rules, and learns
  from what you accept.

Want the full mechanics? **[docs/how-it-works.md](docs/how-it-works.md)** is a
module-by-module tour of the entire codebase — the architecture, the three
data-flow loops, and every source file.

## Quickstart

Neurobase is not published to PyPI yet. From a local checkout:

```bash
uv tool install .     # installs the `neurobase` command
```

Then run the guided setup in the repo you want Neurobase to remember:

```bash
neurobase init
neurobase doctor
```

`init` chooses a visible store root (default `~/neurobase`), enables the current
repo, detects Claude Code / Codex CLI on your `PATH`, shows the exact config
diffs, asks before writing, backs up any existing agent config, registers the MCP
server, and prints the next-session notice. Codex will also ask you to approve the
edited hook on its next launch; until that trust prompt is accepted, Codex won't
run the hook. `doctor` reports the installed shim, store, project, brain backend,
agent binaries, hook wiring, MCP registration, and Codex trust state — each with a
named remedy.

Prefer the explicit path? Use the per-agent installers:

```bash
neurobase enable                  # register the current repo as a project
neurobase init --agent claude
neurobase init --agent codex
```

## Everyday use

Once installed, capture and recall happen automatically. These commands are the
manual surface:

```bash
neurobase status                  # projects, raw (consumed/unconsumed), facts, nodes
neurobase curate                  # fold unconsumed captures now (also runs opportunistically)
neurobase seed --from-dir ./notes # import existing markdown notes as curated facts
```

Review what the recommender proposes from your history:

```bash
neurobase recommend run           # mine + rank the corpus into proposals
neurobase recommend list          # proposals with status/type/target/score
neurobase recommend show <slug>   # the full proposal + evidence + history
neurobase recommend edit <slug>   # revise the draft (records an edit, installs nothing)
neurobase recommend accept <slug> # render a SKILL.md / rule block — with a diff + backup
neurobase recommend reject <slug> --reason "..."
neurobase status --recommender    # precision, edited-rate, 30-day survival, reduction
```

Expose memory to any MCP client on demand:

```bash
neurobase mcp serve               # stdio MCP server: search / read / list / remember
```

To remove Neurobase-owned hooks without touching your memory store:

```bash
neurobase uninstall
```

Use `neurobase uninstall --purge-store` only when you also want to delete the
local store.

Once Neurobase is published, the install command will pull from PyPI directly:

```bash
uv tool install neurobase-cli     # command: `neurobase`
```

(`neurobase-cli` because `neurobase` is taken on PyPI — decision D2. `pip install`
will also work; `uv` recommended, not required.)

## Documentation

- **[docs/how-it-works.md](docs/how-it-works.md)** — how the code is built: a
  guided, module-by-module tour of every subsystem.
- **[AGENTS.md](AGENTS.md)** — start here if you're building on the repo (human or
  agent): build principles, dev workflow, and the review relay.
- **[docs/](docs/README.md)** — the full index: the phased build plan, the
  authoritative behavioral spec, the architecture rationale, ADRs, and notes.

## Contributing

Start with **[AGENTS.md](AGENTS.md)** (the contributor guide). Before every push,
run the full local gate — not just the tests:

```bash
make ci                       # ruff check + ruff format --check + mypy + pytest
# or, without make (e.g. on Windows):
uv run python scripts/ci.py
```

`scripts/ci.py` is the single source of truth for those four checks, and CI runs
the *same* script on every OS in the matrix, so local and CI can't drift. To have
Git block a red push automatically, opt into the committed pre-push hook once per
clone:

```bash
git config core.hooksPath .githooks
```

## License

[Apache-2.0](LICENSE). Copyright © 2026 The Neurobase Authors.

---

<sub>This is a **Python** project (`neurobase-cli`). The `neurobase` name on npm is a
defensive reservation only — the `package.json`/`index.js` here are a placeholder,
not part of the build.</sub>
