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


class StalePreviewError(RuntimeError):
    """Base for the record-only path's mutation-boundary refusals (ADR-0020 D40).

    A record-only acceptance writes no artifact, so it can only be honest if
    *both* sides of the preview still hold at the moment it records: the target
    bytes it claims are installed, and the proposal whose draft produced them.
    Either drifting between preview and write means the recorded acceptance would
    describe something that was never true; callers surface this and record
    nothing."""


class StaleArtifactError(StalePreviewError):
    """The target's bytes no longer match the previewed render (review
    P1-DATA-INTEGRITY-001). ``already_up_to_date`` held only when the preview was
    prepared; the file can change during a long interactive confirm prompt, and a
    no-write acceptance must never stamp a hash that disagrees with disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(
            f"{path} changed after the acceptance was previewed — nothing was recorded. "
            "Re-run accept to preview and confirm the current state."
        )


class StaleProposalError(StalePreviewError):
    """The *proposal* changed after the preview (review P1-DATA-INTEGRITY-004).

    The target re-read alone is not enough: a concurrent ``recommend edit`` during
    the confirm prompt leaves the installed bytes untouched, so the target check
    passes against the stale render — while ``accept_proposal`` would then reload
    and mark the *newly edited* draft accepted. The accepted proposal would
    describe behavior that was never installed."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(
            f"proposal {slug!r} changed after the acceptance was previewed — nothing was "
            "recorded. Re-run accept to preview and confirm the current draft."
        )


@dataclass(frozen=True)
class InstallPreview:
    """The result of rendering (but not writing) one proposal's artifact."""

    doc: store.Document
    artifact: emitters.Artifact
    already_up_to_date: bool
    #: ADR-0020 D40. True when the render already matches disk *and* the
    #: proposal does not yet say so — the artifact is installed but unrecorded,
    #: so acceptance is recordable with no write. False when the proposal is
    #: already ``accepted`` (a plain idempotent no-op, §12.7) or when the target
    #: is foreign (never silently claim a file Neurobase does not own).
    records_acceptance: bool = False


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
    # ADR-0020 D40: "the render already matches disk" means two different things
    # depending on the record. If the proposal is already `accepted`, it is
    # §12.7's idempotent no-op — nothing to write, nothing to record. If it is
    # not, the artifact is installed while the store says it isn't: the state a
    # drift-repair `revert` leaves behind, and the one an accept could never
    # escape while every no-op returned early (review F2). That case is
    # recordable without a write. A foreign target is excluded — matching bytes
    # in a file Neurobase does not own must not be claimed silently.
    records_acceptance = already_up_to_date and status != "accepted" and not artifact.foreign
    return InstallPreview(
        doc=doc,
        artifact=artifact,
        already_up_to_date=already_up_to_date,
        records_acceptance=records_acceptance,
    )


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


def record_acceptance(root: Path, preview: InstallPreview) -> InstallResult:
    """Record acceptance of an artifact that is already installed byte-for-byte
    (``preview.records_acceptance``), without writing or backing anything up —
    there is nothing to write, and a backup of bytes identical to the render
    would preserve nothing (ADR-0020 D40).

    Callers must still have obtained consent: this changes the store's own
    record of what is installed, and the recorded ``installed_hash`` becomes the
    baseline a later survival check and a later ``revert`` both compare against.
    Raises ``ValueError`` if the preview is not in the recordable state, so the
    no-op and record paths can never be confused at a call site, and
    ``StalePreviewError`` if either side of the preview drifted (see below)."""
    if not preview.records_acceptance:
        raise ValueError("preview is not in a recordable state; use commit_install")
    artifact = preview.artifact
    slug = str(preview.doc.get("name") or "")
    # Both sides of the preview are re-verified HERE, at the mutation boundary,
    # because a record-only acceptance writes nothing and so can only be honest if
    # what it *claims* is still true. `records_acceptance` was decided when the
    # preview was prepared, and an interactive confirm prompt can sit open
    # arbitrarily long in between.
    #
    # (a) The target bytes (P1-DATA-INTEGRITY-001), read the same
    #     newline-preserving way the render was diffed against — otherwise a
    #     no-write acceptance could stamp a hash that disagrees with disk.
    if emitters.read_target_text(artifact.path) != artifact.after:
        raise StaleArtifactError(artifact.path)
    # (b) The proposal itself (P1-DATA-INTEGRITY-004). A concurrent
    #     `recommend edit` changes the draft while leaving the target untouched,
    #     so check (a) still passes against the stale render — and
    #     `accept_proposal` below reloads from disk, which would mark the *newly
    #     edited* draft accepted while the installed artifact came from the old
    #     one. Compare the render-relevant state (the managed draft region) and
    #     the status, and refuse if either moved.
    current = proposals.load_proposal(root, slug)
    if current is None or str(current.get("status") or "") != str(preview.doc.get("status") or ""):
        raise StaleProposalError(slug)
    if proposals.extract_draft(current.body) != proposals.extract_draft(preview.doc.body):
        raise StaleProposalError(slug)
    # The same hash commit_install records, over the same bytes — the artifact
    # on disk equals `artifact.after` (just re-verified), which is what makes this
    # path legitimate in the first place.
    installed_hash = hashlib.sha256(artifact.after.encode("utf-8")).hexdigest()
    proposals.accept_proposal(
        root,
        slug,
        target=artifact.target,
        installed_path=artifact.path,
        installed_hash=installed_hash,
    )
    return InstallResult(path=artifact.path, backup_dir=None, installed_hash=installed_hash)
