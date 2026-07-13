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

### G1 — the D11 store-schema guard is missing on the read-only paths

- **status:** open
- **severity:** major — spec §10 says *"refuse to **operate** on a schema newer
  than the binary."* Not "refuse to mutate." Reading a newer-schema store
  violates the contract as written, so this is a `MUST` violation, not a
  cosmetic inconsistency.
- **locations:**
  - `src/neurobase/cli/__init__.py:100-108` — `status --recommender`
  - `src/neurobase/mcp/server.py` — the whole `mcp serve` read surface
- **found:** 2026-07-12 by Codex (how-it-works doc review, finding 2); **scope
  corrected 2026-07-13** by Codex (known-gaps review, finding 1), which caught
  that the original entry both understated the surface and misread the contract.

**What's wrong.** Two store entry points read a store whose on-disk schema may be
newer than the running binary understands, without ever calling the D11 guard.

*1 — `status --recommender`.* `status()` resolves the root, branches on
`recommender`, and `return`s through `_print_recommender_metrics()` **before**
reaching its `_check_store_schema()` call. The metrics path never calls the guard
itself:

```python
resolved_root = store.resolve_root(root)
if recommender:
    _print_recommender_metrics(resolved_root)   # ← no schema check
    return
...
_check_store_schema(resolved_root)              # ← only the normal path
```

*2 — `mcp serve`.* `mcp/server.py` never calls `ensure_store_metadata` at all.
`build_server()` resolves the root and registers tools that read store state —
`memory_search`, `memory_read_node`, `memory_list_projects`,
`recommendations_list`, the optional node resources, and the `recall` prompt —
all unguarded. (`memory_remember` *is* guarded, but only transitively and by
accident: it calls `store.ensure_tree`, which calls `ensure_store_metadata`
internally. The write is safe; every read beside it is not.)

**Audit of every store entry point** (as of 2026-07-13):

| Entry point | Guarded? | How |
|---|---|---|
| `enable`, `status` (normal), `curate`, `seed`, all 6 `recommend` subcommands | ✅ | `_check_store_schema()` |
| Claude scribe, Codex scribe, `recall_common` (session-start inject) | ✅ | `store.ensure_store_metadata()` direct |
| MCP `memory_remember` | ✅ | transitively, via `ensure_tree` |
| `doctor` | n/a | *reports* schema as a check rather than refusing — correct by design |
| **`status --recommender`** | ❌ | returns before the guard |
| **`mcp serve` read tools + resources + prompt** | ❌ | never guarded |

**Why it hasn't been fixed.** It surfaced during a **docs-only** review where a
code change was out of scope, and the follow-up (this file) was also docs-only.
No decision was ever made to exempt these paths — the guard was simply applied
per-command, ad hoc, and these two were missed.

**Do not repeat the original mistake.** The first version of this entry rated the
gap `minor` on the reasoning that the paths are read-only and therefore "cannot
mutate an incompatible store — which is precisely what D11 exists to prevent."
**That rationale was invented.** Spec §10's actual words are *"refuse to operate
on a schema newer than the binary,"* with no read-only exemption anywhere in the
contract. Bounding the severity that way was reading the spec to fit the code.

**Fix direction.** Two options, and the *first step is deciding which* — do not
patch the code until the contract question is settled:

1. **Honor the contract as written** (default, and what the spec currently says):
   guard both paths. Move `_check_store_schema()` above the `if recommender:`
   branch in `status()`; add an `ensure_store_metadata()` call in
   `mcp/server.py`'s `build_server()` (deciding what a schema refusal *looks
   like* over MCP — a hard startup failure would violate spec §13's "`resources/list`
   must never error" invariant, so it likely needs to surface as a structured
   tool error instead). Add tests alongside `tests/test_hook_schema_guard.py`,
   which already enforces this system-wide for hooks.
2. **Deliberately exempt read-only paths** — defensible (a read can't corrupt),
   but that is a **contract change**: it requires amending spec §10 *and* an ADR
   recording the decision. It cannot be adopted by leaving the code as-is and
   calling it intentional. If this route is taken, this gap closes as `wontfix`
   with a pointer to the ADR.
