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

### G1 — the D11 store-schema guard is enforced per-command by hand, not at the store boundary

- **status:** open
- **severity:** major — spec §10 says *"refuse to **operate** on a schema newer
  than the binary."* No read-only exemption exists in the contract. At least one
  path **mutates** a newer-schema store before the guard runs, which is the exact
  outcome D11 exists to prevent.
- **found:** 2026-07-12 by Codex (how-it-works review); scope corrected across
  three rounds of the [known-gaps review](reviews/README.md) — see *Provenance*
  below, which is itself part of the finding.

**Root cause.** `store.ensure_store_metadata()` — the guard — has exactly **five
call sites**:

| Call site | Reached by |
|---|---|
| `core/store.py:118` (inside `ensure_tree`) | anything that creates a tree |
| `cli/__init__.py:57` (`_check_store_schema`) | the CLI commands that remember to call it |
| `adapters/recall_common.py:81` (`build_context`) | session-start recall |
| `adapters/claude/scribe.py:171` | Claude capture |
| `adapters/codex/scribe.py:245` | Codex capture |

Nothing enforces the guard *at the store boundary*. `store.memory_dir()` and
`projects.load_registry()` will happily read or write a store of any schema. So
protection is opt-in per command, and a path is protected only if its author
remembered — which several did not, and which nothing prevents the next author
from forgetting.

**Confirmed defects.** Verified against source; these are examples of the root
cause, not an exhaustive census (see *Provenance*):

1. **`init` (guided) mutates before it guards — the worst case.** `_init_guided`
   calls `projects.register_project()`, which reads *and unconditionally rewrites*
   `<root>/registry.toml` (`core/projects.py:101`, `_write_registry`), and only
   *then* calls `store.ensure_tree()`, where the guard finally runs. A
   newer-schema store is **written to** before it is ever checked.
2. **`mcp serve` never guards at all.** `mcp/server.py` contains no call to
   `ensure_store_metadata`. `build_server()` resolves the project from
   `registry.toml` at startup, and `memory_search`, `memory_read_node`,
   `memory_list_projects`, `recommendations_list`, and (when
   `[mcp] expose_resources` is on) the node resources all read store state with no
   check. `memory_remember` is the partial exception: its *write* is guarded via
   `ensure_tree`, but reads precede it.
3. **`status --recommender` returns before the guard.** `status()` branches on
   `recommender` and returns through `_print_recommender_metrics()` *before*
   reaching its `_check_store_schema()` call. It reads `proposals/` and the ledger
   unconditionally, and — via `metrics._recurrence_reduction` →
   `corpus.load_corpus` — `registry.toml`, `curated/`, and `raw/` as well.
4. **`uninstall --purge-store` deletes an unguarded store.** `shutil.rmtree(<root>)`
   with no schema check. (Arguably *correct* — see the fix constraints.)
5. **Pre-guard registry reads are pervasive.** `projects.resolve_project()` reads
   `registry.toml` and runs first in most flows — including `status`, `curate`, and
   every hook — so even "guarded" commands typically touch the store before the
   guard. `enable`'s own comment (`# before registry.toml is touched`,
   `cli/__init__.py:76`) shows registry reads were *intended* to sit behind the
   guard.

**Provenance — and a caution about this entry.** The first three versions of G1
each made a confident coverage claim, and each was wrong: it called the gap unique
to `status --recommender` (it isn't), then bounded the severity with a
read-only-exemption rationale that **is not in the spec** (invented), then shipped
an "exhaustive 39-entry-point audit" whose tiers didn't reconcile with their own
counts and which misfiled the `init` mutation above as a benign read. The lesson is
recorded here deliberately: **a hand-maintained census of call paths is the wrong
artifact** — it is unverifiable in review, it rots on the next commit, and three
attempts produced three wrong tables. If exhaustive accounting is ever genuinely
needed, it must be a **committed, runnable enumerator** (with a stated definition of
"entry point"), not prose. Until then this entry deliberately claims only the root
cause and named, individually-verified defects.

**Fix direction.** Do not patch call sites one by one — that is the process that
produced this. The decision is architectural and comes first:

1. **Centralize the guard at the store boundary** (recommended). Make it impossible
   to touch the store without it — enforce inside `store.memory_dir()` /
   `projects.load_registry()`, or introduce a single `open_store(root)` handle every
   path must obtain. A future omission then becomes a type error, not a silent hole.
2. **Keep it per-command** and add the missing calls. Cheaper today; re-arms the
   same footgun tomorrow.

Constraints any fix must respect:

- **MCP cannot hard-fail at startup.** Spec §13 requires `resources/list` to always
  answer with a valid array and never error (Codex drops a server that errors
  there), so an MCP refusal must surface as a **structured tool error**.
- **`uninstall --purge-store` probably *should* be exempt.** Deleting a store you
  cannot parse is the safe escape hatch *from* a newer-schema store. If that
  exemption is wanted, write it into spec §10 explicitly rather than leaving it
  implicit.
- **`doctor` must keep reporting rather than refusing** — that is its job. Note it
  currently *re-implements* the schema comparison inline (`cli/diagnostics.py`)
  instead of reusing the guard; collapse that duplication when centralizing, or the
  two will drift.

**This needs an ADR** — either route changes the contract (exemptions) or the
architecture (a store chokepoint). It cannot be settled by quietly editing code.
