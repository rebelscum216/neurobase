"""Project registry + resolution (decision D6, spec §10).

``<root>/registry.toml`` maps a project slug to one or more repo roots.
Resolution walks from a cwd to its git *common* dir (so worktrees collapse to
one project) and longest-prefix-matches against the registry; a non-git cwd
matches by plain path prefix. No match ⇒ untracked (hooks silently no-op).
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import tomli_w

from neurobase.core.store import SLUG_RE, InvalidSlugError

_SLUG_INVALID = re.compile(r"[^a-z0-9]+")


class ProjectSlugCollisionError(ValueError):
    """The slugified name already maps to a different root."""


def slugify(name: str) -> str:
    """Lowercase; every run of non-``[a-z0-9]`` chars becomes one ``-``; trim
    leading/trailing ``-`` (spec §10)."""
    return _SLUG_INVALID.sub("-", name.lower()).strip("-")


def _registry_path(root: Path) -> Path:
    return root / "registry.toml"


def load_registry(root: Path) -> dict[str, list[str]]:
    """``{slug: [roots...]}``. Missing file ⇒ empty registry."""
    path = _registry_path(root)
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    projects = data.get("projects", {})
    return {slug: list(entry.get("roots", [])) for slug, entry in projects.items()}


def _write_registry(root: Path, registry: dict[str, list[str]]) -> None:
    path = _registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"projects": {slug: {"roots": roots} for slug, roots in registry.items()}}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(tomli_w.dumps(doc).encode("utf-8"))
    tmp.replace(path)


def git_common_root(cwd: Path) -> Path | None:
    """The repo root as derived from ``git rev-parse --git-common-dir`` — the
    *common* dir so worktrees collapse to the same project. ``None`` if
    ``cwd`` isn't inside a git repo (or git isn't available)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = cwd / common_dir
    return common_dir.resolve().parent


def register_project(root: Path, cwd: Path, slug: str | None = None) -> str:
    """Register ``cwd`` (or its git common root) under ``slug`` (derived from
    the directory name if not given). Raises ``ProjectSlugCollisionError`` if
    the derived slug already maps to a different root — the caller (CLI)
    should re-prompt with an explicit ``slug``."""
    project_root = git_common_root(cwd) or cwd.resolve()
    final_slug = slugify(slug) if slug else slugify(project_root.name)
    if not SLUG_RE.match(final_slug):
        source = slug if slug else project_root.name
        raise InvalidSlugError(
            f"derived project slug {final_slug!r} (from {source!r}) is invalid "
            "— must match ^[a-z0-9-]+$; pass an explicit --slug"
        )
    registry = load_registry(root)
    existing_roots = registry.get(final_slug, [])
    root_str = str(project_root)
    if existing_roots and root_str not in existing_roots and slug is None:
        raise ProjectSlugCollisionError(
            f"slug {final_slug!r} is already registered to {existing_roots} "
            f"— pass an explicit slug for {project_root}"
        )
    if root_str not in existing_roots:
        existing_roots.append(root_str)
    registry[final_slug] = existing_roots
    _write_registry(root, registry)
    return final_slug


def resolve_project(root: Path, cwd: Path) -> str | None:
    """Longest-prefix match of ``cwd``'s (git-collapsed) path against every
    registered root. ``None`` ⇒ untracked."""
    candidate = git_common_root(cwd) or cwd.resolve()
    registry = load_registry(root)
    best_slug: str | None = None
    best_len = -1
    for slug, roots in registry.items():
        for registered in roots:
            registered_path = Path(registered)
            try:
                candidate.relative_to(registered_path)
            except ValueError:
                continue
            length = len(str(registered_path))
            if length > best_len:
                best_len = length
                best_slug = slug
    return best_slug
