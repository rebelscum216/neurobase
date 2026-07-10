"""Seed importer (spec §12.3, decisions extending §10's "Seeder mapping"):
``neurobase seed --from-dir <path>`` and ``--from-claude-memory``.

Recursively imports markdown-ish files as curated facts. Both sources share
one rule: slug = frontmatter ``name`` if the file has one and it's a valid
slug, else the slugified filename; body verbatim (``[[wikilinks]]`` kept);
files over 20KB are skipped; ``MEMORY.md``-named index files are skipped.

Fail-soft rules (workstream B):

- A missing/non-directory top-level target raises ``BadSeedSourceError`` —
  the CLI turns that into a hard, non-zero-exit error with nothing written.
- Within a valid tree, an individual unreadable/undecodable/oversized file is
  skipped and counted, never fatal to the rest of the run.
- Idempotent on rerun: dedupe by ``(slug, sha256(raw file bytes))`` — an
  unchanged source file is a no-op; a changed one re-imports as an update to
  the same slug, reusing ``core/store.upsert_curated``'s provenance-merge
  behavior. ``agent_last`` is stamped ``"seed"``, never the store's default
  ``"curator"`` — a seed-imported fact was never touched by the curator.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from neurobase.core import store
from neurobase.core.redact import redact

MAX_SOURCE_BYTES = 20 * 1024
_MARKDOWN_SUFFIXES = (".md", ".markdown")
_INDEX_FILENAMES = {"MEMORY.md"}
_SLUGIFY_RE = re.compile(r"[^a-z0-9]+")


class BadSeedSourceError(ValueError):
    """A ``--from-dir``/``--from-claude-memory`` top-level target doesn't
    exist or isn't a readable directory — nothing to import, hard CLI error
    (spec §12.3)."""


@dataclass
class SeedResult:
    """One import pass's tally. ``imported`` covers both new facts and
    updates to an existing slug; ``unchanged`` is a same-digest no-op rerun;
    ``skipped`` names files that failed to import individually — always
    counted, never fatal to the rest of the run."""

    imported: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def merge(self, other: SeedResult) -> SeedResult:
        return SeedResult(
            imported=[*self.imported, *other.imported],
            unchanged=[*self.unchanged, *other.unchanged],
            skipped=[*self.skipped, *other.skipped],
        )


def claude_memory_dir(project_root: Path) -> Path:
    """Claude Code's per-project auto-memory dir (spec §12.3, live-verified):
    ``~/.claude/projects/<cwd-with-every-'/'-replaced-by-'-'>/memory/``."""
    encoded = str(project_root).replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded / "memory"


def _slugify(name: str) -> str:
    return _SLUGIFY_RE.sub("-", name.lower()).strip("-") or "seed-fact"


def _looks_secret(candidate: str) -> bool:
    """True if any built-in redaction pattern matches ``candidate`` verbatim.

    The importer redacts the *body* before it ever reaches ``curated/``, but
    the slug is derived from the frontmatter ``name`` hint or the filename —
    neither of which was ever run through ``redact()``. A secret-shaped
    filename (e.g. an AWS-key-looking ``.md`` name) or frontmatter ``name``
    would otherwise land on disk verbatim as both the curated filename and
    the persisted ``name:`` field. Checked against the *raw* hint (not the
    lower-cased slugified form) since several built-in patterns are
    case-sensitive and would no longer match once lower-cased.
    """
    return redact(candidate) != candidate


def _slug_for(name_hint: object, filename_stem: str, raw_bytes: bytes) -> str:
    if (
        isinstance(name_hint, str)
        and store.SLUG_RE.match(name_hint)
        and not _looks_secret(name_hint)
    ):
        return name_hint
    if not _looks_secret(filename_stem):
        return _slugify(filename_stem)
    # Both the frontmatter name hint (if any) and the filename look
    # secret-shaped — slugifying would just lower-case a still-sensitive
    # string onto disk. Fall back to a slug that reveals nothing about the
    # source name; it's still stable across reruns of the same file.
    return f"seed-{hashlib.sha256(raw_bytes).hexdigest()[:12]}"


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Tolerant frontmatter split for arbitrary source markdown. Unlike
    ``store.read_doc`` (which demands the store's own strict document
    shape), an arbitrary note may have no frontmatter at all, or a shape
    that doesn't match it exactly — either case just falls back to treating
    the whole file as body."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, body


def _iter_source_files(top: Path) -> Iterable[Path]:
    """Recursively yield candidate markdown files under ``top``.

    Uses ``os.walk`` rather than ``Path.rglob`` for two fail-soft reasons:
    ``os.walk``'s default ``onerror=None`` silently skips a permission-denied
    subdirectory instead of raising and aborting the entire walk, and its
    default ``followlinks=False`` never descends into a symlinked
    subdirectory. A symlinked *file* at the leaf level is still yielded here
    (filenames aren't filtered by link status) — ``_import_tree`` skips those
    explicitly so a symlink pointing outside ``top`` is never read.
    """
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames.sort()
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.suffix.lower() in _MARKDOWN_SUFFIXES and path.name not in _INDEX_FILENAMES:
                yield path


def _existing_seed_state(root: Path, project: str, slug: str) -> tuple[str | None, str | None]:
    """``(source_digest, agent_last)`` for ``slug``'s existing curated file,
    or ``(None, None)`` if there is no such file or it fails to parse."""
    path = store.memory_dir(project, root) / "curated" / f"{slug}.md"
    if not path.exists():
        return None, None
    try:
        doc = store.read_doc(path)
    except ValueError:
        return None, None
    digest = doc.get("source_digest")
    agent_last = doc.get("agent_last")
    return (
        digest if isinstance(digest, str) else None,
        agent_last if isinstance(agent_last, str) else None,
    )


def _import_tree(
    root: Path,
    project: str,
    top: Path,
    source_label: str,
    *,
    extra_patterns: Iterable[str],
) -> SeedResult:
    result = SeedResult()
    for path in _iter_source_files(top):
        rel = path.relative_to(top).as_posix()
        if path.is_symlink():
            # Never follow a symlink out of the named --from-dir/auto-memory
            # tree — a .md-suffixed symlink pointing at, say, ~/.ssh/id_rsa
            # would otherwise be read and imported like any other file.
            result.skipped.append((str(path), "symlink (refusing to follow)"))
            continue
        try:
            size = path.stat().st_size
            if size > MAX_SOURCE_BYTES:
                result.skipped.append((str(path), f"oversized ({size} bytes > {MAX_SOURCE_BYTES})"))
                continue
            raw_bytes = path.read_bytes()
            text = raw_bytes.decode("utf-8")
        except OSError as exc:
            result.skipped.append((str(path), f"unreadable: {exc}"))
            continue
        except UnicodeDecodeError as exc:
            result.skipped.append((str(path), f"undecodable: {exc}"))
            continue

        frontmatter, body = _split_frontmatter(text)
        if not body.strip():
            result.skipped.append((str(path), "empty"))
            continue

        slug = _slug_for(frontmatter.get("name"), path.stem, raw_bytes)
        digest = hashlib.sha256(raw_bytes).hexdigest()
        provenance_entry = f"seed:{source_label}/{rel}"

        existing_digest, existing_agent_last = _existing_seed_state(root, project, slug)
        if existing_digest is not None:
            if existing_digest == digest:
                result.unchanged.append(slug)
                continue
        elif existing_agent_last not in (None, "seed"):
            # The slug exists but has no source_digest and was last touched
            # by something other than the seed importer — most likely a
            # normal curator/MCP upsert_curated call, which doesn't pass
            # extra_frontmatter and so drops this bookkeeping entirely.
            # Treating that as "changed, reimport" would silently overwrite
            # curated content the curator has since refined with the stale
            # raw seed text. Refuse instead.
            result.skipped.append(
                (
                    str(path),
                    f"slug {slug!r} already exists and was last touched by "
                    f"{existing_agent_last!r} (not the seed importer); refusing to "
                    "overwrite — remove the curated fact first to force a reimport",
                )
            )
            continue

        redacted_body = redact(body, extra_patterns=extra_patterns)
        store.upsert_curated(
            root,
            project,
            slug,
            redacted_body,
            provenance=[provenance_entry],
            agent_last="seed",
            extra_frontmatter={"source_digest": digest, "source_path": provenance_entry},
        )
        result.imported.append(slug)
    return result


def import_from_dir(
    root: Path,
    project: str,
    source_dir: Path,
    *,
    extra_patterns: Iterable[str] = (),
) -> SeedResult:
    """``--from-dir <path>`` (§12.3): recurse into ``source_dir``, importing
    every ``*.md``/``*.markdown`` file (skipping ``MEMORY.md``-named index
    files exactly as ``--from-claude-memory`` does) as a curated fact under
    ``project``. A missing/non-directory ``source_dir`` raises
    ``BadSeedSourceError`` — there is nothing to import, so the caller should
    treat this as a hard, non-zero-exit CLI error with nothing written."""
    resolved = source_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise BadSeedSourceError(f"{resolved} is not a directory")
    # An *unreadable* top-level target is a hard error too (§12.3): is_dir() is
    # true for a chmod-000 directory, but the os.walk(onerror=None) inside
    # _iter_source_files would then silently yield nothing, so the import would
    # look like a successful empty run. Probe the named directory eagerly so it
    # raises here — unreadable *nested* dirs/files inside a valid tree stay
    # fail-soft (os.walk keeps skipping those).
    try:
        with os.scandir(resolved) as entries:
            next(entries, None)
    except OSError as exc:
        raise BadSeedSourceError(f"{resolved} is not readable: {exc}") from exc
    return _import_tree(root, project, resolved, resolved.name, extra_patterns=extra_patterns)


def import_from_claude_memory(
    root: Path,
    project: str,
    project_root: Path,
    *,
    extra_patterns: Iterable[str] = (),
) -> SeedResult:
    """``--from-claude-memory`` for one already-resolved project (§12.3):
    import Claude Code's auto-memory dir for ``project_root`` (a derived
    path, never one the user named directly — see ``claude_memory_dir``). A
    missing auto-memory directory is *not* an error: most projects simply
    don't have one, so this returns an empty ``SeedResult`` rather than
    raising."""
    mem_dir = claude_memory_dir(project_root)
    if not mem_dir.is_dir():
        return SeedResult()
    return _import_tree(root, project, mem_dir, "claude-memory", extra_patterns=extra_patterns)
