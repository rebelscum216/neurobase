"""Recommender metrics (spec §12.9; ADR-0007 D19) — the numbers ``status
--recommender`` prints.

Two distinct denominators (D19), so an intermediate edit never dilutes
precision:

- ``decided`` = count of proposals whose **current** ``status`` is
  ``accepted`` or ``rejected`` **and** whose ledger history actually contains
  the matching event — the ledger, not a proposal file's own ``status`` field,
  is the authoritative record of a decision (an empty/missing ledger, or one
  missing the event for a given slug, contributes nothing, however a proposal
  file's frontmatter reads). One proposal contributes **at most 1** to
  ``decided``, regardless of how many ``edited`` events preceded its final
  decision.
- ``precision`` = ``accepted / decided``, or ``None`` ("insufficient data")
  when ``decided == 0`` — never a crash/divide-by-zero.
- ``edited_rate`` = (decided proposals whose ledger holds ``>=1`` ``edited``
  event) / ``decided``. Both ``precision`` and ``edited_rate`` are computed
  **only** over this proposal-counted denominator — never over raw ledger
  event counts.
- ``reviewed_events`` is a separate, secondary, **event-counted** metric: the
  literal raw count of ``accepted``+``rejected``+``edited`` ledger *lines*
  (not proposals). A proposal edited three times before acceptance
  contributes 4 to ``reviewed_events`` (3 ``edited`` + 1 ``accepted``) but
  exactly 1 to ``decided`` — this is the specific double-counting bug D19
  exists to prevent, so ``reviewed_events`` is never used as a denominator
  anywhere in this module.
- ``survival``: for each accepted proposal, checked opportunistically.
  Before ``survival_window_days`` (``RecommendConfig``, default 30) have
  elapsed since the proposal's most recent ``accepted`` ledger event, an
  absent/modified artifact reports ``"insufficient_data"``, **never**
  ``"not_survived"`` — only past the window does a missing-or-modified
  artifact flip survival to ``"not_survived"``.
- ``recurrence_reduction``: advisory/best-effort only (explicitly *not* a
  gating MUST, per the plan's "opportunistic v1" framing) — see
  ``_recurrence_reduction`` below for the exact (intentionally simple)
  approach.
- An empty ledger/proposal set reports "insufficient data" for every metric,
  never a crash. A malformed ledger line is skipped by the fail-soft
  ``proposals.read_ledger``/``proposals.load_all_proposals`` readers this
  module reuses — never reimplemented here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.core import store
from neurobase.core.config import RecommendConfig, load_config
from neurobase.recommender import corpus, proposals
from neurobase.recommender.ranker import _parse_iso

_REVIEWED_EVENTS = frozenset({"accepted", "rejected", "edited"})


@dataclass(frozen=True)
class Metrics:
    """The §12.9 metrics contract. ``None`` means "insufficient data" for that
    metric (never a crash/divide-by-zero); ``survival`` maps slug ->
    ``"survived"`` | ``"not_survived"`` | ``"insufficient_data"``."""

    decided: int
    accepted: int
    rejected: int
    precision: float | None
    edited_rate: float | None
    reviewed_events: int
    survival: dict[str, str]
    recurrence_reduction: float | None


def compute_metrics(
    root: Path,
    *,
    config: RecommendConfig | None = None,
    now: datetime | None = None,
) -> Metrics:
    """Compute the full §12.9 metrics contract. ``now``/``config`` are
    injectable for deterministic tests (mirrors ``ranker.rank``). Never
    raises: an empty ledger/proposal set, a malformed ledger line, or a
    missing/modified artifact all resolve to a documented fallback rather than
    a crash or divide-by-zero."""
    cfg = config if config is not None else load_config().recommend
    reference = now if now is not None else datetime.now(UTC)

    decided = 0
    accepted = 0
    rejected = 0
    edited_decided = 0
    accepted_docs: list[tuple[str, store.Document]] = []

    for doc in proposals.load_all_proposals(root):
        status = str(doc.get("status") or "")
        if status not in {"accepted", "rejected"}:
            continue
        slug = str(doc.get("name") or doc.file_path.stem)
        history = proposals.ledger_history(root, slug)
        # §12.9: the ledger, not the proposal file's own `status` field, is the
        # authoritative record of a decision — an empty/missing ledger (or one
        # missing the matching event for this slug, e.g. an orphaned proposal
        # file whose status was set outside accept_proposal/reject_proposal)
        # must not silently manufacture a decided count from stale frontmatter.
        # Only count a proposal toward `decided` when the ledger itself
        # confirms the matching event actually happened.
        if not any(event.get("event") == status for event in history):
            continue
        decided += 1
        if status == "accepted":
            accepted += 1
            accepted_docs.append((slug, doc))
        else:
            rejected += 1
        if any(event.get("event") == "edited" for event in history):
            edited_decided += 1

    precision = (accepted / decided) if decided > 0 else None
    edited_rate = (edited_decided / decided) if decided > 0 else None

    # Event-counted, deliberately NOT proposal-scoped (D19): the raw ledger
    # line count, never the denominator of precision/edited_rate above.
    reviewed_events = sum(
        1 for event in proposals.read_ledger(root) if event.get("event") in _REVIEWED_EVENTS
    )

    survival = {
        slug: _survival_one(root, slug, doc, cfg.survival_window_days, reference)
        for slug, doc in accepted_docs
    }

    recurrence_reduction = _recurrence_reduction(root, accepted_docs, reference)

    return Metrics(
        decided=decided,
        accepted=accepted,
        rejected=rejected,
        precision=precision,
        edited_rate=edited_rate,
        reviewed_events=reviewed_events,
        survival=survival,
        recurrence_reduction=recurrence_reduction,
    )


# --- survival (§12.9, ADR-0007 D2) ------------------------------------------


def _latest_accepted_event(root: Path, slug: str) -> dict[str, Any] | None:
    """The most recent ``accepted`` ledger event for ``slug`` — a proposal can
    be re-accepted (accept is idempotent, §12.7), so there may be more than
    one. ``None`` when there is no resolvable ``accepted`` event at all."""
    candidates = [
        (event, when)
        for event in proposals.ledger_history(root, slug)
        if event.get("event") == "accepted" and (when := _parse_iso(event.get("at"))) is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[1])[0]


def _survival_one(
    root: Path,
    slug: str,
    doc: store.Document,
    window_days: int,
    now: datetime,
) -> str:
    """Survival for one accepted proposal (§12.9). Before
    ``survival_window_days`` have elapsed since the most recent ``accepted``
    ledger event: ``"insufficient_data"``, never ``"not_survived"``. Past the
    window: ``"not_survived"`` if the ``installed_path`` file is missing, or
    if the event recorded an ``installed_hash`` that no longer matches the
    file's current bytes; ``"survived"`` otherwise. A ledger event that
    predates the ``installed_hash`` feature (a legacy proposal, D2) falls back
    to existence-only — it cannot detect modification, only presence."""
    event = _latest_accepted_event(root, slug)
    if event is None:
        return "insufficient_data"
    accepted_at = _parse_iso(event.get("at"))
    if accepted_at is None:
        return "insufficient_data"
    elapsed_days = (now - accepted_at).total_seconds() / 86400.0
    if elapsed_days < window_days:
        return "insufficient_data"

    installed_path = doc.get("installed_path")
    if not isinstance(installed_path, str) or not installed_path:
        return "insufficient_data"
    path = Path(installed_path)
    if not path.exists():
        return "not_survived"

    installed_hash = event.get("installed_hash")
    if isinstance(installed_hash, str) and installed_hash:
        try:
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return "not_survived"
        return "survived" if current_hash == installed_hash else "not_survived"
    # Legacy accepted event with no stored hash: existence-only fallback (D2).
    return "survived"


# --- recurrence reduction (advisory, §12.9, ADR-0007 D19) -------------------


def _recurrence_reduction(
    root: Path,
    accepted_docs: list[tuple[str, store.Document]],
    now: datetime,
) -> float | None:
    """Advisory, best-effort "did acceptance reduce recurrence?" signal — NOT
    a gating MUST (D19). For each accepted proposal, count near-duplicate raw
    captures (``corpus.is_near_duplicate`` against the proposal's own rendered
    body) before vs after its most recent acceptance timestamp, then report
    the aggregate ``after / before`` ratio across every accepted proposal with
    a resolvable "before" count. Deliberately simple: no accepted proposals at
    all, or zero "before" occurrences to compare against, both report
    ``None`` ("insufficient data") rather than a misleading 0/0 or an
    undefined ratio."""
    if not accepted_docs:
        return None
    loaded = corpus.load_corpus(root, now=now)
    total_before = 0
    total_after = 0
    for slug, doc in accepted_docs:
        event = _latest_accepted_event(root, slug)
        accepted_at = _parse_iso(event.get("at")) if event is not None else None
        if accepted_at is None:
            continue
        for capture in loaded.raw:
            when = _parse_iso(capture.captured_at)
            if when is None or not corpus.is_near_duplicate(doc.body, capture.body):
                continue
            if when < accepted_at:
                total_before += 1
            else:
                total_after += 1
    if total_before == 0:
        return None
    return round(total_after / total_before, 4)
