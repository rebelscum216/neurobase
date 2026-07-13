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

### G1 — `status --recommender` bypasses the D11 store-schema guard

- **status:** open
- **severity:** minor (correctness inconsistency; no data-loss risk)
- **location:** `src/neurobase/cli/__init__.py:100-108`
- **found:** 2026-07-12, by Codex during the
  [how-it-works doc review](reviews/2026-07-12-how-it-works-doc.md) (finding 2)

**What's wrong.** Every other command that touches the store calls
`_check_store_schema()` before reading or writing it, so a store written by a
*newer* `neurobase-cli` is refused rather than operated on (spec §10, decision
D11). `status --recommender` does not. `status()` resolves the root, branches on
`recommender`, and `return`s through `_print_recommender_metrics(resolved_root)`
**before** reaching its `_check_store_schema()` call — and the metrics path never
calls the guard itself:

```python
resolved_root = store.resolve_root(root)
if recommender:
    _print_recommender_metrics(resolved_root)   # ← no schema check
    return
...
_check_store_schema(resolved_root)              # ← only the normal path
```

**Why it hasn't been fixed.** It surfaced during a **docs-only** review, where a
code change was out of scope. It is also the mildest form of the problem: the
recommender-metrics path is strictly read-only (`metrics.compute_metrics` never
writes), so it cannot *mutate* an incompatible store — which is precisely what
D11 exists to prevent. It reads a newer-schema store's ledger/proposals and may
print wrong numbers; it cannot corrupt anything.

**Fix direction.** Move the `_check_store_schema(resolved_root)` call above the
`if recommender:` branch so it guards both paths uniformly. Add a test alongside
`tests/test_hook_schema_guard.py` (which already enforces this system-wide for
hooks) asserting `status --recommender` exits 1 on a newer-schema store. Confirm
the D11 contract in spec §10 actually intends to cover read-only paths — if it
deliberately does *not*, then the exemption should be written into the spec and
this becomes a `wontfix` rather than a fix.
