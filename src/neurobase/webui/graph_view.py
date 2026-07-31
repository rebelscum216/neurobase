"""The graph home surface's data composition (app-shell plan, Phase G).

``core/graph.py`` (provenance Slice A) is deliberately **identity-only**: it
answers *which* sessions fed *which* facts, and which facts superseded which,
with no display concerns. This module is the other half — the presentation
layer that turns that identity graph into something a canvas can draw:

* it adds the node kinds Slice A does not model — **proposals/skills** — by
  walking ``proposals.load_all_proposals`` and resolving each one's evidence
  with the same ``EvidenceRef`` / ``resolve_evidence`` pair the Suggestions
  detail page uses (``routes._evidence_rows``);
* it derives **display** fields (session title, prompts-kept count, fact body
  snippet) that Slice A has no business knowing about.

Everything here is read-only and fail-soft: this renders the app's *home*
page, so one unreadable project, one malformed proposal, or one absent raw
file must degrade a node's label — never 500 the surface. The per-request
re-read posture from Phase 1 holds; there is no cache to invalidate.

Node ids are namespaced by kind (``s:``/``f:``/``p:``) and, for the two
project-scoped kinds, by project — two projects may each hold a fact named
``ci-gate``, and they are different nodes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from neurobase.core import graph as core_graph
from neurobase.core import redact, store
from neurobase.core.config import load_config
from neurobase.core.store_handle import StoreHandle
from neurobase.recommender import corpus as recommend_corpus
from neurobase.recommender import proposals

# Display truncation limits. Titles sit under a canvas node; snippets sit in the
# inspector, which scrolls — so the snippet is allowed to be a real sentence.
_TITLE_MAX = 72
_SNIPPET_MAX = 240

# Decided-and-dead: `/suggestions` hides these from its default view and
# `recommend edit` refuses them, so the graph does not draw them either.
_DEAD_PROPOSAL_STATUSES = proposals.EDIT_BLOCKED_STATUSES


def _redact_patterns() -> tuple[str, ...]:
    """The configured extra redaction patterns, read **once per request**.

    ``load_config()`` re-reads and re-parses the TOML on every call, and this
    module redacts once per node — reading the config a couple of thousand times
    to render one page.
    """
    return tuple(load_config().redact.extra_patterns)


def _redact_display(text: str, patterns: tuple[str, ...]) -> str:
    """Mirrors ``routes._redact_display`` — re-scrub at display time because a
    legacy or hand-edited capture may carry secrets the scribe never saw (§14
    defense). Duplicated rather than imported: ``routes`` imports *this* module,
    and a shared helper is not worth a new module or an import cycle."""
    return redact.redact(text, list(patterns))


def _truncate(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _session_display(body: str, patterns: tuple[str, ...]) -> tuple[str | None, int | None]:
    """Best-effort ``(title, prompts_kept)`` from a raw capture body, per the
    plan's 2b rule.

    The title is the first line of the first ``- `` bullet under ``## Prompts``.
    Prompts are multi-line and carry embedded headings of their own (a pasted
    skill body, a ``/command`` dump), so this must **not** assume section
    discipline: it takes the first bullet's first line and stops, rather than
    trying to parse the section as a whole. Both values are display sugar — the
    caller falls back to the filename, which is the real identity.

    Structure is read at **column zero only**. The scribe indents a prompt's
    continuation lines by two spaces, so a heading or bullet pasted inside a
    prompt arrives indented and must not be mistaken for the document's own
    structure. Stripping first would erase exactly that distinction and let
    captured text forge both values — and a legacy or hand-edited capture, the
    case display-redaction exists for, offers no protection at all.
    """
    title: str | None = None
    prompts: int | None = None
    in_prompts = False
    for line in body.splitlines():
        if line[:1].isspace():
            continue  # inside a prompt's continuation — never structure
        if line.startswith("## "):
            in_prompts = line[3:].strip().lower() == "prompts"
            continue
        if not in_prompts and line.startswith("- prompts captured:"):
            value = line.split(":", 1)[1].strip()
            if value.isdigit():
                prompts = int(value)
            continue
        if in_prompts and title is None and line.startswith("- "):
            candidate = line[2:].strip()
            if candidate:
                title = _truncate(_redact_display(candidate, patterns), _TITLE_MAX)
    return title, prompts


def _raw_body(handle: StoreHandle, project: str, name: str) -> str | None:
    """One raw capture's body, read by name — **not** via a bulk ``list_raw``.

    Titles are only ever needed for the handful of sessions that survive the
    connectivity filter, and a store holds thousands of captures; listing the
    whole project to label a few dozen nodes costs about a second per request.

    ``name`` is **not** trusted. It reaches here from a curated fact's
    ``provenance:`` list, which is hand-editable and is carried verbatim through
    ``core/graph.py`` (a name that fails the raw-filename grammar still becomes a
    skeleton session's id). So this applies the same two-part guard as
    ``routes._capture_path``: the resolved file's parent must be *exactly* the
    project's ``raw/`` — which is what refuses a ``..``-bearing name — and it
    must still be inside the store, which is what refuses a symlinked ``raw/``.
    ``handle.contains`` alone only enforces the second: ``raw/../curated/x.md``
    stays inside the store and would otherwise be read out as a transcript.
    """
    try:
        raw_dir = (handle.memory_dir(project) / "raw").resolve()
        path = (raw_dir / name).resolve()
    except (store.InvalidSlugError, OSError, ValueError):
        return None
    if path.parent != raw_dir or not handle.contains(path) or not path.is_file():
        return None
    if not handle.contains(raw_dir):
        return None
    try:
        return store.read_doc(path).body
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _fact_snippets(handle: StoreHandle, project: str, patterns: tuple[str, ...]) -> dict[str, str]:
    """Curated fact bodies in ``project``, keyed by slug, truncated + redacted.

    Entries that resolve outside the store are skipped, matching every peer read
    (``routes._memory_facts``, ``routes._session_rows`` — Codex
    P2-SAFETY-SECURITY-003): a symlinked ``curated/`` must not surface an
    external file's body, and this surface embeds that body in the page.

    Keyed by the fact's frontmatter ``name`` when it has one, because that is
    what ``FactNode.slug`` is — keying on the filename stem alone silently loses
    the snippet for any fact whose name and filename differ.
    """
    snippets: dict[str, str] = {}
    try:
        for doc in handle.list_curated(project, active_only=False):
            if not handle.contains(doc.file_path):
                continue
            slug = str(doc.get("name") or doc.file_path.stem)
            snippets[slug] = _truncate(_redact_display(doc.body, patterns), _SNIPPET_MAX)
    except (OSError, ValueError):
        return {}
    return snippets


def _session_node_id(project: str, session: str) -> str:
    return f"s:{project}:{session}"


def _fact_node_id(project: str, slug: str) -> str:
    return f"f:{project}:{slug}"


def _proposal_node_id(slug: str) -> str:
    return f"p:{slug}"


def _agent_label(agent: str | None) -> str:
    if agent == "claude":
        return "Claude"
    if agent == "codex":
        return "Codex"
    return agent or "unknown agent"


def _session_nodes(
    handle: StoreHandle, sessions: list[core_graph.SessionNode], patterns: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Render sessions to display nodes. Callers pass only the sessions that will
    actually be drawn — each one costs a file read for its title.

    ``file`` is the capture that was actually readable, or ``None``. A session
    can be recovered from the journal or from a fact's provenance with its raw
    file long gone (``core/graph.py``'s rungs 2 and 3), and linking such a node
    to ``/sessions/...`` guarantees a 404 — so the link is only offered when a
    file was really read. ``resolved`` cannot stand in for that: a
    journal-recovered session carries ``resolved=True`` with no file on disk.
    """
    nodes: list[dict[str, Any]] = []
    for session in sessions:
        title: str | None = None
        prompts: int | None = None
        readable: str | None = None
        for name in session.files:
            body = _raw_body(handle, session.project, name)
            if body is None:
                continue
            if readable is None:
                readable = name
            found_title, found_prompts = _session_display(body, patterns)
            # Never let a later capture erase what an earlier one supplied.
            if title is None:
                title = found_title
            if prompts is None:
                prompts = found_prompts
            if title is not None:
                break
        label = title or (session.files[0] if session.files else session.id)
        sub_parts = [_agent_label(session.agent), session.project]
        if prompts is not None:
            sub_parts.append(f"{prompts} prompts kept")
        nodes.append(
            {
                "id": _session_node_id(session.project, session.id),
                "kind": "session",
                "label": label,
                "sub": " · ".join(sub_parts),
                "project": session.project,
                # None when the filename never parsed — the UI must say "unknown",
                # not paint it as Claude. This is a provenance tool.
                "agent": session.agent,
                "captured_at": session.captured_at,
                "prompts": prompts,
                "resolved": session.resolved,
                "file": readable,
            }
        )
    return nodes


def _fact_nodes(
    handle: StoreHandle, facts: tuple[core_graph.FactNode, ...], patterns: tuple[str, ...]
) -> list[dict[str, Any]]:
    snippets: dict[str, dict[str, str]] = {}
    nodes: list[dict[str, Any]] = []
    for fact in facts:
        if fact.project not in snippets:
            snippets[fact.project] = _fact_snippets(handle, fact.project, patterns)
        sub = f"{fact.project} · {fact.status} fact"
        if fact.pinned:
            sub += " · pinned"
        nodes.append(
            {
                "id": _fact_node_id(fact.project, fact.slug),
                "kind": "fact",
                "label": fact.slug,
                "sub": sub,
                "project": fact.project,
                "status": fact.status,
                "pinned": fact.pinned,
                "tombstoned": fact.tombstoned,
                "updated_at": fact.updated_at,
                "snippet": snippets[fact.project].get(fact.slug, ""),
                # A ghost is referenced by an edge but present in neither
                # `curated/` nor `.tombstones/` — `/memory/...` reads `curated/`
                # only, so linking one guarantees a 404.
                "href": (None if fact.status == "ghost" else f"/memory/{fact.project}/{fact.slug}"),
            }
        )
    return nodes


def _proposal_nodes_and_edges(
    root: Path,
    known: set[str],
    raw_index: dict[tuple[str, str], str],
    patterns: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Proposal/skill nodes plus their three evidence-kind edges.

    Evidence kinds map onto the graph exactly as the plan specifies:
    ``curated`` → fact→proposal, ``raw`` → **session→proposal**, ``proposal`` →
    proposal→proposal. The raw case matters: an accepted skill whose evidence is
    only ``raw`` would otherwise float disconnected from everything.

    ``raw`` evidence names a *filename*, but a session's node id is its
    ``session_id`` whenever one resolved — and ``parse_raw_filename`` cannot
    recover that (it returns ``(captured_at, agent)``; the ``sid8`` field is
    deliberately unrecoverable). So the mapping is looked up in ``raw_index``,
    built from the ``files`` each ``SessionNode`` already carries, rather than
    re-derived from the name.

    An edge is only emitted when its other endpoint is a node that actually
    exists in this graph — evidence can name a pruned fact or a capture from a
    project no longer in the registry, and a dangling edge would draw a line to
    nowhere. Nodes are therefore built in a **first pass** and their ids folded
    into the known set before any edge is resolved: resolving edges against a set
    that predates the proposal nodes makes the ``proposal`` → proposal→proposal
    branch structurally unreachable, which is exactly what it was.

    ``rejected`` and ``superseded`` proposals are left out entirely. They are
    decided and dead — ``/suggestions`` hides them from its default view and
    ``recommend edit`` refuses them — and on a canvas they are indistinguishable
    from an installed skill, so drawing them presents settled rejections as live
    lineage.
    """
    try:
        docs = list(proposals.load_all_proposals(root))
    except (OSError, ValueError):
        return [], []

    live = []
    for doc in docs:
        status = str(doc.get("status") or "proposed")
        if status in _DEAD_PROPOSAL_STATUSES:
            continue
        live.append((doc, status))

    nodes: list[dict[str, Any]] = []
    for doc, status in live:
        slug = str(doc.get("name") or doc.file_path.stem)
        nodes.append(
            {
                "id": _proposal_node_id(slug),
                "kind": "proposal",
                "label": slug,
                "sub": f"{doc.get('candidate_type') or 'proposal'} · {status}",
                "project": doc.get("project"),
                "status": status,
                "snippet": _truncate(
                    _redact_display(str(doc.get("rationale") or doc.body), patterns),
                    _SNIPPET_MAX,
                ),
                "href": f"/suggestions/{slug}",
            }
        )

    reachable = known | {node["id"] for node in nodes}
    edges: list[dict[str, Any]] = []
    for doc, _status in live:
        node_id = _proposal_node_id(str(doc.get("name") or doc.file_path.stem))
        for item in doc.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            try:
                ref = recommend_corpus.EvidenceRef.from_frontmatter(item)
            except (KeyError, ValueError):
                continue
            other: str | None = None
            if ref.kind == "curated" and ref.project and ref.slug:
                other = _fact_node_id(ref.project, ref.slug)
            elif ref.kind == "raw" and ref.project and ref.file:
                other = raw_index.get((ref.project, ref.file))
            elif ref.kind == "proposal" and ref.slug:
                other = _proposal_node_id(ref.slug)
            if other is None or other not in reachable or other == node_id:
                continue
            edges.append({"a": other, "b": node_id, "kind": ref.kind})
    return nodes, edges


def graph_payload(
    handle: StoreHandle, root: Path, *, include_orphan_sessions: bool = False
) -> dict[str, Any]:
    """The whole home surface as one JSON-serializable dict.

    Composed in the presentation layer, not in ``core``: ``memory_graph`` for
    session/fact identity, then proposals layered on top.

    **Disconnected sessions are dropped by default.** The plan's scale posture
    ("~90 raws … ~44 KiB") was measured before attribution existed; a real store
    now holds ~2000 captures of which only a few dozen carry any session→fact
    edge, because journal-based attribution only arrived with provenance Slice A
    and every capture folded before that has no attribution record. Rendering
    them all is ~900 KiB of JSON drawn as a cloud of disconnected dots around
    the part that actually carries meaning. The count of what was dropped is
    returned in ``counts.hidden_sessions`` and surfaced in the UI — a cap that
    isn't stated is indistinguishable from "this is everything".

    Facts are **never** dropped for being unattributed: there are few of them,
    they are the curated memory this surface exists to show, and the plan calls
    for unattributed facts to render as orphans.
    """
    # ``include_tombstoned=True`` is required for supersession to be visible at
    # all: the curator writes ``supersedes:`` and tombstones the old fact in the
    # *same* fold, and ``core/graph.py`` drops an edge whose endpoint is excluded
    # by the toggle. Without it the green supersedes edge appears only for
    # ancient history that has already been pruned to a ghost, and never for a
    # fresh supersession — the exact case worth seeing.
    memory = core_graph.memory_graph(handle, include_tombstoned=True)

    # --- identity first, display later -------------------------------------
    # Node *ids* are free (they come straight off the graph); node *labels* cost
    # a file read each. So resolve the whole edge set against ids, decide which
    # sessions survive, and only then pay for the titles of the survivors.
    session_ids = {
        _session_node_id(session.project, session.id): session for session in memory.sessions
    }
    patterns = _redact_patterns()
    fact_nodes = _fact_nodes(handle, memory.facts, patterns)
    known = set(session_ids) | {node["id"] for node in fact_nodes}

    # (project, raw filename) → session node id, so `raw` evidence can find the
    # session it belongs to even when that session resolved to a session_id.
    raw_index = {
        (session.project, name): _session_node_id(session.project, session.id)
        for session in memory.sessions
        for name in session.files
    }

    # ``source`` rides along because it is the edge's *trust* signal, not
    # bookkeeping: ``journal`` means the curator recorded the attribution at fold
    # time (per-pass validated), ``frontmatter`` means the fact itself claims it.
    # The inspector states which, so a reader can tell a verified lineage from a
    # claimed one.
    edges: list[dict[str, Any]] = []
    for edge in memory.session_edges:
        a = _session_node_id(edge.project, edge.session)
        b = _fact_node_id(edge.project, edge.fact)
        if a in known and b in known:
            edges.append({"a": a, "b": b, "kind": "session", "source": edge.source})
    for fact_edge in memory.fact_edges:
        a = _fact_node_id(fact_edge.project, fact_edge.supersedes)
        b = _fact_node_id(fact_edge.project, fact_edge.superseded_by)
        if a in known and b in known:
            edges.append({"a": a, "b": b, "kind": "supersedes", "source": fact_edge.source})

    proposal_nodes, proposal_edges = _proposal_nodes_and_edges(root, known, raw_index, patterns)
    edges.extend(proposal_edges)

    # Decided *after* every edge exists: a session can be connected solely by a
    # proposal's `raw` evidence, which is the last thing composed above.
    linked: set[str] = set()
    for drawn_edge in edges:
        linked.add(str(drawn_edge["a"]))
        linked.add(str(drawn_edge["b"]))
    if include_orphan_sessions:
        drawn = list(session_ids.values())
        hidden = []
    else:
        drawn = [s for node_id, s in session_ids.items() if node_id in linked]
        hidden = [s for node_id, s in session_ids.items() if node_id not in linked]

    nodes: list[dict[str, Any]] = []
    nodes.extend(_session_nodes(handle, drawn, patterns))
    nodes.extend(fact_nodes)
    nodes.extend(proposal_nodes)

    # Sessions and captures are **not** the same unit: ``_sessions_from_raws``
    # collapses every raw file sharing a session id into one node, so a store of
    # 2000 captures can be a few hundred sessions. The disclosure line is worded
    # in captures and sits beside a rail badge that counts captures, so it counts
    # captures too — a disclosure that under-reports by the capture-per-session
    # factor is worse than none.
    counts = {
        "sessions": sum(1 for n in nodes if n["kind"] == "session"),
        "facts": sum(1 for n in nodes if n["kind"] == "fact"),
        "proposals": sum(1 for n in nodes if n["kind"] == "proposal"),
        "edges": len(edges),
        "hidden_sessions": len(hidden),
        "hidden_captures": sum(len(s.files) for s in hidden),
        "shown_captures": sum(len(s.files) for s in drawn),
    }
    projects = sorted({str(n["project"]) for n in nodes if n.get("project")})
    return {"nodes": nodes, "edges": edges, "counts": counts, "projects": projects}


__all__ = ["graph_payload"]
