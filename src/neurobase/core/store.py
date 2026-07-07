"""The markdown store (spec §1): tree layout, document format, atomic writes.

``<root>/projects/<project>/memory/{raw,curated,nodes,.tombstones}`` +
``index.md``. Every file is YAML frontmatter + a markdown body; every write
is atomic (temp file + rename). Nodes and ``index.md`` are pure functions of
``curated/`` — regenerated wholesale, never appended to.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from neurobase.core.config import load_config

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
RAW_SUBDIRS = ("raw", "curated", "nodes", ".tombstones")

_DOC_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n\n(?P<body>.*)\Z", re.DOTALL)


class InvalidSlugError(ValueError):
    """A project/fact/node slug doesn't match ``^[a-z0-9-]+$``."""


class RawConsumedError(RuntimeError):
    """A scribe tried to overwrite a raw capture the curator already consumed."""


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


def memory_dir(project: str, root: Path) -> Path:
    return root / "projects" / project / "memory"


def ensure_tree(project: str, root: Path) -> Path:
    """Create ``raw/ curated/ nodes/ .tombstones/`` under the project's memory
    dir. Idempotent."""
    mem = memory_dir(project, root)
    for sub in RAW_SUBDIRS:
        (mem / sub).mkdir(parents=True, exist_ok=True)
    return mem


def _require_slug(value: str, what: str) -> str:
    if not SLUG_RE.match(value):
        raise InvalidSlugError(f"invalid {what}: {value!r} (must match ^[a-z0-9-]+$)")
    return value


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
    frontmatter = yaml.safe_load(match.group("frontmatter")) or {}
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
) -> Path:
    """Write (or session-keyed-overwrite) a raw capture (spec §1/§5).

    Rewritable by the owning scribe until the curator flips ``consumed:
    true`` — from then on, raises ``RawConsumedError`` so the caller can
    retry with a fresh ``captured_at`` (a new filename), per the mutability
    rule.
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
) -> Path:
    """Merge provenance (prior + new, order-preserving dedupe); ``supersedes``
    is the new value if given, else kept from the prior file. Body is
    overwritten wholesale — the curator owns curated content."""
    _require_slug(slug, "fact slug")
    path = memory_dir(project, root) / "curated" / f"{slug}.md"
    prior_provenance: list[str] = []
    prior_supersedes: list[str] = []
    if path.exists():
        existing = read_doc(path)
        prior_provenance = list(existing.get("provenance") or [])
        prior_supersedes = list(existing.get("supersedes") or [])
    frontmatter = {
        "name": slug,
        "status": "active",
        "supersedes": supersedes if supersedes is not None else prior_supersedes,
        "provenance": _dedupe_preserve_order([*prior_provenance, *provenance]),
        "agent_last": "curator",
        "updated_at": _now_iso(),
    }
    return write_doc(path, frontmatter, body)


def soft_delete_curated(root: Path, project: str, slug: str) -> Path:
    """Tombstone a curated fact: move it to ``.tombstones/``, recoverable
    until ``prune_tombstones`` hard-deletes it past the grace period."""
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
