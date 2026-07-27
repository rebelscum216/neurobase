"""Proposal store (spec §12.6/§12.1): persist ranked candidates as on-disk
``<root>/proposals/<slug>.md`` files, and read them back for the CLI's ranked
list — the brain/apply separation the curator uses (the ranker computes, this
module writes). Workstream F's ``recommend run`` is a thin CLI over
``write_ranked``; the emitters (G) and metrics (H) are separate later slices.

Write behavior (§12.6), each a MUST unless flagged Advisory:

- **Redact first.** The rendered body is passed through ``core/redact.py:redact``
  *before* the proposal file is ever written, so ``<root>/proposals/<slug>.md``
  never carries an unredacted draft at any point in its lifecycle (Invariants,
  §12.8).
- **Decline a near-duplicate of a still-``rejected`` proposal** before writing a
  new ``proposed`` file — a belt-and-suspenders re-check (``corpus.is_near_duplicate``
  against every rejected body) on top of the miner-prompt suppression, so a
  miner that ignores its own instruction still can't resurrect a rejected idea.
- **Upsert a same-slug ``proposed`` proposal**, keeping ``created_at`` and
  bumping ``updated_at`` — **except** never silently clobber a body the user has
  ``recommend edit``-ed (the ledger holds an ``edited`` event newer than our last
  ``proposed`` write): that refresh is skipped entirely, leaving the edited
  proposal exactly as the user left it.
- **Never reset an ``accepted``/``rejected``/``superseded`` proposal back to
  ``proposed``** — a decided proposal is left untouched.
- **Supersede only a still-``proposed`` slug** named in a candidate's explicit
  ``supersedes`` (→ ``status: superseded``); a decided slug named there is left
  alone (the "never overwrite a decided proposal" rule outranks supersede). The
  linkage is recorded in the new proposal's ``supersedes`` frontmatter either
  way. (Advisory — no named workstream-E test; covered here for correctness.)
- **Malformed proposal files are skipped** on any load, never fatal.

``recommend list``'s sort contract (§12.6): ``total`` descending, ties broken by
``created_at`` ascending, then ``name`` ascending.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.core import lock, redact, store
from neurobase.core.config import RecommendConfig, load_config
from neurobase.recommender import corpus
from neurobase.recommender.corpus import (
    EvidenceRef,
    evidence_to_frontmatter,
    ledger_path,
    proposal_path,
)
from neurobase.recommender.ranker import RankedCandidate, _parse_iso

logger = logging.getLogger(__name__)

DRAFT_START = "<!-- neurobase:draft:start -->"
DRAFT_END = "<!-- neurobase:draft:end -->"

# Statuses that a fresh `proposed` render must never overwrite (§12.6 Invariant).
_DECIDED_STATUSES = frozenset({"accepted", "rejected", "superseded"})

# Statuses `recommend edit` must refuse: a rejected or superseded proposal is a
# decided one whose draft must not change (§12.7). Authoritative — enforced inside
# the locked `save_edited_draft` so a stale route/CLI pre-check that passed before a
# concurrent reject cannot let an edit through (Codex P1-DATA-INTEGRITY-001, r2).
# `accepted` is intentionally editable (a re-accept re-installs the revised draft).
EDIT_BLOCKED_STATUSES = frozenset({"rejected", "superseded"})

# The §12.1 proposal schema, enforced structurally on every load so a
# malformed-but-parseable file is skipped rather than crashing a consumer or —
# worse — handing a traversal-shaped `name` to a path-building emitter.
_VALID_STATUSES = frozenset({"proposed", "accepted", "rejected", "superseded"})
_VALID_TYPES = frozenset({"skill", "rule"})
_VALID_CANDIDATE_TYPES = frozenset(
    {"repeated-correction", "repeated-workflow", "repeated-instruction", "cross-project-convention"}
)
# Which artifact families a given `type` may target (§12.1).
_VALID_TARGETS = {
    "rule": frozenset({"AGENTS.md", "CLAUDE.md"}),
    "skill": frozenset({"user-skill", "project-skill"}),
}
_SCORE_KEYS = ("recurrence", "breadth", "recency", "total")


def _is_number(value: Any) -> bool:
    """A real number — ``bool`` is an ``int`` subclass in Python, so exclude it
    explicitly (a ``recurrence: true`` must never read as ``1``)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_valid_proposal(doc: store.Document) -> bool:
    """Full structural §12.1 validation, shared by single- and bulk-load. A
    parseable-but-malformed document is rejected *before* any consumer
    (``recommend show``'s evidence loop) or emitter (the skill emitter's
    ``<slug>/SKILL.md`` path) sees it. Every required key and nested type is
    checked; only the two genuinely-optional edge cases (``project: null`` and
    ``evidence: []``) are allowed to be empty.

    Load-bearing specifics:

    - ``name`` MUST be a store-safe slug (``SLUG_RE`` forbids ``/``/``.``/``..``)
      **and** equal the file's own stem — so a hand-crafted ``good.md`` carrying
      ``name: ../../evil`` can never become a path component in the skill emitter.
    - ``status``/``type``/``candidate_type`` MUST be valid enums (required, not
      optional — a proposal without ``candidate_type`` would contribute no
      per-type reject feedback to the miner, so it is malformed, not tolerated).
    - ``target`` MUST be present and compatible with ``type``.
    - ``scores`` MUST carry all four numeric keys; ``evidence`` MUST be a list of
      **exactly**-shaped structured refs (round-trip equality, not just
      parseability — see ``_valid_evidence``); ``supersedes`` a list of valid
      slugs; ``created_at``/``updated_at`` ISO8601 strings; ``project`` a string
      or null; ``installed_path`` an **absolute** path string or null.
    """
    name = doc.get("name")
    if not isinstance(name, str) or not store.SLUG_RE.match(name) or name != doc.file_path.stem:
        return False
    if doc.get("status") not in _VALID_STATUSES:
        return False
    proposal_type = doc.get("type")
    if proposal_type not in _VALID_TYPES:
        return False
    if doc.get("candidate_type") not in _VALID_CANDIDATE_TYPES:
        return False
    if doc.get("target") not in _VALID_TARGETS[str(proposal_type)]:
        return False
    if not _valid_scores(doc.get("scores")):
        return False
    if not _valid_evidence(doc.get("evidence")):
        return False
    supersedes = doc.get("supersedes")
    if not isinstance(supersedes, list) or not all(
        isinstance(s, str) and store.SLUG_RE.match(s) for s in supersedes
    ):
        return False
    if not _is_iso(doc.get("created_at")) or not _is_iso(doc.get("updated_at")):
        return False
    project = doc.get("project")
    if project is not None and not isinstance(project, str):
        return False
    installed_path = doc.get("installed_path")
    if installed_path is None:
        return True
    return isinstance(installed_path, str) and Path(installed_path).is_absolute()


def _valid_scores(scores: Any) -> bool:
    """A ``scores`` mapping with all four §12.6 keys present and numeric."""
    return isinstance(scores, dict) and all(_is_number(scores.get(key)) for key in _SCORE_KEYS)


def _valid_evidence(evidence: Any) -> bool:
    """``evidence`` is a (possibly empty) list of **exactly**-shaped structured
    refs (§12.1). Each item must serialize/compare round-trip cleanly:
    ``EvidenceRef.from_frontmatter(item).to_frontmatter() == item``. Because
    ``from_frontmatter`` coerces values with ``str(...)`` and drops keys foreign
    to the selected ``kind``, a plain "does it parse" check would silently
    accept a non-string ``slug: 123`` or a forbidden extra key
    (``{"kind":"proposal","slug":"x","project":"…"}``); the round-trip rejects
    both — the canonical serialization only matches an item that already carries
    exactly that kind's keys with string values. An empty list is valid."""
    if not isinstance(evidence, list):
        return False
    for item in evidence:
        if not isinstance(item, dict):
            return False
        try:
            ref = EvidenceRef.from_frontmatter(item)
        except (KeyError, ValueError):
            return False
        # Exact shape (round-trip) AND safe string semantics (SLUG_RE / safe
        # basename) — a canonical `{"kind":"proposal","slug":"../bad"}` passes the
        # round-trip but must still be rejected before it reaches a path builder.
        if ref.to_frontmatter() != item or not ref.is_safe():
            return False
    return True


def _is_iso(value: Any) -> bool:
    """An ISO8601 timestamp string (§12.1 ``created_at``/``updated_at``) — reuses
    the ranker's fail-soft parser so a non-timestamp string is rejected."""
    return isinstance(value, str) and _parse_iso(value) is not None


@dataclass
class WriteOutcome:
    """What ``write_ranked`` did, per slug — for ``recommend run``'s terse
    summary and for tests to assert exactly which branch each candidate took."""

    created: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)  # near-duplicate of a rejected proposal
    skipped_decided: list[str] = field(default_factory=list)  # would reset a decided proposal
    skipped_malformed: list[str] = field(default_factory=list)  # existing unreadable proposal
    preserved_edits: list[str] = field(default_factory=list)  # user edit left intact


def write_ranked(
    root: Path,
    ranked: list[RankedCandidate],
    *,
    config: RecommendConfig | None = None,
    now: datetime | None = None,
) -> WriteOutcome:
    """Persist ranked candidates as ``proposed`` proposal files (§12.6),
    applying the full upsert / supersede / decline / preserve-edit discipline.
    ``now`` stamps ``created_at``/``updated_at`` and the ledger (injectable for
    deterministic tests). Returns a per-slug ``WriteOutcome``."""
    cfg = config if config is not None else load_config().recommend
    stamp = now if now is not None else datetime.now(UTC)
    at_iso = _iso(stamp)

    rejected_bodies = [rp.body for rp in corpus.load_ledger_summary(root).rejected_proposals]
    outcome = WriteOutcome()

    for candidate in ranked:
        _write_one(root, candidate, cfg, at_iso, rejected_bodies, outcome)
    return outcome


def _write_one(
    root: Path,
    candidate: RankedCandidate,
    cfg: RecommendConfig,
    at_iso: str,
    rejected_bodies: list[str],
    outcome: WriteOutcome,
) -> None:
    slug = candidate.slug
    body = redact_body(render_body(candidate))
    # Take the store-wide lifecycle lock for the whole load → decided-status check
    # → write → supersede → append. Without it, a miner refresh that loaded a
    # `proposed` proposal could resume *after* a concurrent `reject` committed and
    # re-write it back to `proposed` — silently undoing the decision (Codex
    # P1-DATA-INTEGRITY-001, round 2). The lock is re-acquired per candidate so a
    # long batch never starves an interactive accept/reject. `_apply_supersedes`
    # writes other slugs directly (never via the public locked functions), so this
    # does not re-enter the lock. Non-mutating branches (malformed/declined) hold
    # it only briefly.
    with _lifecycle_lock(root):
        existing = load_proposal(root, slug)
        path = proposal_path(root, slug)

        if existing is None:
            # Missing and malformed are deliberately distinct on the write path.
            # A malformed existing proposal is user state we cannot safely interpret,
            # so fail closed instead of silently replacing it (§12 Invariants).
            if path.exists():
                logger.warning("skipping %r: existing proposal is malformed", slug)
                outcome.skipped_malformed.append(slug)
                return
            # A brand-new proposal: decline if it merely re-states a still-rejected
            # one (belt-and-suspenders on the miner prompt, §12.6/D18).
            if _is_rejected_near_duplicate(body, rejected_bodies, cfg.near_duplicate_threshold):
                logger.info("declining %r: near-duplicate of a rejected proposal", slug)
                outcome.declined.append(slug)
                return
            _write_proposal(root, candidate, body, created_at=at_iso, updated_at=at_iso)
            outcome.superseded.extend(_apply_supersedes(root, candidate, at_iso))
            _append_ledger(root, slug, "proposed", at_iso, candidate.candidate_type)
            outcome.created.append(slug)
            return

        status = str(existing.get("status") or "proposed")
        if status in _DECIDED_STATUSES:
            # Never silently reset a decided proposal back to proposed (Invariant).
            outcome.skipped_decided.append(slug)
            return

        # An existing `proposed` proposal. Preserve a user's hand edit rather than
        # clobbering it with the miner's fresh draft (§12.6).
        if _edited_since_last_write(root, slug):
            outcome.preserved_edits.append(slug)
            return

        created_at = str(existing.get("created_at") or at_iso)
        _write_proposal(root, candidate, body, created_at=created_at, updated_at=at_iso)
        outcome.superseded.extend(_apply_supersedes(root, candidate, at_iso))
        _append_ledger(root, slug, "proposed", at_iso, candidate.candidate_type)
        outcome.refreshed.append(slug)


# --- supersede transition (§12.6) ------------------------------------------


def _apply_supersedes(root: Path, candidate: RankedCandidate, at_iso: str) -> list[str]:
    """Flip each still-``proposed`` slug named in ``candidate.supersedes`` to
    ``status: superseded``; a decided slug is left untouched (the "never
    overwrite a decided proposal" rule outranks supersede). The new proposal
    records every named slug in its own ``supersedes`` frontmatter regardless,
    for the linkage."""
    superseded: list[str] = []
    for target_slug in candidate.supersedes:
        if target_slug == candidate.slug:
            continue
        prior = load_proposal(root, target_slug)
        if prior is None or str(prior.get("status")) != "proposed":
            continue
        frontmatter = dict(prior.frontmatter)
        frontmatter["status"] = "superseded"
        frontmatter["updated_at"] = at_iso
        try:
            store.write_doc(proposal_path(root, target_slug), frontmatter, prior.body)
        except (store.InvalidSlugError, OSError):
            logger.warning("could not supersede %r", target_slug)
        else:
            superseded.append(target_slug)
    return superseded


# --- proposal file I/O ------------------------------------------------------


def _write_proposal(
    root: Path,
    candidate: RankedCandidate,
    body: str,
    *,
    created_at: str,
    updated_at: str,
) -> Path:
    """Write one ``proposed`` proposal file with the §12.1 frontmatter shape and
    a stable key order. ``body`` must already be redacted."""
    frontmatter: dict[str, Any] = {
        "name": candidate.slug,
        "status": "proposed",
        "type": candidate.type,
        "target": candidate.target,
        "project": candidate.project,
        "candidate_type": candidate.candidate_type,
        "scores": candidate.scores.to_frontmatter(),
        "evidence": evidence_to_frontmatter(candidate.evidence),
        # Only well-formed slugs — so a miner-supplied junk supersedes entry can
        # never make write_ranked emit a proposal that fails its own validator on
        # the next load (a decided slug named here is still left untouched by
        # _apply_supersedes; an unresolvable one simply matches nothing).
        "supersedes": [s for s in candidate.supersedes if store.SLUG_RE.match(s)],
        "created_at": created_at,
        "updated_at": updated_at,
        "installed_path": None,
    }
    return store.write_doc(proposal_path(root, candidate.slug), frontmatter, body)


def load_proposal(root: Path, slug: str) -> store.Document | None:
    """Load one proposal, fail-soft: a missing file or a malformed one (bad
    frontmatter / unparseable YAML / bad slug) returns ``None``, never raises
    (§12.6 — malformed proposal files are skipped, not fatal)."""
    try:
        path = proposal_path(root, slug)
    except store.InvalidSlugError:
        return None
    if not path.exists():
        return None
    try:
        doc = store.read_doc(path)
    except (ValueError, OSError):
        return None
    return doc if _is_valid_proposal(doc) else None


def load_all_proposals(root: Path) -> list[store.Document]:
    """Every proposal under ``<root>/proposals/``, sorted by the ``recommend
    list`` contract: ``total`` score descending, then ``created_at`` ascending,
    then ``name`` ascending (§12.6). Malformed files are skipped, never fatal;
    a missing directory yields ``[]``."""
    directory = corpus.proposals_dir(root)
    if not directory.exists():
        return []
    docs: list[store.Document] = []
    for path in sorted(directory.glob("*.md")):
        try:
            doc = store.read_doc(path)
        except (ValueError, OSError):
            continue
        if _is_valid_proposal(doc):
            docs.append(doc)
    docs.sort(key=_sort_key)
    return docs


def _sort_key(doc: store.Document) -> tuple[float, str, str]:
    scores = doc.get("scores")
    total = scores.get("total") if isinstance(scores, dict) else None
    total_f = float(total) if isinstance(total, (int, float)) else 0.0
    created_at = str(doc.get("created_at") or "")
    name = str(doc.get("name") or doc.file_path.stem)
    # `total` descending, so negate; created_at + name ascending as tie-breaks.
    return (-total_f, created_at, name)


# --- near-duplicate suppression (§12.6/D18) --------------------------------


def _is_rejected_near_duplicate(body: str, rejected_bodies: list[str], threshold: float) -> bool:
    return any(corpus.is_near_duplicate(body, rejected, threshold) for rejected in rejected_bodies)


# --- edit detection (§12.6) -------------------------------------------------


def _edited_since_last_write(root: Path, slug: str) -> bool:
    """True when the ledger holds an ``edited`` event newer than our last
    ``proposed`` write for this slug — the signal that ``recommend edit`` revised
    the body and a refresh must not silently clobber it. Fail-soft: a malformed
    ledger line is skipped, a missing ledger means "not edited"."""
    latest_edited: datetime | None = None
    latest_proposed: datetime | None = None
    for event in read_ledger(root):
        if event.get("slug") != slug:
            continue
        when = _parse_iso(event.get("at"))
        if when is None:
            continue
        kind = event.get("event")
        if kind == "edited":
            latest_edited = when if latest_edited is None else max(latest_edited, when)
        elif kind == "proposed":
            latest_proposed = when if latest_proposed is None else max(latest_proposed, when)
    if latest_edited is None:
        return False
    return latest_proposed is None or latest_edited > latest_proposed


def read_ledger(root: Path) -> list[dict[str, Any]]:
    """Parse the ledger into event dicts, skipping malformed lines (§12.2). A
    missing ledger yields ``[]``. Public (D1, workstream H): ``metrics.py``
    reuses this rather than reimplementing ledger-line parsing — the same
    fail-soft precedent ``curator/engine.py:read_fact_count_trend`` sets."""
    path = ledger_path(root)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _append_ledger(
    root: Path,
    slug: str,
    event: str,
    at_iso: str,
    candidate_type: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one ledger event line (§12.2). A ``proposed`` write records
    ``candidate_type`` for the miner's later per-type reject summary (§12.5);
    ``extra`` carries event-specific keys (a ``reverted`` event's drift
    ``reason``, ADR-0020) without every caller reimplementing the append."""
    record: dict[str, Any] = {"at": at_iso, "slug": slug, "event": event}
    if candidate_type is not None:
        record["candidate_type"] = candidate_type
    if extra:
        record.update(extra)
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def ledger_history(root: Path, slug: str) -> list[dict[str, Any]]:
    """Fail-soft ledger history for one proposal, oldest-first (§12.2)."""
    return [event for event in read_ledger(root) if event.get("slug") == slug]


def extract_draft(body: str) -> str | None:
    """Return the verbatim artifact draft between the managed markers."""
    if body.count(DRAFT_START) != 1 or body.count(DRAFT_END) != 1:
        return None
    start = body.find(DRAFT_START)
    end = body.find(DRAFT_END, start + len(DRAFT_START)) if start >= 0 else -1
    if start < 0 or end < 0:
        return None
    return body[start + len(DRAFT_START) : end].strip("\n")


def replace_draft(body: str, draft: str) -> str | None:
    """Replace only the managed draft region, preserving review prose."""
    if body.count(DRAFT_START) != 1 or body.count(DRAFT_END) != 1:
        return None
    start = body.find(DRAFT_START)
    end = body.find(DRAFT_END, start + len(DRAFT_START)) if start >= 0 else -1
    if start < 0 or end < 0:
        return None
    return f"{body[: start + len(DRAFT_START)]}\n{draft.rstrip()}\n{body[end:]}"


def save_edited_draft(root: Path, slug: str, draft: str, *, now: datetime | None = None) -> bool:
    """Redact and persist one user edit, appending exactly one ledger event.

    The rejected/superseded guard is enforced HERE, inside the lock, on the status
    re-read at write time — not only in the route/CLI before the call. Otherwise a
    caller that checked `proposed` and then raced a concurrent `reject` would still
    save, mutating a now-rejected draft and appending `edited` (Codex
    P1-DATA-INTEGRITY-001, round 2). Returns False on a blocked status so the
    existing "could not save" path fail-closes."""
    with _lifecycle_lock(root):
        doc = load_proposal(root, slug)
        if doc is None:
            return False
        if str(doc.get("status") or "proposed") in EDIT_BLOCKED_STATUSES:
            return False
        updated = replace_draft(doc.body, redact_body(draft))
        if updated is None:
            return False
        stamp = _iso(now if now is not None else datetime.now(UTC))
        frontmatter = dict(doc.frontmatter)
        frontmatter["updated_at"] = stamp
        store.write_doc(doc.file_path, frontmatter, updated)
        _append_ledger(root, slug, "edited", stamp, None)
        return True


@contextmanager
def _lifecycle_lock(root: Path) -> Iterator[None]:
    """Serialize one proposal-lifecycle transition (accept / reject / reopen /
    revert / edit) against every other writer of the **store-wide** proposal +
    ledger tree, so the load → status-check → write → append sequence is one
    atomic critical section (Codex P1-DATA-INTEGRITY-001).

    The per-project store write lock (ADR-0023 / G4) keys on
    ``memory_dir(project)`` and so does *not* cover ``<root>/proposals/`` or the
    recommender ledger, which are store-wide, single-profile paths. Without this,
    two callers — e.g. a CLI ``recommend reopen`` racing the running web UI — can
    both read the same ``rejected`` document, both pass the only-rejected guard,
    and both append a ``reopened`` event. Holding this lock across the whole
    transition means the second caller re-reads the *already-transitioned* status
    and fails its guard, so exactly one transition (and one ledger line) lands.

    We reuse the generic path-level ``core.lock.project_lock`` primitive, keyed on
    the recommender directory (``<root>/recommender/``, where ``ledger.jsonl``
    already lives). The lockfile is a ``.lock`` dotfile there — outside every
    proposal (``proposals/*.md``) and ledger read path — so readers never see it
    and never take it, exactly like the per-project lock's placement."""
    with lock.project_lock(ledger_path(root).parent):
        yield


def reject_proposal(
    root: Path, slug: str, *, reason: str | None = None, now: datetime | None = None
) -> str:
    """Reject a still-proposed proposal and append exactly one ledger event."""
    with _lifecycle_lock(root):
        doc = load_proposal(root, slug)
        if doc is None:
            raise ValueError(f"proposal {slug!r} not found or malformed")
        status = str(doc.get("status") or "proposed")
        if status != "proposed":
            raise ValueError(f"cannot reject proposal {slug!r}: status is {status}")
        stamp = _iso(now if now is not None else datetime.now(UTC))
        frontmatter = dict(doc.frontmatter)
        frontmatter["status"] = "rejected"
        frontmatter["updated_at"] = stamp
        store.write_doc(doc.file_path, frontmatter, doc.body)
        record: dict[str, Any] = {"at": stamp, "slug": slug, "event": "rejected"}
        # Carry the proposal's candidate_type so corpus.load_ledger_summary can build
        # the per-type reject counts the miner prompt feeds on (§12.2/§12.4/§12.5);
        # without it, every CLI rejection contributes nothing to that feedback.
        candidate_type = doc.get("candidate_type")
        if isinstance(candidate_type, str) and candidate_type:
            record["candidate_type"] = candidate_type
        if reason:
            record["reason"] = reason
        path = ledger_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return status


class ProposalChangedError(ValueError):
    """Raised when ``accept_proposal``'s ``expect`` guard finds the proposal on
    disk is no longer the document the caller validated (ADR-0020 D44, review
    P1-DATA-INTEGRITY-004)."""


def accept_proposal(
    root: Path,
    slug: str,
    *,
    target: str,
    installed_path: Path,
    installed_hash: str | None = None,
    expect: store.Document | None = None,
    now: datetime | None = None,
) -> None:
    """Record a successful artifact installation after the write completes.
    ``installed_hash`` (§12.9/ADR-0007 D2, workstream H): the artifact's content
    hash at accept time, so a later ``status --recommender`` survival check can
    tell "modified since acceptance" apart from "never touched" without
    diffing against anything else on disk. ``None`` for callers that don't
    compute one — the survival check falls back to existence-only for those
    (legacy) events.

    ``expect`` (ADR-0020 D44) binds this write to a document the caller already
    validated: if the proposal reloaded here differs from it, raise
    ``ProposalChangedError`` and write nothing. Without it a caller that checks
    the proposal and *then* calls this function has a window in between — this
    function does its own independent reload — so the version it validated need
    not be the version that becomes ``accepted`` (review P1-DATA-INTEGRITY-004).
    The ``expect`` guard closes the reachable check-then-write gap for a caller
    that validated the doc earlier; the ``_lifecycle_lock`` below additionally
    serializes this transition against a concurrent reject/reopen/revert of the
    same proposal (Codex P1-DATA-INTEGRITY-001), so the status re-check here is
    authoritative at the mutation boundary."""
    with _lifecycle_lock(root):
        doc = load_proposal(root, slug)
        if doc is None:
            raise ValueError(f"proposal {slug!r} not found or malformed")
        if expect is not None and (
            doc.frontmatter != expect.frontmatter or doc.body != expect.body
        ):
            raise ProposalChangedError(
                f"proposal {slug!r} changed between validation and this write — nothing recorded"
            )
        status = str(doc.get("status") or "proposed")
        if status in {"rejected", "superseded"}:
            raise ValueError(f"cannot accept proposal {slug!r}: status is {status}")
        stamp = _iso(now if now is not None else datetime.now(UTC))
        frontmatter = dict(doc.frontmatter)
        frontmatter.update(
            status="accepted", target=target, installed_path=str(installed_path), updated_at=stamp
        )
        store.write_doc(doc.file_path, frontmatter, doc.body)
        record: dict[str, Any] = {"at": stamp, "slug": slug, "event": "accepted", "target": target}
        if installed_hash is not None:
            record["installed_hash"] = installed_hash
        path = ledger_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- artifact liveness: is a managed artifact still live to orphan? (ADR-0020) --

#: The liveness states an accepted proposal's *managed artifact* can be in. What
#: matters for `revert` is whether reverting would orphan a still-installed
#: Neurobase artifact — NOT whether the target file's bytes survived verbatim
#: (that is survival, §12.9, a separate question). So the classification is by
#: the slug-scoped marker / skill ownership, re-derived from the proposal, never
#: by a whole-file hash (round-2 review F1: an edit *outside* a rule block flips
#: the file hash while the managed block stays live).
ARTIFACT_LIVE = "live"  #: rule sentinel present, or a SKILL.md exists — NON-revertable
ARTIFACT_MISSING = "missing"  #: target file absent entirely
ARTIFACT_ORPHANED = "orphaned"  #: target present but our block/skill is gone from it
ARTIFACT_UNRESOLVABLE = "unresolvable"  #: target can't be resolved — NON-revertable (fail closed)

#: The liveness states in which no live managed artifact remains to orphan, and
#: only these (ADR-0020 D43, review F1). `live` and `unresolvable` are refused:
#: the first would orphan a live artifact, the second cannot *prove* one is gone.
REVERTABLE_ARTIFACT_STATES = frozenset({ARTIFACT_MISSING, ARTIFACT_ORPHANED})


def latest_accepted_event(root: Path, slug: str) -> dict[str, Any] | None:
    """The most recent ``accepted`` ledger event for ``slug`` — a proposal can
    be re-accepted (accept is idempotent, §12.7), so there may be more than
    one. ``None`` when there is no resolvable ``accepted`` event at all. Used by
    ``metrics``'s survival check (§12.9)."""
    candidates = [
        (event, when)
        for event in ledger_history(root, slug)
        if event.get("event") == "accepted" and (when := _parse_iso(event.get("at"))) is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[1])[0]


def artifact_state(root: Path, doc: store.Document, slug: str) -> str:
    """Classify an accepted proposal's *managed artifact* by liveness (ADR-0020
    D43) — the question ``revert`` must answer. Returns ``live`` when the slug's
    opening sentinel (rule), or *any* SKILL.md at the canonical path (skill), is
    still on disk at **any**
    plausible install location, regardless of surrounding foreign bytes;
    ``missing`` when no candidate target exists; ``orphaned`` when a candidate
    exists but no longer holds our block/skill; ``unresolvable`` when the target
    can't be re-derived *or* a candidate can't be read — refused fail-closed
    rather than assumed gone.

    Every candidate is checked (``emitters.candidate_target_paths``: the recorded
    ``installed_path`` plus the re-derived target under every registered project
    root), because concluding "gone" from one stale root would orphan a live
    artifact at another (review F1, round 3). A candidate that raises ``OSError``
    — a directory or an unreadable file where the artifact should be — makes the
    verdict ``unresolvable`` rather than escaping as a traceback (review
    P2-CORRECTNESS-005): we cannot prove absence, so we refuse.

    Note this is deliberately NOT survival's whole-file-hash comparison
    (``metrics._survival_one``): a rule block stays live under any edit *outside*
    it, so reverting on a mere file-hash change would orphan a live artifact
    (round-2 review F1). The two questions are distinct and answered separately.
    ``slug`` is accepted for signature symmetry with the metrics helpers; the
    targets are re-derived from ``doc``."""
    from neurobase.recommender import emitters  # local: emitters imports proposals

    try:
        candidates = emitters.candidate_target_paths(root, doc)
    except ValueError:
        return ARTIFACT_UNRESOLVABLE

    present = False
    indeterminate = False
    for path in candidates:
        try:
            text = emitters.read_target_text(path)
        except (OSError, UnicodeError):
            # ANY failure to read a candidate — a directory, a permissions error
            # (OSError), or bytes that aren't valid UTF-8 (UnicodeDecodeError, a
            # UnicodeError) — leaves absence unproven, so it must not read as
            # "gone". Catching one specific exception type is what let round 4's
            # invalid-UTF-8 target escape (review P2-CORRECTNESS-005); the rule is
            # "could not read it", not "raised OSError".
            indeterminate = True
            continue
        if not path.exists():
            continue
        present = True
        if emitters.candidate_is_live(text, doc):
            return ARTIFACT_LIVE
    if indeterminate:
        return ARTIFACT_UNRESOLVABLE
    return ARTIFACT_ORPHANED if present else ARTIFACT_MISSING


def revert_proposal(root: Path, slug: str, *, now: datetime | None = None) -> str:
    """Repair the drift where the store still reads ``accepted`` but the
    proposal's managed artifact is no longer live on disk (§12.7, G2, ADR-0020):
    flip the proposal back to ``proposed``, clear its now-dangling
    ``installed_path``, and append exactly one ``reverted`` ledger event carrying
    the liveness ``reason``. Returns the prior status (always ``"accepted"`` on
    success).

    **Drift repair only — never an un-accept.** ``revert`` is allowed only when
    ``artifact_state`` is ``missing`` or ``orphaned`` — i.e. no live managed
    artifact remains to orphan. A ``live`` artifact (the slug's rule marker or
    any SKILL.md at the canonical path — *whatever* else changed around it) is
    refused,
    and so is an ``unresolvable`` one (target can't be re-derived, so absence
    cannot be proven). That boundary is load-bearing: with it removed,
    ``accept → revert → reject`` would leave a live Neurobase-managed artifact
    installed under a ``rejected`` proposal — precisely the orphaning §12.7's
    blocked ``reject``-on-``accepted`` rule exists to prevent (review F1). Round
    2 got this wrong by keying on a whole-file hash, so a user edit *outside* a
    rule block made the file "modified" and revertable while the block stayed
    live; liveness is judged by the sentinel / file existence instead.

    The artifact itself is still **never touched** on any path — v1 has no
    uninstall-by-command (ADR-0007, unchanged). To retire a live artifact, remove
    the managed block/skill by hand first; the proposal then reads
    ``orphaned``/``missing`` and reverts. Because the survival/precision metrics
    key on current frontmatter status rather than the ledger (§12.9), a reverted
    proposal drops out of them cleanly while the ledger keeps the full
    proposed→accepted→reverted history."""
    with _lifecycle_lock(root):
        doc = load_proposal(root, slug)
        if doc is None:
            raise ValueError(f"proposal {slug!r} not found or malformed")
        status = str(doc.get("status") or "proposed")
        if status != "accepted":
            raise ValueError(
                f"cannot revert proposal {slug!r}: status is {status} "
                "(only an accepted proposal can be reverted)"
            )
        state = artifact_state(root, doc, slug)
        refusal = revert_refusal(slug, doc, state)
        if refusal is not None:
            raise ValueError(refusal)
        stamp = _iso(now if now is not None else datetime.now(UTC))
        frontmatter = dict(doc.frontmatter)
        frontmatter["status"] = "proposed"
        frontmatter["installed_path"] = None
        frontmatter["updated_at"] = stamp
        store.write_doc(doc.file_path, frontmatter, doc.body)
        _append_ledger(root, slug, "reverted", stamp, None, {"reason": state})
        return status


def revert_refusal(slug: str, doc: store.Document, state: str) -> str | None:
    """The named-state error for a revert the liveness guard refuses, or ``None``
    when ``state`` is revertable. Both refusals say what to do next, since
    "remove the artifact first" is the sanctioned route to the un-accept revert
    deliberately is not.

    Public so the CLI can refuse *before* prompting for consent rather than
    after; ``revert_proposal`` remains the authority and re-checks at write
    time, so a CLI that skipped this would still be safe."""
    if state in REVERTABLE_ARTIFACT_STATES:
        return None
    where = doc.get("installed_path") or "its installed artifact"
    if state == ARTIFACT_UNRESOLVABLE:
        return (
            f"cannot revert proposal {slug!r}: its install target cannot be resolved "
            "(unregistered project or malformed proposal), so whether a managed "
            "artifact is still installed cannot be verified. revert repairs drift — "
            "it is not an uninstall (ADR-0020); refusing rather than risk orphaning "
            "a live artifact."
        )
    return (
        f"cannot revert proposal {slug!r}: {where} is still installed (its managed "
        "block/skill is live on disk). revert repairs drift — it is not an uninstall "
        "(ADR-0020). Remove the managed block/skill by hand first if you no longer "
        "want it."
    )


def reopen_proposal(root: Path, slug: str, *, now: datetime | None = None) -> str:
    """Reconsider a **rejected** proposal: flip it back to ``proposed`` and append
    one ``reopened`` ledger event. This is the only sanctioned
    ``rejected → proposed`` transition (ADR-0024). Returns the prior status
    (always ``"rejected"`` on success).

    §12.7 keeps a decided proposal durable so the *miner* never silently
    re-surfaces something the user already judged — but a user may change their
    mind. ``reopen`` is that explicit, ledgered action, and is deliberately narrow:

    - **Only ``rejected`` reopens.** ``accepted`` has its own paths (re-check /
      ``revert``, ADR-0020); a ``superseded`` proposal was replaced by a newer one,
      so reopening it would resurrect a duplicate — both are refused with a
      named-status error, exactly like the other blocked-status rules (§12.7).
    - The prior rejection **stays in the ledger** (and in the per-type reject
      feedback the miner reads): reconsidering does not rewrite the fact that it
      was rejected once. The next ``recommend run`` sees a live ``proposed``
      proposal and updates it in place rather than minting a duplicate.

    The whole load → only-rejected check → write → append runs under
    ``_lifecycle_lock`` so two concurrent reopens (a CLI ``recommend reopen``
    racing the running UI) cannot both pass the guard on one rejection and append
    two ``reopened`` events — exactly one wins and the other re-reads
    ``proposed`` and fails its guard (Codex P1-DATA-INTEGRITY-001)."""
    with _lifecycle_lock(root):
        doc = load_proposal(root, slug)
        if doc is None:
            raise ValueError(f"proposal {slug!r} not found or malformed")
        status = str(doc.get("status") or "proposed")
        if status != "rejected":
            raise ValueError(
                f"cannot reopen proposal {slug!r}: status is {status} "
                "(only a rejected proposal can be reopened)"
            )
        stamp = _iso(now if now is not None else datetime.now(UTC))
        frontmatter = dict(doc.frontmatter)
        frontmatter["status"] = "proposed"
        frontmatter["updated_at"] = stamp
        store.write_doc(doc.file_path, frontmatter, doc.body)
        candidate_type = doc.get("candidate_type")
        ct = candidate_type if isinstance(candidate_type, str) and candidate_type else None
        _append_ledger(root, slug, "reopened", stamp, ct)
        return status


# --- body rendering + redaction ---------------------------------------------


def render_body(candidate: RankedCandidate) -> str:
    """Render the human-facing proposal body (§12.1): title, rationale, an
    evidence summary derived from the recomputed counts, the draft artifact body
    as a blockquote, and caveats. This is the text a reviewer reads and
    ``recommend edit`` revises."""
    title = candidate.title.strip() or candidate.slug
    lines = [f"# {title}", ""]
    if candidate.rationale.strip():
        lines += [f"**Rationale:** {candidate.rationale.strip()}", ""]
    lines += [f"**Evidence summary:** {_evidence_summary(candidate)}", ""]
    lines += ["**Draft artifact body:**", "", DRAFT_START, candidate.draft.strip(), DRAFT_END]
    lines += ["", "**Caveats:** review the evidence before accepting; scores are advisory."]
    return "\n".join(lines) + "\n"


def _evidence_summary(candidate: RankedCandidate) -> str:
    scope = f"project `{candidate.project}`" if candidate.project else "multiple projects"
    return (
        f"recurred {candidate.scores.recurrence}× across {candidate.sessions} "
        f"session(s), {candidate.agents} agent(s), {candidate.projects} project(s) "
        f"({scope}); total score {candidate.scores.total}."
    )


def redact_body(body: str) -> str:
    """Redact the rendered body before it is persisted (§12.8 Invariant). Uses
    the same ``core/redact.py:redact`` pass every other write path uses, with any
    configured ``[redact].extra_patterns``."""
    return redact.redact(body, load_config().redact.extra_patterns)


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")
