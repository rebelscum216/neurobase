"""Folder-scoped auto-enable (ADR-0019).

``neurobase enable`` is per-repo and opt-in by design (spec §4/§10: a hook
captures only when the resolved project's memory tree exists). Folder-scoped
auto-enable relocates that consent from per-repo to per-folder: the user names an
``auto_enable_roots`` directory once (in ``config.toml``), and any git repo
beneath it is registered as its own project — and given its memory tree — the
first time a hook fires there.

This module is the single seam the two scribes (§4/§5 capture) and recall (§3
inject) route project resolution through. The ``denylist`` is a *live* gate
(review F4): a denylisted repo neither auto-enables nor keeps capturing if it was
already registered — so editing one config line revokes capture. Everything
downstream is unchanged, because after auto-enable the tree exists and the
ordinary opt-in path takes over.
"""

from __future__ import annotations

from pathlib import Path

from neurobase.core import projects, store
from neurobase.core.store_handle import StoreMode, open_store


def resolve_or_auto_enable(
    root: Path,
    cwd: Path,
    *,
    auto_enable_roots: list[str],
    denylist: list[str],
) -> str | None:
    """The project slug for ``cwd``, auto-registering it first when it is
    untracked but qualifies for folder-scoped auto-enable. ``None`` means the cwd
    is untracked-and-not-qualifying, *or* it is denylisted — the caller no-ops
    exactly as before this feature existed.

    Fail-safe like the hooks that call it: a store whose schema is newer than this
    binary supports, a slug collision, an un-sluggable repo name, or any
    filesystem error while writing the tree/registry all yield ``None`` rather
    than raising."""
    # READ guards the schema without writing: a too-new store fails closed here,
    # before any registry mutation, and an untracked non-qualifying cwd never
    # creates store.toml as a side effect (ADR-0015 D11).
    try:
        handle = open_store(root, StoreMode.READ)
    except store.UnsupportedSchemaError:
        return None

    # F4: the denylist is a *live* gate, checked first so it wins — a denylisted
    # repo neither auto-enables nor keeps capturing when already registered.
    if projects.is_denylisted(cwd, denylist):
        return None

    existing = handle.resolve_project(cwd)
    if existing is not None:
        return existing

    repo_root = projects.auto_enable_root_for(cwd, auto_enable_roots, denylist)
    if repo_root is None:
        return None

    # Qualifies. Create the tree *before* committing the registry entry, all inside
    # one guarded block, so no partial state survives a failure (review F2):
    #   - an un-sluggable name, a slug collision, a too-new store, or any OSError
    #     → return None, leaving the store as we found it;
    #   - writing the registry entry only *after* the tree exists means a tree
    #     failure can never leave a registered-but-treeless project — which would
    #     otherwise match resolve_project forever and never be retried, silently
    #     killing capture for that repo.
    # This is the only place a hook mutates the registry — reached once per repo,
    # on the first session under an auto_enable_root, through a WRITE handle so the
    # D11 guard runs before the mutation (ADR-0015).
    try:
        # Derive (and validate) the slug BEFORE opening the WRITE handle, so an
        # un-sluggable repo name skips out without even creating store.toml (a
        # pristine store stays pristine on a skipped enable — review B3).
        slug = projects.derive_slug(repo_root)
        writer = open_store(root, StoreMode.WRITE)
        writer.ensure_tree(slug)
        writer.register_project(repo_root)
    except (
        store.UnsupportedSchemaError,
        projects.ProjectSlugCollisionError,
        store.InvalidSlugError,
        OSError,
    ):
        return None
    return slug
