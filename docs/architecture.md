# Architecture: the layer contract

This is the enforceable structure behind the codebase — where a new piece of
code belongs and what it's allowed to depend on. For a guided, file-by-file
tour of what's actually implemented, read
**[how-it-works.md](how-it-works.md)** instead (its §2 has the full
architecture diagram and module map); this page is the contract that tour is
describing, stated as rules a contributor or agent can check new code
against.

## The layers, bottom to top

```
core/            the foundation — every other package writes through it
brain/           LLM execution backends (provider-independent)
adapters/*·curator/·recommender/·mcp/   the "business logic" tier
cli/             orchestration, presentation, consent/diff/backup choreography
```

1. **`core/`** owns the on-disk store (`store.py`), the project registry
   (`projects.py`), config (`config.py`), redaction (`redact.py`), wikilink
   regeneration (`linkify.py`), backups (`backups.py`), and search
   (`search.py`). It knows about markdown, TOML, and the filesystem — nothing
   about agents, LLMs, or the CLI.
2. **`brain/`** is the one package that talks to an LLM, behind a single
   `plan_json`/`text` interface (`base.py`) with backend auto-detection
   (`select.py`). It depends on `core/` (config, for backend selection) and
   nothing above it.
3. **`adapters/claude/`, `adapters/codex/`, `curator/`, `recommender/`,
   `mcp/`** are the mid tier — each owns one contract (spec §2–§5, §12, §13)
   and depends on `core/` and `brain/`, never on each other except through
   `adapters/recall_common.py` (the one deliberate shared module, re-exported
   by both agent adapters rather than each reimplementing recall). `mcp/`
   and the two `adapters/` packages are the system's **edges** — the only
   places that talk to something outside this process (an agent's hook
   runtime, an MCP client).
4. **`cli/`** is a thin orchestration/presentation layer over everything
   below it: argument parsing, the consent → diff → backup choreography
   (spec §7), output formatting, and the hook fast-path dispatcher. It is the
   single front door — every human command and every agent-invoked hook
   resolves to a symbol in `cli/__init__.py` — but it contains no store
   logic, no parsing logic, no LLM logic of its own.

## The rule

**Lower layers never import upward.** `core/` cannot import `brain/`,
`adapters/`, or `cli/`; `brain/` cannot import `adapters/` or `cli/`; the mid
tier cannot import `cli/`. Dependencies only point down the list above. If a
change requires an upward import to compile, that's a signal the new code is
in the wrong layer, not a signal to add the import.

The one deliberate cross-package dependency inside the mid tier
(`adapters/recall_common.py`, shared by both agent adapters) exists because
recall assembly (spec §3) is agent-agnostic — the alternative was duplicating
it in both `adapters/claude/` and `adapters/codex/`, which is exactly the
divergence risk the shared module avoids. New shared logic between two mid-tier
packages should follow that same pattern: a named, single-purpose shared
module, not a lateral import of one package into another.

## Where the three loops cross the layers

Everything the system does is one of three loops (see how-it-works.md §3 for
the full walkthrough of each):

| Loop | Path through the layers |
|---|---|
| **Capture → curate → recall** | `adapters/*` (scribe) → `core/store` (raw write) → `curator/` (fold, calling `brain/`) → `core/store` (curated/nodes write) → `adapters/*` (recall, via `adapters/recall_common`) |
| **On-demand recall** | `mcp/server` → `core/search`, `core/store` — a read-only edge that never touches `curator/` or `recommender/` |
| **Recommend** | `recommender/` (mine via `brain/`, rank, propose) → `core/store` (proposal write) → `cli/` (accept/reject/edit, via the same consent/diff/backup path `adapters/*` installers use) |

Note that `mcp/`'s `recommendations_list` tool is a **read** over
`recommender/`'s output (spec §13) — the MCP edge never mines or ranks itself;
that keeps the expensive, LLM-driven work behind an explicit CLI invocation
(`neurobase recommend run`), not something a connected MCP client can trigger
implicitly.

## Why this matters for third-party agent support

The backlog's "third agent via the adapter guide" item depends on this
contract holding: adding a new agent should mean writing one new
`adapters/<agent>/` package (scribe + recall + install) against the existing
mid-tier contracts, reusing `adapters/recall_common.py`, `core/`, and
`brain/` unchanged — not touching `curator/`, `recommender/`, or `mcp/` at
all. See **[adapter-guide.md](adapter-guide.md)** for what that package
actually needs to implement.
