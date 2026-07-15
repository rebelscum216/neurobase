"""The markdown store (spec §1): tree layout, document format, atomic writes.

``<root>/projects/<project>/memory/{raw,curated,nodes,.tombstones}`` +
``index.md``. Every file is YAML frontmatter + a markdown body; every write
is atomic (temp file + rename). Nodes and ``index.md`` are pure functions of
``curated/`` — regenerated wholesale, never appended to.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import tomli_w
import yaml

from neurobase.core.config import load_config

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
RAW_SUBDIRS = ("raw", "curated", "nodes", ".tombstones")
STORE_SCHEMA_VERSION = 1

_DOC_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n\n(?P<body>.*)\Z", re.DOTALL)


class InvalidSlugError(ValueError):
    """A project/fact/node slug doesn't match ``^[a-z0-9-]+$``."""


class RawConsumedError(RuntimeError):
    """A scribe tried to overwrite a raw capture the curator already consumed."""


class UnsupportedSchemaError(RuntimeError):
    """The store's on-disk schema is newer than this binary supports (D11)."""


@dataclass
class Document:
    """A parsed store document: frontmatter fields, ``body``, ``file_path``."""

    frontmatter: dict[str, Any]
    body: str
    file_path: Path

    def get(self, key: str, default: Any = None) -> Any:
        return self.frontmatter.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.frontmatter[key]


# --- root + tree -----------------------------------------------------------


def resolve_root(explicit: str | Path | None = None) -> Path:
    """``<root>`` precedence (spec §1): explicit arg > ``NEUROBASE_ROOT`` env
    > config value > default ``~/neurobase``."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env_root = os.environ.get("NEUROBASE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    cfg = load_config()
    return Path(cfg.store.root).expanduser().resolve()


def _require_slug(value: str, what: str) -> str:
    if not SLUG_RE.match(value):
        raise InvalidSlugError(f"invalid {what}: {value!r} (must match ^[a-z0-9-]+$)")
    return value


def memory_dir(project: str, root: Path) -> Path:
    """The path boundary for every store entry point — validates ``project``
    here so an invalid/empty slug can never silently collapse into a bad path
    (e.g. an empty project string joining away to ``<root>/projects/memory``)."""
    _require_slug(project, "project slug")
    return root / "projects" / project / "memory"


def store_toml_path(root: Path) -> Path:
    return root / "store.toml"


def ensure_store_metadata(root: Path) -> Path:
    """Write ``<root>/store.toml`` (``schema = 1``, ``created_at``) on first
    use; on subsequent calls, refuse to operate if the on-disk schema is
    newer than this binary supports (spec §10, decision D11). ``neurobase
    migrate`` owns future schema bumps."""
    path = store_toml_path(root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schema": STORE_SCHEMA_VERSION, "created_at": _now_iso()}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(tomli_w.dumps(doc).encode("utf-8"))
        tmp.replace(path)
        return path
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if not isinstance(schema, int) or schema > STORE_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"{path}: schema {schema!r} is newer than this binary supports "
            f"(max {STORE_SCHEMA_VERSION}) — upgrade neurobase-cli."
        )
    return path


def ensure_tree(project: str, root: Path) -> Path:
    """Create ``raw/ curated/ nodes/ .tombstones/`` under the project's memory
    dir (and the root's ``store.toml`` if this is a fresh store). Idempotent."""
    ensure_store_metadata(root)
    mem = memory_dir(project, root)
    for sub in RAW_SUBDIRS:
        (mem / sub).mkdir(parents=True, exist_ok=True)
    return mem


# --- document format ---------------------------------------------------


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def write_doc(path: Path, frontmatter: dict[str, Any], body: str) -> Path:
    fm_text = yaml.safe_dump(
        frontmatter, sort_keys=False, default_flow_style=False, allow_unicode=True
    )
    _atomic_write_text(path, f"---\n{fm_text}---\n\n{body}")
    return path


def read_doc(path: Path) -> Document:
    text = path.read_text(encoding="utf-8")
    match = _DOC_RE.match(text)
    if not match:
        raise ValueError(f"{path}: missing YAML frontmatter block")
    # Normalize a YAML parse failure (unterminated flow sequence, bad indent, …)
    # to ValueError so every caller's `except ValueError` skip-path (list_raw,
    # list_curated, the proposal loaders) treats an unparseable frontmatter block
    # as a malformed-but-skippable document rather than crashing — yaml.YAMLError
    # is not a ValueError subclass, so it would otherwise propagate uncaught.
    try:
        frontmatter = yaml.safe_load(match.group("frontmatter")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise ValueError(f"{path}: frontmatter did not parse to a mapping")
    return Document(frontmatter=frontmatter, body=match.group("body"), file_path=path)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- raw/ --------------------------------------------------------------


def _sid8(session_id: str | None) -> str:
    if not session_id:
        return "nosid"
    cleaned = re.sub(r"[^a-z0-9]", "", session_id.lower())
    return cleaned[:8] or "nosid"


def raw_filename(captured_at: datetime, agent: str, session_id: str | None) -> str:
    ts = captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{ts}_{agent}_{_sid8(session_id)}.md"


def raw_path(
    root: Path, project: str, captured_at: datetime, agent: str, session_id: str | None
) -> Path:
    return memory_dir(project, root) / "raw" / raw_filename(captured_at, agent, session_id)


def write_raw(
    root: Path,
    project: str,
    *,
    agent: str,
    session_id: str,
    cwd: str,
    branch: str,
    captured_at: datetime,
    body: str,
    transcript_path: str | None = None,
) -> Path:
    """Write (or session-keyed-overwrite) a raw capture (spec §1/§5).

    Rewritable by the owning scribe until the curator flips ``consumed:
    true`` — from then on, raises ``RawConsumedError`` so the caller can
    retry with a fresh ``captured_at`` (a new filename), per the mutability
    rule.

    ``transcript_path`` is optional (ADR-0014, D15): when given, the raw is
    written as ``capture_version: 2`` so the curator's distill step (spec
    §2.0) can resolve the transcript later. Omitted ⇒ a v1 raw; every reader
    must tolerate the absence of both keys.
    """
    path = raw_path(root, project, captured_at, agent, session_id)
    if path.exists() and read_doc(path).get("consumed"):
        raise RawConsumedError(f"{path} is already consumed; retry with captured_at=now")
    frontmatter = {
        "agent": agent,
        "session_id": session_id,
        "cwd": cwd,
        "branch": branch,
        "captured_at": captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "consumed": False,
    }
    if transcript_path is not None:
        frontmatter["transcript_path"] = transcript_path
        frontmatter["capture_version"] = 2
    write_doc(path, frontmatter, body)
    return path


def list_raw(root: Path, project: str, unconsumed_only: bool = True) -> list[Document]:
    """Oldest-first (filename timestamp prefix sorts chronologically).
    Unparseable files are skipped, never fatal."""
    raw_dir = memory_dir(project, root) / "raw"
    if not raw_dir.exists():
        return []
    docs = []
    for path in sorted(raw_dir.glob("*.md")):
        try:
            doc = read_doc(path)
        except ValueError:
            continue
        if unconsumed_only and doc.get("consumed"):
            continue
        docs.append(doc)
    return docs


def mark_consumed(path: Path) -> Path:
    """Flip ``consumed: true`` — the only permitted mutation of an existing
    raw file; every other frontmatter field and the body are preserved."""
    doc = read_doc(path)
    frontmatter = dict(doc.frontmatter)
    frontmatter["consumed"] = True
    return write_doc(path, frontmatter, doc.body)


# --- curated/ ------------------------------------------------------------


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def upsert_curated(
    root: Path,
    project: str,
    slug: str,
    body: str,
    *,
    provenance: Iterable[str] = (),
    supersedes: list[str] | None = None,
    agent_last: str = "curator",
    extra_frontmatter: dict[str, Any] | None = None,
) -> Path:
    """Merge provenance (prior + new, order-preserving dedupe); ``supersedes``
    is the new value if given, else kept from the prior file. Body is
    overwritten wholesale — the curator owns curated content.

    ``agent_last`` defaults to ``"curator"`` (unchanged behavior for every
    existing caller); pass an override (e.g. ``"seed"``) for a caller other
    than the curator so this field never silently claims the curator touched
    a fact it never saw (spec §12.3). ``extra_frontmatter`` additively merges
    caller-owned keys (e.g. a seed importer's ``source_digest``) into the
    written frontmatter — the core keys below always win on collision, so a
    caller can never use it to overwrite ``name``/``status``/``provenance``/
    ``supersedes``/``agent_last``/``updated_at``."""
    _require_slug(slug, "fact slug")
    path = memory_dir(project, root) / "curated" / f"{slug}.md"
    prior_provenance: list[str] = []
    prior_supersedes: list[str] = []
    if path.exists():
        existing = read_doc(path)
        prior_provenance = list(existing.get("provenance") or [])
        prior_supersedes = list(existing.get("supersedes") or [])
    frontmatter: dict[str, Any] = {
        **(extra_frontmatter or {}),
        "name": slug,
        "status": "active",
        "supersedes": supersedes if supersedes is not None else prior_supersedes,
        "provenance": _dedupe_preserve_order([*prior_provenance, *provenance]),
        "agent_last": agent_last,
        "updated_at": _now_iso(),
    }
    return write_doc(path, frontmatter, body)


def list_curated(root: Path, project: str, active_only: bool = True) -> list[Document]:
    """Curated facts, sorted by slug (stable order for plan payloads + node
    synthesis). Unparseable files are skipped, never fatal. ``active_only``
    keeps only ``status: active`` (all files in ``curated/`` should be active —
    tombstoned facts live in ``.tombstones/`` — but filter defensively)."""
    curated_dir = memory_dir(project, root) / "curated"
    if not curated_dir.exists():
        return []
    docs = []
    for path in sorted(curated_dir.glob("*.md")):
        try:
            doc = read_doc(path)
        except ValueError:
            continue
        if active_only and doc.get("status") != "active":
            continue
        docs.append(doc)
    return docs


def soft_delete_curated(root: Path, project: str, slug: str) -> Path:
    """Tombstone a curated fact: move it to ``.tombstones/``, recoverable
    until ``prune_tombstones`` hard-deletes it past the grace period."""
    _require_slug(slug, "fact slug")
    mem = memory_dir(project, root)
    src = mem / "curated" / f"{slug}.md"
    doc = read_doc(src)
    frontmatter = dict(doc.frontmatter)
    frontmatter["status"] = "tombstoned"
    frontmatter["tombstoned_at"] = _now_iso()
    dest = mem / ".tombstones" / f"{slug}.md"
    write_doc(dest, frontmatter, doc.body)
    src.unlink()
    return dest


def prune_tombstones(root: Path, project: str, older_than_days: int = 14) -> list[str]:
    """Hard-delete tombstones past the grace period. Returns pruned slugs."""
    tomb_dir = memory_dir(project, root) / ".tombstones"
    if not tomb_dir.exists():
        return []
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    pruned = []
    for path in sorted(tomb_dir.glob("*.md")):
        try:
            doc = read_doc(path)
        except ValueError:
            continue
        tombstoned_at = doc.get("tombstoned_at")
        if not tombstoned_at:
            continue
        try:
            when = datetime.fromisoformat(str(tombstoned_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < cutoff:
            path.unlink()
            pruned.append(path.stem)
    return pruned


# --- nodes/ + index.md ------------------------------------------------------


def write_node(root: Path, project: str, name: str, body: str) -> Path:
    """Nodes are regenerated wholesale, never appended to (no-drift guarantee)."""
    _require_slug(name, "node name")
    path = memory_dir(project, root) / "nodes" / f"{name}.md"
    frontmatter = {"name": name, "generated_at": _now_iso()}
    return write_doc(path, frontmatter, body)


def _first_body_line(body: str, max_chars: int = 120) -> str:
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line:
            return line.lstrip("#").strip()[:max_chars]
    return ""


def rebuild_index(root: Path, project: str) -> Path:
    """Regenerate ``index.md`` from ``nodes/`` + active ``curated/`` — a pure
    function of on-disk state, run after every curate."""
    mem = memory_dir(project, root)
    lines = [f"# Memory index — {project}", ""]
    nodes_dir = mem / "nodes"
    for node_path in sorted(nodes_dir.glob("*.md")) if nodes_dir.exists() else []:
        doc = read_doc(node_path)
        name = doc.get("name", node_path.stem)
        lines.append(f"- [{name}](nodes/{node_path.name}) — {_first_body_line(doc.body)}")
    lines.append("")
    active_count = 0
    curated_dir = mem / "curated"
    for path in curated_dir.glob("*.md") if curated_dir.exists() else []:
        try:
            doc = read_doc(path)
        except ValueError:
            continue
        if doc.get("status") == "active":
            active_count += 1
    lines.append(f"_{active_count} active curated facts._")
    index_path = mem / "index.md"
    _atomic_write_text(index_path, "\n".join(lines) + "\n")
    return index_path
