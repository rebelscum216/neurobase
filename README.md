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

> **Status: `0.1.0` release candidate — not yet tagged or published.** The full
> loop is implemented for Claude Code and Codex CLI: deterministic capture, an
> LLM curator that folds and deletes, curate-time transcript distill for richer
> Claude-session recall, cross-agent recall, consent-first hook installers, an MCP
> server, and the v1 recommender (mine → rank → propose → accept/reject/edit →
> metrics). The package version is prepared as `0.1.0`, but the `v0.1.0` git tag,
> GitHub Release, and PyPI publish are intentionally held until release approval.

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
- **Transcript distill.** When a raw capture has a resolvable Claude transcript,
  curate can distill the fuller session into a bounded, redacted digest before the
  plan step; missing transcripts or backend failures safely fall back to the skim.
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
data-flow loops, and every source file. For the layer rules new code should
follow, see **[docs/architecture.md](docs/architecture.md)**.

## How it compares

Local-first, cross-agent MCP memory isn't a green field — it's honest to say
so. [basic-memory](https://github.com/basicmachines-co/basic-memory) is the
closest thing to Neurobase's memory-and-wiki layer already shipped, with a
proven paid tier; [Memorix](https://github.com/AVIDS2/memorix) already spans
more agents and has its own skill-promotion command; [mem0
OpenMemory](https://github.com/mem0ai/mem0) is the funded incumbent. Facts
below checked directly against each project's README as of 2026-07; pricing
in particular moves — verify current numbers before quoting them elsewhere.

| | **Neurobase** | basic-memory | Memorix | mem0 OpenMemory |
|---|---|---|---|---|
| License | Apache-2.0 | AGPL-3.0 | Apache-2.0 | Apache-2.0 |
| Storage | Markdown, wikilinked | Markdown, wikilinked (Obsidian) | SQLite + Orama search | Postgres + Qdrant (Docker) |
| Cross-agent | Claude Code + Codex CLI (hooks + MCP) | Any MCP client | Many agents (MCP + plugins) | Any MCP client |
| Fact set | Curator **folds & deletes** — small, current, non-redundant | Editable notes (`write_note`/`edit_note`/`delete_note`), no automatic curation | Notes + generated briefs; no documented automatic pruning | Vector-recalled memories, no documented automatic pruning |
| Skill/rule promotion | **Mines the corpus for recurring patterns, proposes SKILL.md/AGENTS.md, and tracks accept/edit/reject + 30-day survival per proposal** | — | Has `memorix skills` (CLI) / `memorix_promote` (MCP tool) to promote knowledge into skill files; README doesn't document automatic pattern-mining or post-promotion tracking | — |
| Cost | Free | Free, self-hosted + a paid hosted-sync tier (check current pricing — it's changed more than once) | Free | Free self-hosted (mem0 also sells a cloud product) |

What basic-memory and Memorix do well, they do well — a mature markdown+
Obsidian graph and broad agent coverage aren't nothing, and if you just want
cross-agent recall, either is a reasonable choice. Memorix in particular
already has a skill-promotion command, so the honest distinction isn't
"nobody else promotes skills" — it's that Neurobase's promotion is driven by
mining the corpus for recurring cross-session patterns rather than a manual
command, and measures what happened after acceptance (kept, edited, or
reverted) to feed back into ranking. That measured loop, on top of a curator
that actively deletes instead of just accumulating, is the actual bet.

## Quickstart

Until the first public package is published, install from a checkout:

```bash
git clone https://github.com/rebelscum216/neurobase.git
cd neurobase
uv tool install .                 # installs the `neurobase` command
```

After the PyPI release, the install command will be:

```bash
uv tool install neurobase-cli     # command: `neurobase`
```

(`neurobase-cli` because `neurobase` is taken on PyPI — decision D2. `pip
install` will work too; `uv` is recommended, not required.)

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

## Documentation

- **[docs/how-it-works.md](docs/how-it-works.md)** — how the code is built: a
  guided, module-by-module tour of every subsystem.
- **[docs/architecture.md](docs/architecture.md)** — the layer contract: what
  each package owns and what it's allowed to depend on.
- **[docs/adapter-guide.md](docs/adapter-guide.md)** — what it takes to add a
  third agent (Gemini CLI, Cursor, ...) beyond Claude Code and Codex CLI.
- **[SECURITY.md](SECURITY.md)** — the trust boundary, the redaction policy,
  and how to report a vulnerability.
- **[AGENTS.md](AGENTS.md)** — start here if you're building on the repo (human or
  agent): build principles, dev workflow, and the review relay.
- **[docs/](docs/README.md)** — the full index: the phased build plan, the
  authoritative behavioral spec, the architecture rationale, ADRs, and notes.
- **[CHANGELOG.md](CHANGELOG.md)** — what shipped in each release.

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)**, which points to
**[AGENTS.md](AGENTS.md)** (the real operating guide) and the
[code-review relay](docs/code-review-relay.md) this project uses for
non-trivial changes. Before every push, run the full local gate — not just
the tests:

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
