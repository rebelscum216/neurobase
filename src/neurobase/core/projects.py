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


class RegistryNotContainedError(ValueError):
    """``registry.toml`` resolves outside the store root, so it is not this
    store's selector at all (Codex P2-SAFETY-SECURITY-010).

    Raised only on the read-for-**rewrite** path, which must fail closed: reading
    an external registry to write it back would both import its entries into this
    store's mapping and follow the symlink to rewrite a file outside the store.
    The read-only paths never raise this — they fail soft to empty, matching the
    §10 posture for a missing or corrupt registry."""


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
    """``<root>/registry.toml``.

    **Private on purpose** (Codex P2-SAFETY-SECURITY-013). Round 4's fix made this
    public so the guard at ``StoreHandle.load_registry`` could name the path it was
    proving — but ``scripts/check_store_chokepoint.py`` matches accessors by *name*
    and cannot see the ``"registry.toml"`` literal hidden inside a helper, so the
    public spelling handed every production module a CI-approved way to reach the
    registry from a raw root: the precise reintroduction ADR-0015 exists to make
    mechanically hard. Containment now lives *below* this helper (see
    :func:`registry_is_contained`), so no caller outside this module ever needs to
    name the path — and the guard lists both spellings anyway, so re-publishing it
    fails CI rather than silently reopening the hole."""
    return root / "registry.toml"


def registry_is_contained(root: Path) -> bool:
    """True when ``<root>/registry.toml`` resolves to somewhere at or beneath
    ``root`` — i.e. the registry this store would read is really *this store's*.

    The registry is the **selector**, not another document: it decides which
    project namespaces a sweep walks at all, so containing the five document
    enumerations (raw, curated, tombstone, journal, proposals) while accepting an
    external selector leaves the identity boundary incomplete under the same
    symlink threat model (Codex P2-SAFETY-SECURITY-010).

    Both sides are resolved, so a symlinked ``registry.toml`` cannot smuggle in a
    selector from outside the store, while a store whose own **root** is reached
    through a symlink (macOS ``/var`` → ``/private/var``, a symlinked parent, a
    relative path) still matches — the root is resolved too. A *missing* registry
    resolves to its own would-be location and is therefore contained: absent is
    not hostile, and it already reads as empty one layer down.

    Fails **closed** on a ``resolve()`` that raises — a self-referential symlink
    loop (``RuntimeError``) or an unreadable parent (``OSError``) — matching
    :meth:`StoreHandle.contains`, whose definition this deliberately mirrors so
    the two guards can never disagree about what "contained" means.

    Public because the **doctor** must be able to tell an uncontained registry
    from an absent one on its no-handle (corrupt ``store.toml``) path; it is in
    the chokepoint's forbidden set and allow-listed there for exactly that one
    call site."""
    try:
        resolved_root = root.resolve()
        target = _registry_path(root).resolve()
    except (OSError, RuntimeError):
        return False
    return target == resolved_root or target.is_relative_to(resolved_root)


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
    is unreadable (``OSError``), unparseable (``tomllib.TOMLDecodeError``), not
    shaped like a registry (:class:`RegistryShapeError`), or **not contained**
    (:class:`RegistryNotContainedError`).

    Containment is checked *before* the read, not after: this path exists only to
    write the result back, so merely reading an external registry would import its
    entries into the mapping handed to the writer (Codex P2-SAFETY-SECURITY-010,
    round 5 — the rewrite probe carried an external ``injected`` entry straight
    through). Fail closed here rather than fail soft, because an empty mapping
    would be *worse* than an exception: it would rewrite the file from ``{}`` and
    drop every real project's roots."""
    if not registry_is_contained(root):
        raise RegistryNotContainedError(
            f"{_registry_path(root)} resolves outside the store ({root}) — refusing "
            "to read a registry this store does not own for a rewrite"
        )
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

    An **uncontained** registry — one whose ``registry.toml`` resolves outside
    ``root`` — reads as empty too (Codex P2-SAFETY-SECURITY-010). The guard sits
    here, at the low-level accessor, rather than only on the handle: round 4 put it
    on ``StoreHandle.load_registry`` alone, and round 5's probe walked straight past
    it through ``resolve_project`` → this function, still resolving the
    attacker-selected slug on the capture, recall, ``status``, ``curate``, doctor and
    MCP paths. Every registry read in the process reaches one of these two
    functions, so this is the boundary; a guard on one method above it is a guard
    on one caller.

    Empty rather than raising is deliberate and is the ruling Codex gave in round 5:
    it preserves the §10/§13 fail-soft posture and stops a single hostile selector
    recreating P2-REGRESSION-002's whole-surface failure. Empty must not be mistaken
    for *healthy*, though — telling "uncontained" from "absent" is
    :func:`registry_is_contained`'s job, and the doctor is where that distinction is
    reported.

    Read-for-**rewrite** is the deliberate exception and does not come through
    here — see :func:`register_project`, which must not overwrite a registry it
    could not fully parse, nor one it does not own."""
    if not registry_is_contained(root):
        return {}
    try:
        data = _load_toml(root)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return _shape(data, strict=False)


def _write_registry(root: Path, registry: dict[str, list[str]]) -> None:
    """The only registry writer. Refuses an uncontained target.

    ``register_project`` already fails closed one step earlier in
    :func:`_read_registry`, so in practice this never fires for it — it is here
    because *writing* through a symlink is the more damaging half of the finding
    (it mutates a file outside the store), and a future second writer must not have
    to remember the rule. Guarding the read but not the write would be exactly the
    "one caller remembered" mistake round 5 found."""
    if not registry_is_contained(root):
        raise RegistryNotContainedError(
            f"{_registry_path(root)} resolves outside the store ({root}) — refusing "
            "to write a registry this store does not own"
        )
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
    registry raises here instead — and so does an **out-of-store** one
    (:class:`RegistryNotContainedError`), which this path must refuse before
    reading rather than fail soft, since it would otherwise import the external
    registry's entries and rewrite a file outside the store. ``core/enable.py``
    catches all of these and fails closed, which is the documented auto-enable
    posture."""
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


def _resolved_config_dirs(paths: list[str]) -> list[Path]:
    """Expand ``~`` and resolve each configured path, **dropping** any entry that
    isn't absolute after expansion (a relative entry would resolve against the
    hook's launch cwd — non-deterministic scope, review F5) and any entry that
    can't be turned into a path at all. Never raises on a bad entry — a malformed
    ``config.toml`` value must not crash a hook."""
    if isinstance(paths, str):
        paths = [paths]  # defensive: never iterate a bare string per character (R2-1)
    out: list[Path] = []
    for raw in paths:
        try:
            expanded = Path(raw).expanduser()
            if not expanded.is_absolute():
                continue  # skip relative entries rather than resolve them vs cwd
            out.append(expanded.resolve())
        except (RuntimeError, TypeError, OSError):
            continue  # ~ with no home, non-string element, or a pathological path
    return out


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
    return any(_is_within(candidate, deny) for deny in _resolved_config_dirs(denylist))


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
    for deny in _resolved_config_dirs(denylist):
        if _is_within(repo_root, deny):
            return None
    for allowed in _resolved_config_dirs(auto_enable_roots):
        if _is_within(repo_root, allowed):
            return repo_root
    return None
