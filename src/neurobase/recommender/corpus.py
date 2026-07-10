"""Corpus loader + structured evidence model (spec §12.4, §12.1; ADR-0007
D17/D18/D21).

A pure, read-only aggregator the miner (workstream D) runs over. Across
**every** registered project (spec §10 registry) it gathers, without ever
writing:

1. active curated facts (uncapped — the curator already keeps this set small,
   spec §2);
2. recent raw captures, capped per project by ``raw_lookback_days`` **and**
   ``raw_cap_per_project`` (whichever yields fewer, ADR-0007 D17);
3. an accepted/rejected ledger summary (the ledger doesn't exist until
   workstream F — it is read fail-soft/empty here).

Plus the structured evidence model (§12.1): ``EvidenceRef`` values that
serialize cleanly into proposal frontmatter, and fail-soft resolution (D21 — a
missing/tombstoned/pruned target resolves to "unresolved", never raises, and is
never dropped from the frontmatter list).

Fail-soft is the governing rule throughout: **one missing or malformed project
tree must never blind the miner to every other project** (§12.4) — such a
project is skipped and named in ``Corpus.skipped_projects``, not fatal.

Design notes for the miner/ranker (workstreams D/E) that consume this:

- The loader returns **dataclasses**, not bare dicts — typed, mypy-checked, and
  the same house style as ``core.search.SearchHit`` / ``core.store.Document``.
  Each ``RawCapture`` carries the per-file ``agent``/``session_id`` metadata the
  ranker recomputes breadth from (§12.6), so the ranker never has to re-open the
  raw files the loader already read.
- ``curated`` / ``raw`` are **flat lists** carrying their own ``project`` field
  rather than a per-project mapping: the miner iterates them to build one prompt,
  and every item already knows how to name itself as an ``EvidenceRef`` (see
  ``CuratedFact.as_evidence`` / ``RawCapture.as_evidence``).
- ``jaccard_similarity`` / ``is_near_duplicate`` (ADR-0007 D18) live here, next
  to the ledger summary that first needs them, and are imported by the miner's
  prompt-builder (§12.5) and the ranker's suppression check (§12.6) — one
  deterministic definition, reused, so no fake brain ever has to fake a
  similarity judgment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from neurobase.core import projects, search, store
from neurobase.core.config import RecommendConfig, load_config

# Canonical on-disk locations (spec §12.1 / §12.2), defined here so every later
# workstream (E/F/H) imports one source of truth rather than re-deriving them.
_PROPOSALS_DIRNAME = "proposals"
_LEDGER_RELPATH = ("recommender", "ledger.jsonl")


def proposals_dir(root: Path) -> Path:
    """``<root>/proposals/`` — one ``<slug>.md`` file per proposal (§12.1)."""
    return root / _PROPOSALS_DIRNAME


def proposal_path(root: Path, slug: str) -> Path:
    """``<root>/proposals/<slug>.md``. Validates ``slug`` against the store's
    ``SLUG_RE`` (§12.1: ``<slug>`` matches ``^[a-z0-9-]+$``) so a proposal path
    can never escape ``proposals/`` — the same boundary discipline
    ``store.memory_dir`` enforces for project slugs. A caller writing a proposal
    should never pass an invalid slug; ``resolve_evidence`` catches this and
    reports the ref unresolved rather than propagating the raise."""
    if not _valid_slug(slug):
        raise store.InvalidSlugError(f"invalid proposal slug: {slug!r} (must match ^[a-z0-9-]+$)")
    return proposals_dir(root) / f"{slug}.md"


def ledger_path(root: Path) -> Path:
    """``<root>/recommender/ledger.jsonl`` — append-only event log (§12.2)."""
    return root.joinpath(*_LEDGER_RELPATH)


# --- structured evidence model (§12.1) -------------------------------------


@dataclass(frozen=True)
class EvidenceRef:
    """A structured evidence reference (§12.1). One of three kinds:

    - ``curated`` → ``{"kind": "curated", "project": ..., "slug": ...}``
    - ``raw``     → ``{"kind": "raw", "project": ..., "file": ...}``
    - ``proposal``→ ``{"kind": "proposal", "slug": ...}``

    Constructed via the three classmethods so an ill-formed ref (a ``proposal``
    with a ``project``, a ``curated`` with a ``file``) is unrepresentable. The
    ``project``/``slug``/``file`` fields default to ``None`` and only the ones a
    given kind uses are serialized — ``to_frontmatter`` never emits a ``None``.
    """

    kind: str
    project: str | None = None
    slug: str | None = None
    file: str | None = None

    @classmethod
    def curated(cls, project: str, slug: str) -> EvidenceRef:
        return cls(kind="curated", project=project, slug=slug)

    @classmethod
    def raw(cls, project: str, file: str) -> EvidenceRef:
        return cls(kind="raw", project=project, file=file)

    @classmethod
    def proposal(cls, slug: str) -> EvidenceRef:
        return cls(kind="proposal", slug=slug)

    def to_frontmatter(self) -> dict[str, str]:
        """The exact §12.1 mapping shape for this kind — only that kind's keys,
        never a ``None`` value. This is what ``store.write_doc`` dumps
        block-style into a proposal's ``evidence:`` list."""
        if self.kind == "curated":
            return {
                "kind": "curated",
                "project": _require(self.project),
                "slug": _require(self.slug),
            }
        if self.kind == "raw":
            return {"kind": "raw", "project": _require(self.project), "file": _require(self.file)}
        if self.kind == "proposal":
            return {"kind": "proposal", "slug": _require(self.slug)}
        raise ValueError(f"unknown evidence kind: {self.kind!r}")

    @classmethod
    def from_frontmatter(cls, data: dict[str, Any]) -> EvidenceRef:
        """Rebuild a ref from a proposal's stored frontmatter mapping (the
        inverse of ``to_frontmatter``), for the resolution/read side."""
        kind = data.get("kind")
        if kind == "curated":
            return cls.curated(str(data["project"]), str(data["slug"]))
        if kind == "raw":
            return cls.raw(str(data["project"]), str(data["file"]))
        if kind == "proposal":
            return cls.proposal(str(data["slug"]))
        raise ValueError(f"unknown evidence kind: {kind!r}")

    def is_safe(self) -> bool:
        """True when this ref's *string values* are store-safe (§12.1), not just
        its keys/shape: every project/fact/proposal slug matches ``SLUG_RE`` and a
        ``raw`` ``file`` is a safe basename. A canonical-shaped ref carrying a
        traversal-valued string (``slug: "../bad"``, a ``file`` with separators)
        is **not** safe — this is the boundary that keeps such a value from ever
        reaching a path builder, enforced both before a ref is persisted and when
        one is read back."""
        if self.kind == "curated":
            return (
                self.project is not None
                and _valid_slug(self.project)
                and self.slug is not None
                and _valid_slug(self.slug)
            )
        if self.kind == "raw":
            return (
                self.project is not None
                and _valid_slug(self.project)
                and self.file is not None
                and _is_safe_raw_basename(self.file)
            )
        if self.kind == "proposal":
            return self.slug is not None and _valid_slug(self.slug)
        return False


def _require(value: str | None) -> str:
    if value is None:
        raise ValueError("evidence ref is missing a required field for its kind")
    return value


def _valid_slug(slug: str) -> bool:
    """A curated/proposal slug is store-safe iff it matches ``SLUG_RE`` — that
    regex already forbids ``/``, ``.``, and ``..``, so a valid slug can never
    traverse out of its directory (§12.1)."""
    return bool(store.SLUG_RE.match(slug))


def _is_safe_raw_basename(file: str) -> bool:
    """A ``raw`` evidence ``file`` must name a single file *inside* the
    project's ``raw/`` dir — a bare ``*.md`` basename, never an absolute path, a
    path with separators, or a ``..`` traversal. This boundary is load-bearing:
    joining an absolute component onto a ``Path`` silently discards the ``raw/``
    prefix (``Path("…/raw") / "/etc/passwd"`` is ``/etc/passwd``), so without it
    resolution could escape the store entirely."""
    return (
        bool(file)
        and "/" not in file
        and "\\" not in file
        and file not in (".", "..")
        and file.endswith(".md")
    )


def evidence_to_frontmatter(refs: list[EvidenceRef]) -> list[dict[str, str]]:
    """Serialize a whole evidence list into the frontmatter shape (§12.1) — the
    value a proposal writer assigns to ``frontmatter["evidence"]``."""
    return [ref.to_frontmatter() for ref in refs]


# --- fail-soft evidence resolution (§12.4, ADR-0007 D21) -------------------

RESOLVED = "resolved"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ResolvedEvidence:
    """The outcome of resolving one ``EvidenceRef`` against the store (§12.4).

    ``status`` is ``"resolved"`` or ``"unresolved"``. A ``curated`` ref that has
    been tombstoned/pruned still *resolves* to its ``.tombstones/`` record when
    that survives (``tombstoned=True``); only a target that is genuinely gone
    (hand-deleted raw, fully-pruned fact) is ``"unresolved"``. Evidence is an
    append-only historical record — an unresolved item is reported, **never**
    dropped and **never** raised (D21)."""

    ref: EvidenceRef
    status: str
    path: Path | None = None
    tombstoned: bool = False

    @property
    def resolved(self) -> bool:
        return self.status == RESOLVED


def resolve_evidence(root: Path, ref: EvidenceRef) -> ResolvedEvidence:
    """Resolve one evidence ref to an on-disk path, fail-soft (D21). Never
    raises on a missing target and never on a bad project slug — an
    unresolvable ref simply comes back ``status="unresolved"``."""
    try:
        if ref.kind == "raw":
            file = _require(ref.file)
            if not _is_safe_raw_basename(file):
                return ResolvedEvidence(ref, UNRESOLVED)
            path = store.memory_dir(_require(ref.project), root) / "raw" / file
            return _resolved_if_exists(ref, path)
        if ref.kind == "curated":
            slug = _require(ref.slug)
            if not _valid_slug(slug):
                return ResolvedEvidence(ref, UNRESOLVED)
            mem = store.memory_dir(_require(ref.project), root)
            live = mem / "curated" / f"{slug}.md"
            if live.exists():
                return ResolvedEvidence(ref, RESOLVED, live)
            tomb = mem / ".tombstones" / f"{slug}.md"
            if tomb.exists():
                return ResolvedEvidence(ref, RESOLVED, tomb, tombstoned=True)
            return ResolvedEvidence(ref, UNRESOLVED)
        if ref.kind == "proposal":
            # proposal_path validates the slug and raises on a bad one — caught
            # below and reported unresolved, per D21's fail-soft contract.
            return _resolved_if_exists(ref, proposal_path(root, _require(ref.slug)))
    except (store.InvalidSlugError, ValueError, OSError):
        return ResolvedEvidence(ref, UNRESOLVED)
    return ResolvedEvidence(ref, UNRESOLVED)


def _resolved_if_exists(ref: EvidenceRef, path: Path) -> ResolvedEvidence:
    return ResolvedEvidence(
        ref, RESOLVED if path.exists() else UNRESOLVED, path if path.exists() else None
    )


# --- near-duplicate detection (ADR-0007 D18) -------------------------------


def jaccard_similarity(a: str, b: str) -> float:
    """Normalized token-overlap (Jaccard) similarity between two bodies, over
    lower-cased word tokens — reusing ``core.search``'s exact tokenization
    (ADR-0007 D18). ``|A ∩ B| / |A ∪ B|``; two token-empty bodies score ``0.0``
    (degenerate, never treated as a match)."""
    ta = set(search._tokenize(a))
    tb = set(search._tokenize(b))
    union = ta | tb
    if not union:
        return 0.0
    return len(ta & tb) / len(union)


def is_near_duplicate(a: str, b: str, threshold: float = 0.6) -> bool:
    """True when two bodies' Jaccard similarity meets or exceeds ``threshold``
    (Default ``0.6``, §12.11) — the deterministic near-duplicate test the miner
    prompt-builder (§12.5) and the ranker's suppression check (§12.6) share."""
    return jaccard_similarity(a, b) >= threshold


# --- corpus data model -----------------------------------------------------


@dataclass(frozen=True)
class CuratedFact:
    """One active curated fact (spec §2), carrying its source ``project`` so it
    can name itself as evidence."""

    project: str
    slug: str
    body: str
    provenance: list[str]
    path: Path

    def as_evidence(self) -> EvidenceRef:
        return EvidenceRef.curated(self.project, self.slug)


@dataclass(frozen=True)
class RawCapture:
    """One recent raw session capture (spec §1). ``agent``/``session_id`` are
    the per-file metadata the ranker recomputes breadth from (§12.6), captured
    here so it never has to re-read the file."""

    project: str
    file: str  # basename within the project's raw/ dir
    agent: str
    session_id: str | None
    captured_at: str  # ISO8601, as stored
    body: str
    path: Path

    def as_evidence(self) -> EvidenceRef:
        return EvidenceRef.raw(self.project, self.file)


@dataclass(frozen=True)
class RejectedProposal:
    """A rejected proposal surfaced for near-duplicate suppression (§12.4/D18).
    ``candidate_type`` is carried for the miner's per-type reject summary."""

    slug: str
    candidate_type: str | None
    body: str


@dataclass(frozen=True)
class LedgerSummary:
    """Compact, code-computed ledger digest for the miner prompt (§12.5): per-
    ``candidate_type`` reject counts, plus the rejected proposal bodies the
    near-duplicate check runs against. Empty when the ledger doesn't exist yet
    (it lands in workstream F) or every line is malformed."""

    reject_counts: dict[str, int] = field(default_factory=dict)
    rejected_proposals: list[RejectedProposal] = field(default_factory=list)


@dataclass(frozen=True)
class Corpus:
    """Everything the miner reads, aggregated across every registered project.
    ``skipped_projects`` names any project whose tree was missing/malformed and
    therefore skipped (§12.4) — observability, so a corrupt project surfaces as
    a countable skip rather than a silent gap."""

    curated: list[CuratedFact] = field(default_factory=list)
    raw: list[RawCapture] = field(default_factory=list)
    ledger: LedgerSummary = field(default_factory=LedgerSummary)
    skipped_projects: list[str] = field(default_factory=list)


# --- the loader ------------------------------------------------------------


def load_corpus(
    root: Path,
    *,
    config: RecommendConfig | None = None,
    now: datetime | None = None,
) -> Corpus:
    """Aggregate the read-only corpus across **every** registered project
    (§12.4). ``config`` supplies the raw caps (defaults from ``config.toml``);
    ``now`` is the reference time for the lookback window (defaults to the
    current UTC time) — both injectable so tests drive the caps deterministically.

    Fail-soft: a malformed registry yields an empty corpus, and any single
    project whose tree is missing or malformed is skipped (named in
    ``skipped_projects``), never aborting the whole pass."""
    cfg = config if config is not None else load_config().recommend
    reference = now if now is not None else datetime.now(UTC)

    curated: list[CuratedFact] = []
    raw: list[RawCapture] = []
    skipped: list[str] = []

    for project in sorted(_registry_projects(root)):
        try:
            facts = _load_curated(root, project)
            captures = _load_raw(root, project, cfg, reference)
        except Exception:
            # One corrupt project (bad slug in the registry, an unreadable
            # tree) must not blind the miner to every other project (§12.4).
            skipped.append(project)
            continue
        curated.extend(facts)
        raw.extend(captures)

    return Corpus(
        curated=curated,
        raw=raw,
        ledger=load_ledger_summary(root),
        skipped_projects=skipped,
    )


def _registry_projects(root: Path) -> list[str]:
    """Registry slugs, fail-soft: a malformed/missing registry yields ``[]``
    (matches ``core.search``'s contractually fail-soft registry read)."""
    try:
        return list(projects.load_registry(root))
    except Exception:
        return []


def _load_curated(root: Path, project: str) -> list[CuratedFact]:
    return [
        CuratedFact(
            project=project,
            slug=str(doc.get("name") or doc.file_path.stem),
            body=doc.body,
            provenance=[str(p) for p in (doc.get("provenance") or [])],
            path=doc.file_path,
        )
        for doc in store.list_curated(root, project)
    ]


def _load_raw(root: Path, project: str, cfg: RecommendConfig, now: datetime) -> list[RawCapture]:
    """Recent raw captures for one project, capped by age **and** count,
    whichever yields fewer (ADR-0007 D17): keep only captures within
    ``raw_lookback_days`` of ``now``, then keep at most ``raw_cap_per_project``
    of the most recent. ``list_raw`` already returns oldest-first and skips
    unparseable files."""
    cutoff = now - timedelta(days=cfg.raw_lookback_days)
    captures: list[RawCapture] = []
    for doc in store.list_raw(root, project, unconsumed_only=False):
        captured_raw = doc.get("captured_at")
        when = _parse_dt(captured_raw)
        # A capture with no parseable timestamp can't be aged against the
        # lookback window; drop it rather than let it slip past the age cap.
        if when is None or when < cutoff:
            continue
        captures.append(
            RawCapture(
                project=project,
                file=doc.file_path.name,
                agent=str(doc.get("agent") or ""),
                session_id=(str(doc.get("session_id")) if doc.get("session_id") else None),
                captured_at=str(captured_raw),
                body=doc.body,
                path=doc.file_path,
            )
        )
    # list_raw is oldest-first; the count cap keeps the *most recent* window.
    if len(captures) > cfg.raw_cap_per_project:
        captures = captures[-cfg.raw_cap_per_project :]
    return captures


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- ledger summary (§12.2/§12.4, fail-soft) -------------------------------


def load_ledger_summary(root: Path) -> LedgerSummary:
    """Read ``<root>/recommender/ledger.jsonl`` into a compact digest for the
    miner (§12.4). Fail-soft to an empty summary: the file won't exist until
    workstream F, and a malformed line anywhere in it is skipped, never fatal
    (§12.2, the exact precedent ``curator/engine.py:read_fact_count_trend``
    sets)."""
    path = ledger_path(root)
    if not path.exists():
        return LedgerSummary()

    reject_counts: dict[str, int] = {}
    rejected_types: dict[str, str | None] = {}
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return LedgerSummary()

    for line in raw_lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("event") != "rejected":
            continue
        slug = event.get("slug")
        if not isinstance(slug, str):
            continue
        ctype = event.get("candidate_type")
        ctype = ctype if isinstance(ctype, str) else None
        rejected_types[slug] = ctype
        if ctype is not None:
            reject_counts[ctype] = reject_counts.get(ctype, 0) + 1

    rejected_proposals = _rejected_bodies(root, rejected_types)
    return LedgerSummary(reject_counts=reject_counts, rejected_proposals=rejected_proposals)


def _rejected_bodies(root: Path, rejected_types: dict[str, str | None]) -> list[RejectedProposal]:
    """Load the body of each still-``rejected`` proposal named in the ledger,
    for the near-duplicate check. A proposal file that is missing, malformed, or
    no longer ``rejected`` is simply omitted — fail-soft, never fatal."""
    out: list[RejectedProposal] = []
    for slug in sorted(rejected_types):
        # A ledger line's slug is untrusted input (the file accretes across many
        # CLI runs) — an invalid one must not make proposal_path raise out of
        # this fail-soft reader, so skip it rather than build an unsafe path.
        if not _valid_slug(slug):
            continue
        path = proposal_path(root, slug)
        if not path.exists():
            continue
        try:
            doc = store.read_doc(path)
        except (ValueError, OSError):
            continue
        if doc.get("status") != "rejected":
            continue
        out.append(
            RejectedProposal(
                slug=slug,
                candidate_type=rejected_types[slug],
                body=doc.body,
            )
        )
    return out
