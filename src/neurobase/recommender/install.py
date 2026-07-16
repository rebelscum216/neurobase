"""Shared proposal-install service (spec §12.7), lifted out of
``cli/__init__.py:recommend_accept`` so the CLI and the web UI (Phase 1 D-1)
can share the exact same diff → consent → backup → atomic-write → ledger
choreography. This module performs no prompting: ``prepare_install`` is a
side-effect-free preview safe to call from a GET handler, and
``commit_install`` assumes the caller already obtained consent (a CLI
``typer.confirm()``, or a web UI's CSRF-protected POST) and just performs the
write.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from neurobase.core import backups, store
from neurobase.recommender import emitters, proposals

# §12.7: accept on a rejected/superseded proposal is a hard error, never
# reopened. Shared with proposals.py's own decided-status set so the guard
# stays in lockstep with the write path it protects.
_DECIDED_STATUSES = frozenset({"rejected", "superseded"})


class ProposalNotFoundError(LookupError):
    """Raised when ``slug`` has no proposal, or an existing file is malformed."""


class ProposalDecidedError(RuntimeError):
    """Raised when ``slug`` is already ``rejected``/``superseded`` — a decided
    proposal is never reopened (§12.7)."""

    def __init__(self, slug: str, status: str) -> None:
        self.slug = slug
        self.status = status
        super().__init__(f"cannot accept proposal {slug!r}: status is {status}")


@dataclass(frozen=True)
class InstallPreview:
    """The result of rendering (but not writing) one proposal's artifact."""

    doc: store.Document
    artifact: emitters.Artifact
    already_up_to_date: bool


@dataclass(frozen=True)
class InstallResult:
    """What ``commit_install`` actually wrote."""

    path: Path
    backup_dir: Path | None
    installed_hash: str


def prepare_install(root: Path, slug: str, *, target: str | None = None) -> InstallPreview:
    """Load the proposal, guard its status, and render the artifact — no
    writes on any path (safe to call from a GET handler). Raises
    ``ProposalNotFoundError`` when ``slug`` doesn't resolve to a valid
    proposal, ``ProposalDecidedError`` when it is ``rejected``/``superseded``,
    or ``ValueError`` when the proposal/emitter is otherwise malformed.

    The status guard runs BEFORE ``emitters.prepare`` so a blocked proposal
    can never reach the renderer, let alone the no-op/foreign-file checks
    below it (§12.7)."""
    doc = proposals.load_proposal(root, slug)
    if doc is None:
        raise ProposalNotFoundError(slug)
    status = str(doc.get("status") or "proposed")
    if status in _DECIDED_STATUSES:
        raise ProposalDecidedError(slug, status)
    artifact = emitters.prepare(root, doc, skill_scope=target)
    already_up_to_date = artifact.before == artifact.after
    return InstallPreview(doc=doc, artifact=artifact, already_up_to_date=already_up_to_date)


def commit_install(root: Path, preview: InstallPreview) -> InstallResult:
    """Back up the existing target (if any), write the artifact atomically,
    and record the acceptance in the ledger. The caller is responsible for
    having already obtained consent — this function assumes consent was given
    and performs the write unconditionally; it does not prompt or confirm
    anything, and does not re-check ``already_up_to_date``."""
    artifact = preview.artifact
    backup_dir = backups.backup_files(root, [artifact.path])
    emitters.write_atomic(artifact)
    # §12.9 survival check (ADR-0007 D2): record the artifact's content hash at
    # accept time so a later `status --recommender` can tell "modified since
    # acceptance" apart from "never touched" without diffing against anything
    # else on disk.
    installed_hash = hashlib.sha256(artifact.after.encode("utf-8")).hexdigest()
    slug = str(preview.doc.get("name") or "")
    proposals.accept_proposal(
        root,
        slug,
        target=artifact.target,
        installed_path=artifact.path,
        installed_hash=installed_hash,
    )
    return InstallResult(path=artifact.path, backup_dir=backup_dir, installed_hash=installed_hash)
