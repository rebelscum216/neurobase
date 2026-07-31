# ADR-0027: the registry is editable from the web UI, including deleting a project's memory

- **Status:** Accepted
- **Date:** 2026-07-31
- **Resolves:** Phase P of the app-shell plan
  ([`docs/notes/2026-07-16-webui-app-shell-plan.md`](../notes/2026-07-16-webui-app-shell-plan.md))
- **Extends:** [ADR-0021](0021-webui-surface.md) (the §14 write-surface posture),
  [ADR-0015](0015-store-chokepoint-handle.md) (all store access through a handle)
- **Supersedes:** none

## Context

`registry.toml` decides which folders Neurobase captures from. Until now it had
exactly one writer — `projects.register_project`, reachable only through
`neurobase enable`, run from inside the target directory — and **no inverse at
all**. There was no `deregister`, no `remove_root`, nothing. A folder registered
by a typo, a repo that moved, a project enabled once to try it out: all
permanent, editable only by hand-editing TOML in the store.

Reading the set was barely better. `neurobase status` reports the *one* project
the current directory resolves to; nothing anywhere listed all of them. Three
states that determine whether a registered project actually does anything —
the root still existing on disk, an `[enable] denylist` entry suppressing it,
`auto_enable_roots` covering it — were each visible only by reading config and
reasoning about it. And a project tree with no registry entry (which happens:
this store has one) was invisible to every surface while its facts sat there
unreachable.

The web UI already renders four read surfaces over this store. Putting the
directory there is the obvious move. What needed deciding is the write half — in
particular whether a **browser page** should be able to delete captured memory,
given that ADR-0021 admits localhost alone does not stop a drive-by POST.

## Decision

Ship `GET /projects` as a full read/write registry surface, with five mutations:
add a folder, attach a second root, drop a root, deregister, and
deregister-plus-delete-the-memory-tree. The behavioral contract lands in spec
appendix **§14** in the same commit, as ADR-0021 requires of every route added to
this surface.

**Destructive deletion is in scope, deliberately.** The alternative — read/write
for the safe operations, "go hand-edit TOML and `rm -rf`" for the destructive one
— is worse on every axis that matters: it sends the user to do the *irreversible*
half with no guard rails, no visibility into what they are about to destroy, and
a real chance of removing the wrong directory. A confirmed, inspectable delete
that shows its blast radius first is safer than the manual `rm` it replaces.

What makes it acceptable:

- **Two steps, not one.** A GET renders exactly what will be destroyed — tree
  path, registered roots, live raw/curated/node counts — and offers
  deregister-without-delete as the visible alternative. Only then a POST.
- **A typed confirmation, compared server-side.** The user types the slug back.
  This is not decoration: it is what makes the action unreachable by a single
  crafted POST in a way a hidden field would not be, and it forces the user to
  read *which* project they are on.
- **The guards live in `core`, not the handler.**
  `StoreHandle.delete_project_tree` validates the slug against `^[a-z0-9-]+$`
  (so nothing with a separator or `..` can name a target) and re-proves
  containment **after resolution** (so a symlinked project directory cannot
  redirect the `rmtree` out of the store). Both hold for the CLI path too.
- **Deregister is the default the page steers toward**, and it destroys nothing:
  the tree stays, and re-registering the folder restores the project intact.

**Removal semantics, decided here:**

- Removing a project's **last root deregisters it** rather than leaving
  `roots = []`. A zero-root entry can never be matched by `resolve_project`, so
  it would be an invisible project that every sweep still walks.
- `remove_root` matches the **exact stored string**, never a resolved path. The
  case removal matters most for is a root whose directory is gone; resolving
  would make that the one case that could not be undone.
- Both removals **read strictly** (`_read_registry`, like `register_project`) and
  raise on a corrupt registry. These reads exist only to be written back, so the
  fail-soft `load_registry` posture would rewrite the file from `{}` and silently
  drop every other project's roots — the one place where "treat a corrupt
  registry as empty" is exactly wrong.

**The CLI gains `neurobase disable`** over the same primitives. ADR-0021 makes
`webui/` a *peer* of `cli/`; a removal path only the browser could reach would
invert that.

## Consequences

- The registry is inspectable and editable without hand-editing TOML, and the
  three states that silence a registered project are visible on the row instead
  of inferable from config.
- Unregistered project trees surface for the first time. Comparing
  `list_project_trees()` (disk) against `load_registry()` (registry) is the only
  way to see one, and this is the only surface that does it.
- `core/projects.py` gains the inverse of `register_project` — available to every
  caller, not just this page.
- `is_denylisted` / `auto_enable_root_for` are refactored onto shared
  `denylist_hit` / `auto_enable_hit` helpers that return the *matching configured
  entry* for display. Behavior is unchanged; the display path deliberately does
  **not** call `git_common_root`, which would mean a `git rev-parse` subprocess
  per registered root on every render.
- Two new known-gaps: **G8**, the delete holds a lock that lives inside the tree
  it removes (a narrow race, recorded when introduced rather than found later);
  **G9**, store-wide proposals are not cascaded and are left dangling.
- Future destructive routes have a shape to copy: preview → typed confirmation →
  core-side guards. Nothing in this ADR blesses an *automatic* deletion path;
  G8 in particular would become real if one landed.

## Alternatives considered

- **Read-only directory, edits via CLI only.** Rejected: it leaves the actual
  problem (no inverse exists anywhere) unsolved, and a read-only page that shows
  you a wrong entry you cannot fix is a worse experience than no page.
- **No destructive delete on the web surface.** Rejected above — it pushes the
  irreversible operation to a raw `rm -rf` with no preview.
- **A soft-delete / trash for project trees.** Real appeal, but it needs a
  restore path, a retention policy, and a reaper to be honest, and the store
  already has a tombstone mechanism scoped to *facts* whose semantics do not
  extend to whole projects. Deregister-keeps-memory already covers the "I might
  want this back" case, which is the one users actually hit.
- **Cascading the delete to proposals and the ledger.** Rejected for now: it
  would destroy the record of skills that are still installed on disk. Recorded
  as G9 and left to the schema-2 profile work (ADR-0016), where proposal scoping
  has to be settled anyway.
- **Folding this into the planned Phase T `/status`.** Rejected: that page is
  diagnostics (doctor checks, brain resolution, run buttons) and pulling its
  prerequisite refactor forward would have doubled this branch.
