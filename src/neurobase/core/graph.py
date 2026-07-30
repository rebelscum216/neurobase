"""Session→fact provenance graph (provenance plan, Slice A): a deterministic,
LLM-free, **read-only** view of "which conversations fed which memory".

Nothing here writes, and nothing here infers. Every edge is already recorded
somewhere on disk — a curated fact's ``provenance``/``supersedes`` frontmatter
(spec §1) or the curator's per-pass ``fold`` journal record (spec §2, ADR-0022) —
and this module only joins those two records into nodes and edges.

**The degradation ladder.** Provenance targets outlive neither raw retention nor
tombstone pruning, so a naive join loses edges exactly where the history is
oldest. Following the ranker's D21 discipline, this reader **degrades to coarse,
never fabricates, and never drops**:

1. A ``raw/<file>`` provenance entry whose raw file is on disk → session identity
   from its frontmatter → a **resolved** edge.
2. Raw file gone, but the fold journal recorded that file's identity at
   consumption time → still a **resolved** edge, sourced from the journal.
3. Neither → parse the filename grammar (``{ts}_{agent}_{sid8}.md``, the inverse
   of ``store.raw_filename``) into a **skeleton** session node: the edge is kept
   and marked ``resolved=False``. Note ``sid8`` is a *truncated, stripped* digest
   of the session id, not the id — a skeleton node therefore reports
   ``session_id=None`` rather than pretending the fragment is an id.
4. A ``supersedes`` (or journal ``fold.superseded``) target that exists in neither
   ``curated/`` nor ``.tombstones/`` renders as a **ghost** fact node, same ladder.

**What is deliberately NOT rendered: run-granular edges.** The journal records
which sessions a pass consumed *and* which facts it touched; joining those two
would sprout 90 edges off one unattributed fact in a 90-raw backlog pass. Only
``fold.edges`` — the validated per-fact attribution — produces journal edges. An
unattributed fact renders as an honest orphan.

**Sentinel provenance produces no session edge.** ``user-directed`` and ``seed:*``
are not sessions (matching the ranker's ``startswith("raw/")`` filter);
``user-directed`` membership instead marks the fact ``pinned``.

Two deviations from the 2026-07-16 plan, both forced by what landed since:

- ``memory_graph`` takes a **``StoreHandle``**, not a raw ``root: Path``. ADR-0015
  made the handle the store chokepoint and ``scripts/check_store_chokepoint.py``
  fails the gate on a raw-root accessor outside the three implementation modules —
  ``core/graph.py`` is not one of them, and should not be: it is a *reader*, and a
  reader that skipped the schema guard is exactly what the chokepoint exists to
  prevent.
- ``SessionNode`` carries ``files`` (a tuple), not a single ``file``. A session
  usually has several captures; collapsing them to one node per *session* is the
  question the graph answers, and one-file-per-node would have rendered the same
  conversation as N disconnected nodes.

Layer note: this is legal in ``core/`` on the ``core/search.py`` precedent — it
reads markdown, frontmatter, and the filesystem, nothing more. It **must not**
import ``recommender/`` (an upward import, ``docs/architecture.md``). The
fact→proposal half of the app's graph is composed in the webui route layer, which
may legally import both core and the mid tier.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from neurobase.core import store
from neurobase.core.store_handle import StoreHandle

RAW_PREFIX = "raw/"
PINNED_SENTINEL = "user-directed"

# Inverse of ``store.raw_filename``: ``{ts}_{agent}_{sid8}.md`` with an optional
# ``__gNNNN`` contention suffix. Split left-first for the timestamp (which never
# contains ``_``) and right-first for ``sid8``, so an agent name containing an
# underscore cannot shift the fields.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:\.\d+)?Z$")


@dataclass(frozen=True)
class SessionNode:
    """One conversation. ``id`` is the session id when known, else the raw
    filename — the graph key, stable within a project. ``resolved`` is False for
    a skeleton recovered from a filename alone."""

    id: str
    project: str
    files: tuple[str, ...]
    session_id: str | None
    agent: str | None
    captured_at: str | None
    resolved: bool


@dataclass(frozen=True)
class FactNode:
    """One curated fact. ``status`` is ``active``, ``tombstoned``, or ``ghost``
    (referenced by an edge but present in neither ``curated/`` nor
    ``.tombstones/`` — pruned, or superseded more than one generation back)."""

    slug: str
    project: str
    status: str
    pinned: bool
    updated_at: str | None
    tombstoned: bool
    resolved: bool


@dataclass(frozen=True)
class SessionFactEdge:
    """A session fed a fact. ``source`` is ``journal`` (per-pass validated
    attribution) or ``frontmatter``; the journal wins when both record it."""

    session: str
    fact: str
    project: str
    resolved: bool
    source: str


@dataclass(frozen=True)
class FactFactEdge:
    """``supersedes`` was folded into ``superseded_by``. Journal-sourced pairs
    outlive both tombstone pruning and multi-generation supersession, which
    overwrites the ``supersedes`` frontmatter key."""

    supersedes: str
    superseded_by: str
    project: str
    resolved: bool
    source: str


@dataclass(frozen=True)
class MemoryGraph:
    sessions: tuple[SessionNode, ...]
    facts: tuple[FactNode, ...]
    session_edges: tuple[SessionFactEdge, ...]
    fact_edges: tuple[FactFactEdge, ...]


@dataclass
class _Raw:
    """Accumulated identity for one raw capture, from disk and/or the journal."""

    file: str
    session_id: str | None
    agent: str | None
    captured_at: str | None
    resolved: bool


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_raw_filename(name: str) -> tuple[str | None, str | None]:
    """``(captured_at, agent)`` recovered from a raw filename, either ``None`` if
    the name does not match the grammar. The ``sid8`` field is deliberately not
    returned: it is a truncated, non-alphanumeric-stripped prefix of the session
    id (``store._sid8``), so it cannot be widened back into one."""
    stem = store.canonical_raw_name(name)
    if not stem.endswith(".md"):
        return None, None
    stem = stem[: -len(".md")]
    ts, sep, rest = stem.partition("_")
    if not sep or not _TS_RE.match(ts):
        return None, None
    agent, sep, _sid8 = rest.rpartition("_")
    if not sep or not agent:
        return None, None
    # ``raw_filename`` writes ``%H-%M-%S`` because ``:`` is not filename-safe;
    # restore the ISO shape rather than inventing a second timestamp format.
    date, _, clock = ts.partition("T")
    return f"{date}T{clock.replace('-', ':')}", agent


def _read_fold_records(handle: StoreHandle, project: str) -> list[dict[str, Any]]:
    """Every ``fold`` object in the project's curator journal, oldest pass first.

    Fail-soft at every level: a missing journal, an unreadable one, a truncated
    or malformed line, or a line whose ``fold`` is not an object are all skipped
    rather than raised. The journal is an append-only audit trail written outside
    any transaction (spec §2 states it **may have gaps**), so a reader that
    raised on one bad line would lose every good one after it."""
    path = handle.memory_dir(project) / store.CURATOR_LOG
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    folds = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        fold = record.get("fold") if isinstance(record, dict) else None
        if isinstance(fold, dict):
            folds.append(fold)
    return folds


def _collect_raws(
    handle: StoreHandle, project: str, folds: list[dict[str, Any]]
) -> dict[str, _Raw]:
    """Raw captures known to this project, keyed by filename.

    Disk first (the primary record), then the journal's ``consumed`` entries fill
    in captures whose raw file is gone. The journal only *fills gaps* — it never
    overwrites identity read from a live file, which is the fresher of the two if
    they ever disagree. (Journal precedence applies to edge *attribution*, below,
    where the journal is the validated record and frontmatter is the backstop.)"""
    raws: dict[str, _Raw] = {}
    for doc in handle.list_raw(project, unconsumed_only=False):
        raws[doc.file_path.name] = _Raw(
            file=doc.file_path.name,
            session_id=_str_or_none(doc.get("session_id")),
            agent=_str_or_none(doc.get("agent")),
            captured_at=_str_or_none(doc.get("captured_at")),
            resolved=True,
        )
    for fold in folds:
        for entry in fold.get("consumed") or ():
            if not isinstance(entry, dict):
                continue
            name = _str_or_none(entry.get("file"))
            if not name or name in raws:
                continue
            raws[name] = _Raw(
                file=name,
                session_id=_str_or_none(entry.get("session_id")),
                agent=_str_or_none(entry.get("agent")),
                captured_at=_str_or_none(entry.get("captured_at")),
                resolved=True,
            )
    return raws


def _skeleton(name: str) -> _Raw:
    """Rung 3 of the ladder: a capture known only by the name a fact cites."""
    captured_at, agent = parse_raw_filename(name)
    return _Raw(file=name, session_id=None, agent=agent, captured_at=captured_at, resolved=False)


def _session_key(raw: _Raw) -> str:
    return raw.session_id or raw.file


def _fact_from_doc(doc: store.Document, project: str, *, tombstoned: bool) -> FactNode:
    provenance = [str(p) for p in (doc.get("provenance") or ())]
    return FactNode(
        slug=str(doc.get("name") or doc.file_path.stem),
        project=project,
        status="tombstoned" if tombstoned else "active",
        pinned=PINNED_SENTINEL in provenance,
        updated_at=_str_or_none(doc.get("updated_at")),
        tombstoned=tombstoned,
        resolved=True,
    )


def _project_graph(
    handle: StoreHandle,
    project: str,
    *,
    include_tombstoned: bool,
    raws: dict[str, _Raw],
    facts: dict[tuple[str, str], FactNode],
    session_edges: dict[tuple[str, str, str], SessionFactEdge],
    fact_edges: dict[tuple[str, str, str], FactFactEdge],
) -> None:
    """Accumulate one project's nodes and edges into the shared collections."""
    folds = _read_fold_records(handle, project)
    raws.update(_collect_raws(handle, project, folds))

    active_docs = handle.list_curated(project)
    tombstoned_docs = handle.list_tombstoned(project)
    # Every slug the store knows about, whether or not the caller asked to see
    # tombstones. A reference to a slug that is *known but excluded* yields no
    # node and no edge (the caller excluded it); only a slug that exists nowhere
    # becomes a ghost. Without this split, ``include_tombstoned=False`` would
    # silently promote every tombstoned fact into a ghost.
    known = {str(d.get("name") or d.file_path.stem) for d in (*active_docs, *tombstoned_docs)}

    included_docs: list[tuple[store.Document, bool]] = [(d, False) for d in active_docs]
    if include_tombstoned:
        included_docs += [(d, True) for d in tombstoned_docs]
    for doc, is_tombstoned in included_docs:
        node = _fact_from_doc(doc, project, tombstoned=is_tombstoned)
        facts[project, node.slug] = node
    included = {str(d.get("name") or d.file_path.stem) for d, _ in included_docs}

    def ghost(slug: str) -> None:
        facts.setdefault(
            (project, slug),
            FactNode(
                slug=slug,
                project=project,
                status="ghost",
                pinned=False,
                updated_at=None,
                tombstoned=False,
                resolved=False,
            ),
        )

    def add_session_edge(slug: str, raw_name: str, source: str) -> None:
        raw = raws.get(raw_name)
        if raw is None:
            raw = _skeleton(raw_name)
            raws[raw_name] = raw
        key = (project, _session_key(raw), slug)
        existing = session_edges.get(key)
        # Journal attribution is the validated record (spec §2 step 4); it wins
        # over the frontmatter backstop when a pass recorded both.
        if existing is not None and not (existing.source == "frontmatter" and source == "journal"):
            return
        session_edges[key] = SessionFactEdge(
            session=key[1], fact=slug, project=project, resolved=raw.resolved, source=source
        )

    def add_fact_edge(old: str, new: str, source: str) -> None:
        if new not in included:
            return
        if old in known and old not in included:
            return  # known but excluded by the tombstone toggle
        if old not in known:
            ghost(old)
        key = (project, old, new)
        existing = fact_edges.get(key)
        if existing is not None and not (existing.source == "frontmatter" and source == "journal"):
            return
        fact_edges[key] = FactFactEdge(
            supersedes=old,
            superseded_by=new,
            project=project,
            resolved=old in known,
            source=source,
        )

    for doc, _is_tombstoned in included_docs:
        slug = str(doc.get("name") or doc.file_path.stem)
        for entry in doc.get("provenance") or ():
            entry = str(entry)
            # Sentinels (``user-directed``, ``seed:*``) are not sessions.
            if entry.startswith(RAW_PREFIX):
                add_session_edge(slug, entry[len(RAW_PREFIX) :], "frontmatter")
        for old in doc.get("supersedes") or ():
            add_fact_edge(str(old), slug, "frontmatter")

    for fold in folds:
        edges = fold.get("edges")
        if isinstance(edges, dict):
            for slug, names in edges.items():
                if str(slug) not in included or not isinstance(names, list):
                    continue
                for name in names:
                    if isinstance(name, str) and name:
                        add_session_edge(str(slug), name, "journal")
        for pair in fold.get("superseded") or ():
            if not isinstance(pair, dict):
                continue
            old, new = _str_or_none(pair.get("slug")), _str_or_none(pair.get("by"))
            if old and new:
                add_fact_edge(old, new, "journal")


def _sessions_from_raws(project: str, raws: dict[str, _Raw]) -> list[SessionNode]:
    """Collapse captures into one node per session. A session's ``captured_at``
    is its earliest capture, and it is ``resolved`` if *any* of its captures
    resolved — a session known from one live raw is not made unknown by a second
    capture whose file has since been pruned."""
    grouped: dict[str, list[_Raw]] = {}
    for raw in raws.values():
        grouped.setdefault(_session_key(raw), []).append(raw)
    nodes = []
    for key, group in grouped.items():
        ordered = sorted(group, key=lambda r: r.file)
        stamps = sorted(r.captured_at for r in ordered if r.captured_at)
        agents = sorted({r.agent for r in ordered if r.agent})
        nodes.append(
            SessionNode(
                id=key,
                project=project,
                files=tuple(r.file for r in ordered),
                session_id=next((r.session_id for r in ordered if r.session_id), None),
                agent=agents[0] if agents else None,
                captured_at=stamps[0] if stamps else None,
                resolved=any(r.resolved for r in ordered),
            )
        )
    return nodes


def memory_graph(
    handle: StoreHandle,
    project: str | None = None,
    *,
    include_tombstoned: bool = False,
) -> MemoryGraph:
    """The session→fact graph for one project, or every registered project.

    Deterministic, LLM-free, fail-soft, and read-only — one frontmatter parse per
    file per call (the webui's existing per-request re-read posture; no cache to
    invalidate). Ordering is total and stable so callers can diff two graphs.

    Fail-soft is scoped to the *store*, not to the caller. In the all-projects
    sweep a registry slug that is corrupt or whose tree is unreadable contributes
    nothing rather than taking down every other project's graph (the
    ``memory_list_projects`` posture). An **explicitly named** project that is not
    a valid slug still raises :class:`store.InvalidSlugError` — swallowing that
    would turn a caller's typo into a silently empty graph, which is
    indistinguishable from a project that genuinely has no memory yet.
    """
    sweeping = project is None
    targets = sorted(handle.load_registry()) if sweeping else [str(project)]

    sessions: list[SessionNode] = []
    facts: dict[tuple[str, str], FactNode] = {}
    session_edges: dict[tuple[str, str, str], SessionFactEdge] = {}
    fact_edges: dict[tuple[str, str, str], FactFactEdge] = {}

    for target in targets:
        raws: dict[str, _Raw] = {}
        try:
            _project_graph(
                handle,
                target,
                include_tombstoned=include_tombstoned,
                raws=raws,
                facts=facts,
                session_edges=session_edges,
                fact_edges=fact_edges,
            )
        except store.InvalidSlugError:
            if not sweeping:
                raise
            continue
        except OSError:
            continue
        sessions.extend(_sessions_from_raws(target, raws))

    return MemoryGraph(
        sessions=tuple(sorted(sessions, key=lambda s: (s.project, s.captured_at or "", s.id))),
        facts=tuple(sorted(facts.values(), key=lambda f: (f.project, f.slug))),
        session_edges=tuple(
            sorted(session_edges.values(), key=lambda e: (e.project, e.session, e.fact))
        ),
        fact_edges=tuple(
            sorted(fact_edges.values(), key=lambda e: (e.project, e.supersedes, e.superseded_by))
        ),
    )


__all__ = [
    "FactFactEdge",
    "FactNode",
    "MemoryGraph",
    "SessionFactEdge",
    "SessionNode",
    "memory_graph",
    "parse_raw_filename",
]
