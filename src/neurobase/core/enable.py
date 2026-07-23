"""Folder-scoped auto-enable (prototype; pending ADR).

``neurobase enable`` is per-repo and opt-in by design (spec §4/§10: a hook
captures only when the resolved project's memory tree exists). Folder-scoped
auto-enable relocates that consent from per-repo to per-folder: the user names an
``auto_enable_roots`` directory once (in ``config.toml``), and any git repo
beneath it is registered as its own project — and given its memory tree — the
first time a hook fires there.

This module is the single seam both scribes (§4/§5 capture) and recall (§3 inject)
route project resolution through. It resolves the *registered* project first; only
a genuinely untracked cwd that also qualifies for auto-enable triggers a
registration. Everything downstream is unchanged, because after auto-enable the
tree exists and the ordinary opt-in path takes over.
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
    is untracked and did not qualify — the caller no-ops exactly as before this
    feature existed.

    Fail-safe like the hooks that call it: a store whose schema is newer than this
    binary supports (guarded through a READ handle before any write), or a slug
    collision / un-sluggable repo name, yields ``None`` rather than raising."""
    # READ guards the schema without writing: a too-new store fails closed here,
    # before any registry mutation, and an untracked non-qualifying cwd never
    # creates store.toml as a side effect (ADR-0015 D11).
    try:
        handle = open_store(root, StoreMode.READ)
    except store.UnsupportedSchemaError:
        return None
    existing = handle.resolve_project(cwd)
    if existing is not None:
        return existing

    repo_root = projects.auto_enable_root_for(cwd, auto_enable_roots, denylist)
    if repo_root is None:
        return None

    # Qualifies: register + create the tree through a WRITE handle so the D11
    # schema guard runs *before* the registry/tree are touched (ADR-0015). This is
    # the only place a hook mutates the registry — reached once per repo, on the
    # first session under an auto_enable_root.
    try:
        writer = open_store(root, StoreMode.WRITE)
        slug = writer.register_project(repo_root)
    except store.UnsupportedSchemaError:
        return None
    except (projects.ProjectSlugCollisionError, store.InvalidSlugError):
        # A slug clash with a different already-registered repo, or a repo name
        # that can't be slugified, is not something to auto-guess: skip and let the
        # user run `neurobase enable --slug ...` deliberately.
        return None
    writer.ensure_tree(slug)
    return slug
