# Known gaps

Known **defects and inconsistencies in shipped code** — places where what the code
does and what it should do have drifted apart, but the fix hasn't landed yet.

This file exists because nothing else in `docs/` was the right home for it:

| If it's… | It goes… |
|---|---|
| A decision (spike outcome, D-table change) | [`adr/`](adr/README.md) — immutable once accepted |
| Scratch thinking, an investigation log | [`notes/`](notes/README.md) |
| A code-review baton | [`reviews/`](reviews/README.md) |
| A **future feature** | build-plan [Backlog](neurobase-build-plan.md) — roadmap, not a defect |
| A **defect in code that already shipped** | **here** |

## Conventions

- One `### G<n>` entry per gap, newest last. Never renumber.
- `status`: `open` · `fixed` (link the commit/PR) · `wontfix` (say why) ·
  `promoted` (moved to a GitHub issue once Phase 9 ships issue templates).
- Absolute dates (`2026-07-12`), never "recently."
- A gap is not a TODO. If it's merely unbuilt, it belongs in the build-plan
  backlog. This file is for code that is **wrong or inconsistent right now**.
- **Graduation path:** Phase 9 ships CONTRIBUTING + issue templates, at which
  point GitHub Issues becomes the tracker. Each open gap here becomes an issue;
  this file then either retires or stays as the offline, greppable mirror (agents
  can read it without network access — which is the whole point of a local-first
  project).

---

### G1 — the D11 store-schema guard is applied ad hoc, and is missing or late on most entry points

- **status:** open
- **severity:** major — spec §10 says *"refuse to **operate** on a schema newer
  than the binary."* Not "refuse to mutate," and no read-only exemption exists
  anywhere in the contract. This is a `MUST` violation, and it is systemic rather
  than a single stray branch.
- **root cause:** the guard (`store.ensure_store_metadata`) is invoked
  **per-command, by hand**, at the CLI layer — not at the store boundary. Any path
  that forgets it, or that reads the store before calling it, is silently
  unprotected. `projects.resolve_project()` → `load_registry()` reads
  `<root>/registry.toml` with no guard of its own, and it runs *first* in nearly
  every flow.
- **found:** 2026-07-12 by Codex (how-it-works review). **Scope corrected twice**
  — 2026-07-13 (known-gaps review r1: the surface was wider than stated and the
  contract was misread) and again r2 (the replacement audit was itself wrong).
  The table below is the third attempt and the first machine-verified one.

**Audit scope + method.** Every CLI command, every hook event, and every MCP
tool/resource/prompt, as of `d3b4091`. Each entry point's call chain was traced to
every store artifact it touches (`store.toml`, `registry.toml`, `raw/`, `curated/`,
`nodes/`, `index.md`, `proposals/`, `recommender/ledger.jsonl`, `backups/`), then
independently re-verified against source. 39 entry points. Reproduce with:

```bash
grep -rn "ensure_store_metadata" src/     # the guard has exactly 5 call sites
grep -n  "_check_store_schema" src/neurobase/cli/__init__.py
```

Those five call sites are the whole of D11's enforcement: `store.ensure_tree`
(store.py:118), `cli._check_store_schema` (cli/__init__.py:57),
`recall_common.build_context` (recall_common.py:81), and the two scribes
(claude/scribe.py:171, codex/scribe.py:245). Five call sites guarding 39 entry
points is the gap, stated as compactly as it can be.

Definitions: **guarded** = the guard runs before *every* store access.
**partial** = the guard runs, but *after* at least one store read (in practice
always `registry.toml`, via `resolve_project`). **unguarded** = the guard never
runs on that path.

#### Tier 1 — unguarded (13)

No D11 check at all. These read or destroy real memory content.

| Entry point | Store state touched unguarded |
|---|---|
| `status --recommender` | `proposals/`, `ledger.jsonl`, and — via `metrics._recurrence_reduction` → `corpus.load_corpus` — `registry.toml`, `curated/`, `raw/` |
| `mcp serve` — `build_server()` startup | `registry.toml`, `nodes/` |
| `mcp serve` — `memory_search` | `registry.toml`, `curated/`, `nodes/` |
| `mcp serve` — `memory_read_node` | `nodes/` |
| `mcp serve` — `memory_list_projects` | `registry.toml`, `curated/`, `nodes/` |
| `mcp serve` — `recommendations_list` | `proposals/` |
| `mcp serve` — node resources (register + read) | `registry.toml`, `nodes/` |
| `uninstall --purge-store` | **`shutil.rmtree(<root>)` — deletes the entire store** |
| `uninstall --restore-backup <ts>` | `backups/<ts>/manifest.json` + stored copies |
| `uninstall` (default) | `backups/` (write) |
| `init --agent claude` / `init --agent codex` | `backups/`; `backup_files` does `mkdir(parents=True)`, **creating `<root>/` even when the store was never initialized** |

`mcp/server.py` never calls `ensure_store_metadata` **at all** — its entire read
surface is unguarded. (`memory_remember` is the sole exception, and only
partially: see Tier 2.)

#### Tier 2 — partial (11)

The guard *does* run and does block the substantive operation — but only after
`resolve_project()` has already read `registry.toml`. Materially lower risk than
Tier 1: no memory content is read before the refusal.

`status` (normal path) · `curate` · `init` (guided) · all five hooks
(`claude session-start|session-end`, `codex session-start|stop|notify`) ·
MCP `memory_remember` · MCP `recall` prompt.

This is a real classification, not pedantry: `enable`'s own inline comment reads
*"`_check_store_schema(resolved_root)  # before registry.toml is touched`"*
(`cli/__init__.py:76`) — the author explicitly intended registry reads to sit
behind the guard. Tier 2 is where that intent isn't upheld.

#### Tier 3 — guarded (12) and not-applicable (3)

**Guarded:** `enable`, all six `recommend` subcommands, all five `seed` variants.
**Not-applicable:** `version` (touches nothing); `init --agent <bad value>` (exits
first); `doctor` — which *reports* schema rather than refusing, correct by design.

> Note: `doctor` does not reuse the guard — `cli/diagnostics.py` **re-implements**
> the schema comparison (`schema > store.STORE_SCHEMA_VERSION`) inline. That
> duplication should be collapsed when the guard is centralized, or the two will
> drift.

**Fix direction.** Do **not** patch 13 call sites — that is what produced this
mess. The decision comes first, and it is architectural:

1. **Centralize the guard at the store boundary** (recommended). Make it
   impossible to touch the store without it — e.g. enforce inside
   `store.memory_dir()` / `projects.load_registry()`, or introduce a single
   `open_store(root)` handle that every path must obtain. This closes Tier 1 and
   Tier 2 together and makes a future omission a type error rather than a silent
   hole.
2. **Keep it per-command** and simply add the 13 missing calls. Cheaper now,
   but it re-arms the same footgun: nothing stops the 14th path from forgetting.

Two constraints any fix must respect:

- **MCP cannot hard-fail at startup.** Spec §13 requires `resources/list` to
  always answer with a valid array and *never* error — Codex drops a server that
  errors there. So an MCP schema refusal must surface as a **structured tool
  error**, not a startup exception.
- **`uninstall --purge-store` probably should *not* be guarded.** Deleting a store
  you cannot understand is the one operation that is safe regardless — arguably it
  is the correct escape hatch *from* a newer-schema store. If so, that exemption
  must be written into spec §10 explicitly, not left implicit.

**Whichever path is chosen, it needs an ADR** — it either changes the contract
(exemptions) or changes the architecture (a store chokepoint). It cannot be
settled by silently editing code.
