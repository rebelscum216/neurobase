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
from typing import Any

import tomli_w

from neurobase.core.store import SLUG_RE, InvalidSlugError

_SLUG_INVALID = re.compile(r"[^a-z0-9]+")


class ProjectSlugCollisionError(ValueError):
    """The slugified name already maps to a different root."""


class RegistryShapeError(ValueError):
    """``registry.toml`` parsed as TOML but is not a registry: ``projects`` is not
    a table, an entry is not a table, or a ``roots`` list holds a non-string.

    Distinct from ``tomllib.TOMLDecodeError`` because the two read paths need to
    tell "this file is not valid TOML" from "this file is valid TOML that means
    nothing here" — but only the *strict* path ever sees either."""


def slugify(name: str) -> str:
    """Lowercase; every run of non-``[a-z0-9]`` chars becomes one ``-``; trim
    leading/trailing ``-`` (spec §10)."""
    return _SLUG_INVALID.sub("-", name.lower()).strip("-")


def _registry_path(root: Path) -> Path:
    return root / "registry.toml"


def _load_toml(root: Path) -> dict[str, Any]:
    """The file read + TOML parse. Missing file ⇒ ``{}``; unreadable ⇒ ``OSError``;
    unparseable ⇒ ``tomllib.TOMLDecodeError``."""
    path = _registry_path(root)
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _shape(data: dict[str, Any], *, strict: bool) -> dict[str, list[str]]:
    """``{slug: [roots...]}`` out of parsed TOML.

    Valid TOML is not automatically a registry: ``projects`` may not be a table,
    an entry may not be a table, and a ``roots`` list may hold non-strings — all
    reachable by hand-editing, and each one detonates *later* (in ``Path()``, on
    the capture path) rather than at the read if it is passed through.

    ``strict`` decides what a violation means. Strict raises. Lenient **skips the
    offending entry** and keeps the rest, rather than emptying the whole
    registry: ``resolve_project`` runs at hook time, so one hand-mangled entry
    must untrack *that* project, not silently kill capture for every other one —
    the same posture the spec takes for a malformed project tree. A ``projects``
    that is not a table has no entries to salvage and reads as empty."""
    table = data.get("projects", {})
    if not isinstance(table, dict):
        if strict:
            raise RegistryShapeError(f"'projects' is {type(table).__name__}, not a table")
        return {}
    registry: dict[str, list[str]] = {}
    for slug, entry in table.items():
        roots = entry.get("roots", []) if isinstance(entry, dict) else None
        if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
            if strict:
                raise RegistryShapeError(f"entry {slug!r} has no valid 'roots' list of strings")
            continue  # skip this project, keep the others
        registry[str(slug)] = list(roots)
    return registry


def _read_registry(root: Path) -> dict[str, list[str]]:
    """Strict read, for the read-for-rewrite path only. Raises on a registry that
    is unreadable (``OSError``), unparseable (``tomllib.TOMLDecodeError``), or not
    shaped like a registry (:class:`RegistryShapeError`)."""
    return _shape(_load_toml(root), strict=True)


def load_registry(root: Path) -> dict[str, list[str]]:
    """``{slug: [roots...]}``. A missing, unreadable, unparseable, or
    wrongly-shaped ``registry.toml`` all read as empty — never as an exception.

    **Spec §10:** registry parseability is a separate, fail-soft concern — a
    corrupt ``registry.toml`` is not folded into the schema guard; registry reads
    (``load_registry``, ``resolve_project``) *treat it as empty*. The contract
    lives here, at the named accessor, rather than in each reader: a rule that
    every caller has to remember is one a future caller will forget, and the
    failure mode is a whole surface crashing on a file it only wanted to read.
    "Corrupt" covers valid TOML of the wrong shape too — see :func:`_shape`,
    which is where the file-level and shape-level halves of that guarantee meet.

    Read-for-**rewrite** is the deliberate exception and does not come through
    here — see :func:`register_project`, which must not overwrite a registry it
    could not fully parse."""
    try:
        data = _load_toml(root)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return _shape(data, strict=False)


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


def derive_slug(project_root: Path, slug: str | None = None) -> str:
    """The slug :func:`register_project` would assign for ``project_root``,
    validated but **without** writing the registry.

    Split out so a caller can learn (and create the tree for) the slug *before*
    committing the registry entry — auto-enable relies on this ordering so a tree
    failure can never leave a registered-but-treeless project (review F2). Raises
    :class:`InvalidSlugError` for a name that can't be slugified."""
    final_slug = slugify(slug) if slug else slugify(project_root.name)
    if not SLUG_RE.match(final_slug):
        source = slug if slug else project_root.name
        raise InvalidSlugError(
            f"derived project slug {final_slug!r} (from {source!r}) is invalid "
            "— must match ^[a-z0-9-]+$; pass an explicit --slug"
        )
    return final_slug


def register_project(root: Path, cwd: Path, slug: str | None = None) -> str:
    """Register ``cwd`` (or its git common root) under ``slug`` (derived from
    the directory name if not given). Raises ``ProjectSlugCollisionError`` if
    the derived slug already maps to a different root — the caller (CLI)
    should re-prompt with an explicit ``slug``.

    **Reads strictly**, unlike :func:`load_registry`: this read exists only to be
    written back, so treating an unparseable registry as empty would rewrite the
    file from ``{}`` and silently drop every other project's roots. A corrupt
    registry raises here instead — ``core/enable.py`` already catches
    ``TOMLDecodeError``/``OSError`` and fails closed, which is the documented
    auto-enable posture."""
    project_root = git_common_root(cwd) or cwd.resolve()
    final_slug = derive_slug(project_root, slug)
    registry = _read_registry(root)
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


def remove_root(root: Path, slug: str, project_root: str) -> bool:
    """Drop one registered root from ``slug``. Returns ``False`` (writing nothing)
    when the slug isn't registered or doesn't carry that root.

    ``project_root`` is matched as an **exact string** against what the registry
    stores, not as a resolved path: the registry holds whatever string was written
    (``register_project`` writes ``str(git_common_root(...))``, a hand-edit may hold
    ``~/x``), and a caller removing a root is always echoing back a value it just
    read from :func:`load_registry`. Resolving here would make removal depend on
    whether the directory still exists — exactly the case where removal matters most.

    Removing a slug's **last** root deletes the entry outright rather than leaving
    ``roots = []`` behind: a zero-root entry can never be matched by
    :func:`resolve_project`, so it would be an invisible project that every sweep
    still walks.

    **Reads strictly**, like :func:`register_project` and for the same reason — this
    read exists only to be written back, so an unparseable registry must raise here
    rather than be treated as empty and rewritten from ``{}``, dropping every other
    project's roots."""
    registry = _read_registry(root)
    roots = registry.get(slug)
    if roots is None or project_root not in roots:
        return False
    remaining = [entry for entry in roots if entry != project_root]
    if remaining:
        registry[slug] = remaining
    else:
        del registry[slug]
    _write_registry(root, registry)
    return True


def deregister_project(root: Path, slug: str) -> bool:
    """Remove ``slug`` and all its roots from the registry. Returns ``False``
    (writing nothing) when the slug isn't registered.

    **Registry only** — the project's memory tree under ``<root>/projects/<slug>/``
    is left untouched, so nothing captured is destroyed and re-registering the same
    directory restores the project intact. Deleting the tree is a separate,
    explicitly destructive operation (``StoreHandle.delete_project_tree``).

    Reads strictly, for the reason given in :func:`remove_root`."""
    registry = _read_registry(root)
    if slug not in registry:
        return False
    del registry[slug]
    _write_registry(root, registry)
    return True


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


def _is_within(path: Path, ancestor: Path) -> bool:
    """True when ``path`` *is* ``ancestor`` or lives beneath it (both already
    resolved by the caller). ``Path.is_relative_to`` is component-wise and returns
    ``True`` for an equal path, so it also covers ``path == ancestor`` — this is
    *not* string-prefix matching, so ``~/Projects2`` is not "within"
    ``~/Projects``."""
    return path.is_relative_to(ancestor)


def _resolved_config_entries(paths: list[str]) -> list[tuple[str, Path]]:
    """Each usable configured path as ``(verbatim, resolved)``. **Drops** any entry
    that isn't absolute after expansion (a relative entry would resolve against the
    hook's launch cwd — non-deterministic scope, review F5) and any entry that
    can't be turned into a path at all. Never raises on a bad entry — a malformed
    ``config.toml`` value must not crash a hook.

    The verbatim half exists so a display surface can name *which* configured entry
    matched, in the form the user actually wrote it (``~/Projects``), rather than
    the expanded path they'd have to recognize."""
    if isinstance(paths, str):
        paths = [paths]  # defensive: never iterate a bare string per character (R2-1)
    out: list[tuple[str, Path]] = []
    for raw in paths:
        try:
            expanded = Path(raw).expanduser()
            if not expanded.is_absolute():
                continue  # skip relative entries rather than resolve them vs cwd
            out.append((raw, expanded.resolve()))
        except (RuntimeError, TypeError, OSError):
            continue  # ~ with no home, non-string element, or a pathological path
    return out


def _resolved_config_dirs(paths: list[str]) -> list[Path]:
    """Just the resolved paths from :func:`_resolved_config_entries`."""
    return [resolved for _, resolved in _resolved_config_entries(paths)]


def _config_hit(path: Path, paths: list[str]) -> str | None:
    """The configured entry — verbatim, as written — covering ``path``, or ``None``.

    ``path`` is used **as given** (expanded and resolved, but never git-collapsed):
    the callers below hand it a registered root, which ``register_project`` already
    stored as a git common root. Re-deriving it would mean a ``git rev-parse``
    subprocess per call, and these run once per registered root on every render of
    the Projects directory.

    Fails closed and silent on a path that can't be resolved (a symlink loop, an
    unreadable parent): "not covered" rather than an exception escaping into a
    hook or a page render."""
    if not paths:
        return None
    try:
        candidate = path.expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    for raw, configured in _resolved_config_entries(paths):
        if _is_within(candidate, configured):
            return raw
    return None


def denylist_hit(path: Path, denylist: list[str]) -> str | None:
    """The ``[enable] denylist`` entry covering ``path``, verbatim as configured, or
    ``None``. See :func:`is_denylisted` for what the denylist actually gates."""
    return _config_hit(path, denylist)


def auto_enable_hit(path: Path, auto_enable_roots: list[str]) -> str | None:
    """The ``[enable] auto_enable_roots`` entry covering ``path``, verbatim as
    configured, or ``None``. Coverage alone does not mean a repo *is* registered —
    see :func:`auto_enable_root_for` for the full qualification."""
    return _config_hit(path, auto_enable_roots)


def is_denylisted(cwd: Path, denylist: list[str]) -> bool:
    """Whether ``cwd``'s repo (git-collapsed, else its resolved path) sits under
    any ``denylist`` entry.

    The denylist gates Neurobase's **automatic** actions for a repo — new capture
    (both scribes) and automatic injection (session-start recall + the MCP recall
    prompt) — even when the repo is already registered, so it wins over an explicit
    ``neurobase enable``. It is deliberately **prospective and narrow** (review
    R2-2): it does *not* gate explicit MCP tools (``memory_search`` / ``read`` /
    ``remember``) or CLI commands (``status`` / ``curate`` / ``seed``), and it does
    *not* purge already-captured raws, curated facts, or nodes. Fully removing a
    repo's memory means deregistering it and deleting its tree."""
    if not denylist:
        return False
    candidate = (git_common_root(cwd) or cwd.resolve()).resolve()
    return denylist_hit(candidate, denylist) is not None


def auto_enable_root_for(
    cwd: Path, auto_enable_roots: list[str], denylist: list[str]
) -> Path | None:
    """The repo root to auto-register for ``cwd`` under folder-scoped auto-enable,
    or ``None`` when it doesn't qualify.

    Auto-enable is **git-repo-scoped**: ``cwd`` must be inside a git repo, and
    that repo's *common* root becomes its own project — so the umbrella folder is
    never captured as one giant project, and worktrees collapse to one project
    exactly like in :func:`resolve_project`. A repo whose root sits under any
    ``denylist`` path never qualifies (the denylist wins over
    ``auto_enable_roots``). Configured paths are ``~``-expanded and resolved, and
    relative/unusable entries are skipped (see :func:`_resolved_config_dirs`);
    a non-existent configured path simply matches nothing rather than raising."""
    if not auto_enable_roots:
        return None  # feature off — never walk git for a repo we won't register
    repo_root = git_common_root(cwd)
    if repo_root is None:
        return None  # not a git repo — auto-enable only registers real repos
    repo_root = repo_root.resolve()
    if denylist_hit(repo_root, denylist) is not None:
        return None  # the denylist wins over auto_enable_roots
    return repo_root if auto_enable_hit(repo_root, auto_enable_roots) is not None else None
