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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.core import redact, store
from neurobase.core.config import RecommendConfig, load_config
from neurobase.recommender import corpus
from neurobase.recommender.corpus import evidence_to_frontmatter, ledger_path, proposal_path
from neurobase.recommender.ranker import RankedCandidate, _parse_iso

logger = logging.getLogger(__name__)

DRAFT_START = "<!-- neurobase:draft:start -->"
DRAFT_END = "<!-- neurobase:draft:end -->"

# Statuses that a fresh `proposed` render must never overwrite (§12.6 Invariant).
_DECIDED_STATUSES = frozenset({"accepted", "rejected", "superseded"})

# The §12.1 proposal schema, enforced structurally on every load so a
# malformed-but-parseable file is skipped rather than crashing a consumer or —
# worse — handing a traversal-shaped `name` to a path-building emitter.
_VALID_STATUSES = frozenset({"proposed", "accepted", "rejected", "superseded"})
_VALID_TYPES = frozenset({"skill", "rule"})
_VALID_CANDIDATE_TYPES = frozenset(
    {"repeated-correction", "repeated-workflow", "repeated-instruction", "cross-project-convention"}
)


def _is_valid_proposal(doc: store.Document) -> bool:
    """Structural §12.1 validation, shared by single- and bulk-load. Rejects a
    parseable-but-malformed document *before* any consumer (``recommend show``'s
    evidence loop) or emitter (the skill emitter's ``<slug>/SKILL.md`` path)
    sees it:

    - ``name`` MUST be a store-safe slug (``SLUG_RE``, which already forbids
      ``/``/``.``/``..``) **and** equal the file's own stem — so a hand-crafted
      ``good.md`` carrying ``name: ../../evil`` can never become a path
      component in the skill emitter;
    - ``status``/``type`` MUST be valid enums; ``candidate_type`` too when
      present;
    - ``evidence`` MUST be a list of mappings (``recommend show`` iterates it and
      calls ``.get`` on each item — a bare string would raise ``AttributeError``
      past the list/show fail-soft invariant);
    - ``scores``/``supersedes`` MUST be a mapping/list when present.
    """
    name = doc.get("name")
    if not isinstance(name, str) or not store.SLUG_RE.match(name) or name != doc.file_path.stem:
        return False
    if doc.get("status") not in _VALID_STATUSES:
        return False
    if doc.get("type") not in _VALID_TYPES:
        return False
    candidate_type = doc.get("candidate_type")
    if candidate_type is not None and candidate_type not in _VALID_CANDIDATE_TYPES:
        return False
    evidence = doc.get("evidence")
    if evidence is not None and (
        not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence)
    ):
        return False
    scores = doc.get("scores")
    if scores is not None and not isinstance(scores, dict):
        return False
    supersedes = doc.get("supersedes")
    return supersedes is None or isinstance(supersedes, list)


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
        "supersedes": list(candidate.supersedes),
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
    for event in _read_ledger(root):
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


def _read_ledger(root: Path) -> list[dict[str, Any]]:
    """Parse the ledger into event dicts, skipping malformed lines (§12.2). A
    missing ledger yields ``[]``."""
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
    root: Path, slug: str, event: str, at_iso: str, candidate_type: str | None
) -> None:
    """Append one ledger event line (§12.2). A ``proposed`` write records
    ``candidate_type`` for the miner's later per-type reject summary (§12.5)."""
    record: dict[str, Any] = {"at": at_iso, "slug": slug, "event": event}
    if candidate_type is not None:
        record["candidate_type"] = candidate_type
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def ledger_history(root: Path, slug: str) -> list[dict[str, Any]]:
    """Fail-soft ledger history for one proposal, oldest-first (§12.2)."""
    return [event for event in _read_ledger(root) if event.get("slug") == slug]


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
    """Redact and persist one user edit, appending exactly one ledger event."""
    doc = load_proposal(root, slug)
    if doc is None:
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


def reject_proposal(
    root: Path, slug: str, *, reason: str | None = None, now: datetime | None = None
) -> str:
    """Reject a still-proposed proposal and append exactly one ledger event."""
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


def accept_proposal(
    root: Path,
    slug: str,
    *,
    target: str,
    installed_path: Path,
    now: datetime | None = None,
) -> None:
    """Record a successful artifact installation after the write completes."""
    doc = load_proposal(root, slug)
    if doc is None:
        raise ValueError(f"proposal {slug!r} not found or malformed")
    status = str(doc.get("status") or "proposed")
    if status in {"rejected", "superseded"}:
        raise ValueError(f"cannot accept proposal {slug!r}: status is {status}")
    stamp = _iso(now if now is not None else datetime.now(UTC))
    frontmatter = dict(doc.frontmatter)
    frontmatter.update(
        status="accepted", target=target, installed_path=str(installed_path), updated_at=stamp
    )
    store.write_doc(doc.file_path, frontmatter, doc.body)
    record = {"at": stamp, "slug": slug, "event": "accepted", "target": target}
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


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
