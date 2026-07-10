"""Ranker (spec §12.6): turn the miner's candidate dicts into scored,
threshold-gated candidates, recomputing every count **strictly from evidence**.

The ranker deliberately does **not** trust the miner's self-reported
``occurrences``/``projects``/``agents`` (those are advisory display text only,
§12.5). It recomputes ``recurrence``/``sessions``/``agents``/``projects`` from
each candidate's structured ``evidence`` list plus the corpus loader's per-file
metadata — the ADR-0007 determinism guarantee (a fake brain need only emit a
correct evidence list, never correct arithmetic). ``proposals.py`` does the
writing; this module is pure compute.

Formulas (§12.6):

- ``recurrence = max(1, len(evidence))`` — the same number written to
  ``scores.recurrence``.
- ``sessions``/``agents`` = distinct values *reachable from evidence* (a ``raw``
  ref's frontmatter carries ``agent``+``session_id``; a ``curated`` ref's
  ``provenance`` resolves back through its own ``raw/<file>`` entries);
  ``projects`` = distinct project qualifiers named on the evidence refs.
- ``breadth = sessions × max(agents, 1) × max(projects, 1)``.
- ``recency_weight = max(0.05, 0.5 ** (days_since_last_occurrence /
  recency_halflife_days))``.
- ``total = recurrence × breadth × recency_weight``.

**Threshold gate** (§12.6/§12.11): keep a candidate only when
``len(evidence) >= min_occurrences`` (default 3) **and**
``sessions >= min_breadth_sessions`` (default 2). Failing either half is a
*silent drop*, never an error — the pattern may qualify on a later run.

Judgment calls, flagged for review (§12.6, ADR-0007 D21):

- **Curated→raw provenance depth = exactly one level.** A ``curated`` evidence
  ref is resolved to its fact, whose ``provenance`` entries of the form
  ``raw/<file>`` are each resolved *once* to a raw capture for its
  ``agent``/``session_id``/``captured_at``. Provenance entries that are not
  ``raw/`` (e.g. ``seed:...``) carry no session/agent metadata and are ignored
  for breadth. Raw captures have no provenance, so there is nothing deeper to
  chase — the recursion is naturally bounded at one hop.
- **An unresolved evidence ref can only *under*-count, never crash.** A ``raw``
  file that no longer exists (hand-deleted, D21) simply contributes no
  session/agent/timestamp; ``projects`` still counts the ref's named project
  (it is a property of the ref itself, not of the file), so a missing file
  never zeroes a project qualifier the evidence explicitly asserts. Nothing
  here raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.core import store
from neurobase.core.config import RecommendConfig, load_config
from neurobase.recommender import corpus
from neurobase.recommender.corpus import Corpus, EvidenceRef


@dataclass(frozen=True)
class Scores:
    """The four ``scores`` numbers written to a proposal's frontmatter (§12.1).
    ``recurrence`` is an ``int`` (``len(evidence)``); the rest are floats/ints
    derived by the formulas above."""

    recurrence: int
    breadth: int
    recency: float
    total: float

    def to_frontmatter(self) -> dict[str, Any]:
        return {
            "recurrence": self.recurrence,
            "breadth": self.breadth,
            "recency": self.recency,
            "total": self.total,
        }


@dataclass(frozen=True)
class RankedCandidate:
    """A miner candidate that passed the threshold gate, with every count
    recomputed from evidence. Carries exactly what ``proposals.py`` needs to
    render and persist a proposal file — the miner's advisory
    ``occurrences``/``projects``/``agents`` are intentionally dropped here."""

    slug: str
    type: str
    candidate_type: str
    title: str
    rationale: str
    draft: str
    target: str
    project: str | None
    supersedes: list[str]
    evidence: list[EvidenceRef]
    scores: Scores
    sessions: int
    agents: int
    projects: int


@dataclass(frozen=True)
class _Occurrence:
    """One session-shaped occurrence reachable from evidence: an
    ``agent``/``session_id`` pair and the capture's timestamp (any of which may
    be missing when a ref is unresolved — those simply don't contribute)."""

    agent: str | None
    session_id: str | None
    when: datetime | None


@dataclass
class _Derivation:
    """Accumulator for the per-candidate evidence walk."""

    occurrences: list[_Occurrence] = field(default_factory=list)
    projects: set[str] = field(default_factory=set)

    @property
    def sessions(self) -> int:
        return len({o.session_id for o in self.occurrences if o.session_id})

    @property
    def agents(self) -> int:
        return len({o.agent for o in self.occurrences if o.agent})

    @property
    def last_occurrence(self) -> datetime | None:
        times = [o.when for o in self.occurrences if o.when is not None]
        return max(times) if times else None


def rank(
    root: Path,
    candidates: list[dict[str, Any]],
    loaded: Corpus,
    *,
    config: RecommendConfig | None = None,
    now: datetime | None = None,
) -> list[RankedCandidate]:
    """Score each miner candidate from its evidence and return only those that
    clear the threshold gate (§12.6). ``loaded`` is the corpus the miner ran
    over — its per-file ``RawCapture``/``CuratedFact`` metadata is the fast path
    for evidence resolution; refs outside the corpus's cap window fall back to a
    direct, fail-soft store read. ``now``/``config`` are injectable for
    deterministic tests. Never writes, never raises on a bad ref."""
    cfg = config if config is not None else load_config().recommend
    reference = now if now is not None else datetime.now(UTC)

    raw_index = {(r.project, r.file): r for r in loaded.raw}
    curated_index = {(c.project, c.slug): c for c in loaded.curated}

    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        result = _rank_one(root, candidate, cfg, reference, raw_index, curated_index)
        if result is not None:
            ranked.append(result)
    return ranked


def _rank_one(
    root: Path,
    candidate: dict[str, Any],
    cfg: RecommendConfig,
    now: datetime,
    raw_index: dict[tuple[str, str], corpus.RawCapture],
    curated_index: dict[tuple[str, str], corpus.CuratedFact],
) -> RankedCandidate | None:
    refs = _evidence_refs(candidate)
    recurrence = max(1, len(refs))

    derivation = _Derivation()
    for ref in refs:
        _walk_ref(root, ref, raw_index, curated_index, derivation)

    sessions = derivation.sessions
    agents = derivation.agents
    projects = len(derivation.projects)

    # Threshold gate — a silent drop (not an error) on either failure (§12.6).
    if len(refs) < cfg.min_occurrences or sessions < cfg.min_breadth_sessions:
        return None

    breadth = sessions * max(agents, 1) * max(projects, 1)
    recency = _recency_weight(derivation.last_occurrence, now, cfg.recency_halflife_days)
    total = round(recurrence * breadth * recency, 4)
    scores = Scores(recurrence=recurrence, breadth=breadth, recency=recency, total=total)

    return RankedCandidate(
        slug=str(candidate["slug"]),
        type=str(candidate["type"]),
        candidate_type=str(candidate["candidate_type"]),
        title=str(candidate.get("title") or ""),
        rationale=str(candidate.get("rationale") or ""),
        draft=str(candidate.get("draft") or ""),
        target=str(candidate.get("target") or ""),
        project=_source_project(derivation.projects),
        supersedes=[str(s) for s in candidate.get("supersedes") or []],
        evidence=refs,
        scores=scores,
        sessions=sessions,
        agents=agents,
        projects=projects,
    )


def _evidence_refs(candidate: dict[str, Any]) -> list[EvidenceRef]:
    """Rebuild structured refs from the candidate's (already-normalized by the
    miner) evidence dicts. Any still-malformed entry is dropped — it just leaves
    less to count, never a raise."""
    out: list[EvidenceRef] = []
    for item in candidate.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        try:
            out.append(EvidenceRef.from_frontmatter(item))
        except (ValueError, KeyError):
            continue
    return out


def _walk_ref(
    root: Path,
    ref: EvidenceRef,
    raw_index: dict[tuple[str, str], corpus.RawCapture],
    curated_index: dict[tuple[str, str], corpus.CuratedFact],
    derivation: _Derivation,
) -> None:
    """Fold one evidence ref into the derivation. ``projects`` is taken from the
    ref itself (so an unresolved file never zeroes an asserted project);
    ``sessions``/``agents``/recency require resolving to a raw capture and are
    fail-soft — an unresolvable target contributes nothing."""
    if ref.kind == "raw" and ref.project and ref.file:
        derivation.projects.add(ref.project)
        occ = _raw_occurrence(root, ref.project, ref.file, raw_index)
        if occ is not None:
            derivation.occurrences.append(occ)
    elif ref.kind == "curated" and ref.project and ref.slug:
        derivation.projects.add(ref.project)
        for raw_file in _curated_provenance_raws(root, ref.project, ref.slug, curated_index):
            occ = _raw_occurrence(root, ref.project, raw_file, raw_index)
            if occ is not None:
                derivation.occurrences.append(occ)
    # `proposal` refs carry no project/session/agent metadata — they count
    # toward recurrence (len(evidence)) but not breadth.


def _raw_occurrence(
    root: Path,
    project: str,
    file: str,
    raw_index: dict[tuple[str, str], corpus.RawCapture],
) -> _Occurrence | None:
    """Metadata for one raw capture — from the corpus index when present (the
    common case), else a direct fail-soft store read for a ref outside the
    corpus's cap window. Returns ``None`` when the file no longer resolves."""
    cached = raw_index.get((project, file))
    if cached is not None:
        return _Occurrence(
            agent=cached.agent or None,
            session_id=cached.session_id,
            when=_parse_iso(cached.captured_at),
        )
    resolved = corpus.resolve_evidence(root, EvidenceRef.raw(project, file))
    if not resolved.resolved or resolved.path is None:
        return None
    try:
        doc = store.read_doc(resolved.path)
    except (ValueError, OSError):
        return None
    return _Occurrence(
        agent=(str(doc.get("agent")) or None) if doc.get("agent") else None,
        session_id=(str(doc.get("session_id")) if doc.get("session_id") else None),
        when=_parse_iso(doc.get("captured_at")),
    )


def _curated_provenance_raws(
    root: Path,
    project: str,
    slug: str,
    curated_index: dict[tuple[str, str], corpus.CuratedFact],
) -> list[str]:
    """The ``raw/<file>`` provenance basenames of one curated fact (one hop) —
    from the corpus index when present, else a direct fail-soft read of the
    curated (or tombstoned) file. Non-``raw/`` provenance entries carry no
    session/agent metadata and are skipped."""
    cached = curated_index.get((project, slug))
    provenance: list[str]
    if cached is not None:
        provenance = cached.provenance
    else:
        resolved = corpus.resolve_evidence(root, EvidenceRef.curated(project, slug))
        if not resolved.resolved or resolved.path is None:
            return []
        try:
            doc = store.read_doc(resolved.path)
        except (ValueError, OSError):
            return []
        provenance = [str(p) for p in (doc.get("provenance") or [])]
    return [entry[len("raw/") :] for entry in provenance if entry.startswith("raw/")]


def _recency_weight(last: datetime | None, now: datetime, halflife_days: int) -> float:
    """``max(0.05, 0.5 ** (days_since_last / halflife))`` (§12.6). No resolvable
    occurrence timestamp ⇒ treat as most-recent (weight ``1.0``); such a
    candidate has ``sessions == 0`` and is dropped by the gate anyway, so this
    fallback never inflates a written proposal."""
    if last is None:
        return 1.0
    days_since = max(0.0, (now - last).total_seconds() / 86400.0)
    if halflife_days <= 0:
        return 1.0
    return round(max(0.05, 0.5 ** (days_since / halflife_days)), 4)


def _source_project(projects: set[str]) -> str | None:
    """A single-project candidate names that project in frontmatter; a
    cross-project one (evidence spanning several projects, or none) has
    ``project: null`` (§12.1)."""
    return next(iter(projects)) if len(projects) == 1 else None


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO8601 (``...Z``) timestamp, fail-soft to ``None``; naive
    values are assumed UTC so comparisons never raise on tz-mismatch."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
