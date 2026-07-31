# ADR-0027: the Projects directory ships read-only; registry editing is deferred

- **Status:** Accepted
- **Date:** 2026-07-31
- **Resolves:** Phase P of the app-shell plan
  ([`docs/notes/2026-07-16-webui-app-shell-plan.md`](../notes/2026-07-16-webui-app-shell-plan.md))
- **Extends:** [ADR-0021](0021-webui-surface.md) (the §14 write-surface posture),
  [ADR-0015](0015-store-chokepoint-handle.md) (all store access through a handle)
- **Supersedes:** none

## Context

`registry.toml` decides which folders Neurobase captures from. It had exactly one
writer — `projects.register_project`, reachable only through `neurobase enable`
run from inside the target directory — and **no inverse at all**. Reading the set
was barely better: `neurobase status` reports the *one* project the current
directory resolves to; nothing listed all of them, and the three states that
determine whether a registered project actually does anything (root still
existing, `[enable] denylist` suppression, `auto_enable_roots` coverage) were
visible only by reading config and reasoning about it. A project tree with no
registry entry — which happens; the dev store has one — was invisible everywhere.

Phase P was specified and built as a full read/write registry surface: list every
project, add a folder, attach a root, drop a root, deregister, and
deregister-plus-delete-the-memory-tree behind a typed confirmation.

**Three review rounds found seven blockers, and after the first, every one was
the same defect.** `projects.load_registry` preserves arbitrary hand-edited
content *by contract* — any TOML table key, any string inside `roots` — and it is
documented to do exactly that, so one bad entry cannot untrack every other
project. Each mutating route, and the display code feeding it, quietly assumed
that output was well-formed:

- an entry keyed `[projects."bad_slug"]` made every store accessor raise, and
  `memory_dir` sat outside the fail-soft guard — `GET /projects` 500'd, hiding
  every healthy project behind the one entry the user came to delete;
- a linked worktree outside the store whose *git common root* was the store
  passed a submitted-path check and registered the store root itself, because the
  path validated and the path registered are not the same path;
- a corrupt `registry.toml` made the delete route destroy the tree and *then*
  fail on the registry rewrite;
- a key containing `/` rendered an action URL that could not route, so the
  "always removable" guarantee held only for URL-safe keys;
- a root containing an embedded NUL raised `ValueError` out of a path resolution
  that caught only `OSError`/`RuntimeError`, taking down the page.

The reading half is worth having on its own and its failure mode is bounded: a
bad entry degrades a row. The editing half needs hostile-registry handling to be
the *subject* of its design rather than a series of patches discovered one review
round at a time.

## Decision

**Ship `GET /projects` read-only. Defer registry editing to its own branch.**

The directory lists every registered project — slug, each registered root,
whether that root still exists, denylist/auto-enable coverage, raw/curated/node
counts — plus every project tree with no registry entry. Every per-project store
read is fail-soft: a bad project degrades its own row, never the page. A registry
key that is not a valid slug, and a root that is not a usable path, are each
rendered *states* rather than exceptions.

No mutating route exists on this surface. Changing the list means `neurobase
enable` or editing `registry.toml` by hand, and the page says so.

**What the deferred branch inherits** (built, reviewed, and removed from this
one): the five mutating routes, `projects.remove_root` / `deregister_project`,
`StoreHandle.delete_project_tree`, `neurobase disable`, and the two known-gaps
that only exist once a delete does (the write lock living inside the tree it
removes; store-wide proposals dangling after a project is deleted). Its design
starts from the rule this round established: **treat every registry key and root
as arbitrary bytes**, and give actions an identity that does not depend on the
key's shape.

**What stays here**, because it fixes pre-existing behavior rather than enabling
new editing: `register_project` now raises `ProjectInsideStoreError` when the
root it is about to register is at or under the store. That guard sits at the
single registry write, so it binds `neurobase enable` and auto-enable too — both
of which had the hole independently of any web UI. `project_root_for` and
`is_inside_store` are its public parts, and `_config_hit` now fails closed on a
path that cannot be resolved at all.

## Consequences

- The registry is inspectable without reading TOML, and the three states that
  silence a registered project are visible on the row instead of inferable.
- Unregistered project trees surface for the first time — comparing
  `list_project_trees()` (disk) against `load_registry()` (registry) is the only
  way to see one.
- `neurobase enable` can no longer register the store as a project. That was
  reachable before this branch, from the CLI, with no web layer involved.
- **Removing a project still has no supported path.** Deregistering means editing
  `registry.toml` by hand until the editing branch lands. This is the honest cost
  of the deferral and the reason it is a deferral rather than a cancellation.
- The read surface is a place the editing work can land incrementally, and its
  test suite already encodes the hostile-input cases that editing must satisfy.

## Alternatives considered

- **Ship editing with the three round-3 blockers patched.** Rejected. Each round
  patched the instance in front of it and the next round found another instance
  of the same class; a fourth round was more likely than not. The pattern, not
  any individual defect, is what changed the decision.
- **Ship nothing until editing is safe.** Rejected: the reading half solves a
  real problem today (nothing lists the projects, and orphaned trees are
  invisible), and its risk is a degraded row.
- **Keep `neurobase disable` and defer only the web routes.** Rejected: it reads
  strictly and shares the corrupt-registry failure mode, so it belongs with the
  editing work rather than half-landed here.
- **Make `load_registry` validate keys and roots.** Tempting, and wrong: its
  leniency is the documented §10 guarantee that one hand-mangled entry untracks
  *that* project rather than killing capture everywhere. The fix belongs at the
  surfaces, not by hardening the accessor into dropping data.
