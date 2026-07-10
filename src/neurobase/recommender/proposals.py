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

# Statuses that a fresh `proposed` render must never overwrite (§12.6 Invariant).
_DECIDED_STATUSES = frozenset({"accepted", "rejected", "superseded"})


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
        return store.read_doc(path)
    except (ValueError, OSError):
        return None


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
            docs.append(store.read_doc(path))
        except (ValueError, OSError):
            continue
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
    lines += ["**Draft artifact body:**", ""]
    lines += _blockquote(candidate.draft.strip())
    lines += ["", "**Caveats:** review the evidence before accepting; scores are advisory."]
    return "\n".join(lines) + "\n"


def _evidence_summary(candidate: RankedCandidate) -> str:
    scope = f"project `{candidate.project}`" if candidate.project else "multiple projects"
    return (
        f"recurred {candidate.scores.recurrence}× across {candidate.sessions} "
        f"session(s), {candidate.agents} agent(s), {candidate.projects} project(s) "
        f"({scope}); total score {candidate.scores.total}."
    )


def _blockquote(text: str) -> list[str]:
    if not text:
        return ["> _(no draft body)_"]
    return [f"> {line}" if line else ">" for line in text.splitlines()]


def redact_body(body: str) -> str:
    """Redact the rendered body before it is persisted (§12.8 Invariant). Uses
    the same ``core/redact.py:redact`` pass every other write path uses, with any
    configured ``[redact].extra_patterns``."""
    return redact.redact(body, load_config().redact.extra_patterns)


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")
