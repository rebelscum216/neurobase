"""Grep + term-frequency search over the store (curated facts + status nodes).

Powers the MCP ``memory_search`` tool (build-plan Phase 7, decision D-a: simple
grep + term-frequency scoring in v1; a BM25/FTS index is backlog). Pure,
offline, deterministic — no LLM, no network. Reusable by any caller that needs
to look memory up by keyword.

Scoping (decision D-c): an explicit ``project`` searches only that project; when
omitted, every project in the registry is searched (the server has no session
``cwd`` to trust). Fail-soft throughout — a bad slug or unreadable tree yields
no hits rather than raising.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from neurobase.core import projects, store

_WORD_RE = re.compile(r"[a-z0-9]+")
_NAME_WEIGHT = 3  # a query term in the slug/name counts more than in the body
_SNIPPET_CHARS = 200
_DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class SearchHit:
    """One ranked match. ``kind`` is ``"curated"`` or ``"node"``."""

    project: str
    name: str
    kind: str
    score: int
    snippet: str


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _score(terms: list[str], name: str, body: str) -> int:
    """Term frequency: each query term counts once per whole-word occurrence,
    weighted higher when it lands in the slug/name."""
    name_tokens = _tokenize(name)
    body_tokens = _tokenize(body)
    total = 0
    for term in terms:
        total += _NAME_WEIGHT * name_tokens.count(term)
        total += body_tokens.count(term)
    return total


def _snippet(terms: list[str], body: str) -> str:
    """First non-empty line containing a query term, else the first line;
    truncated. Purely for display — never affects ranking."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for line in lines:
        low = line.lower()
        if any(term in low for term in terms):
            return line[:_SNIPPET_CHARS]
    return lines[0][:_SNIPPET_CHARS] if lines else ""


def _all_projects(root: Path) -> list[str]:
    """Registry project slugs, fail-soft: a malformed registry yields ``[]``
    (search is contractually fail-soft — a corrupt file must not raise)."""
    try:
        return list(projects.load_registry(root))
    except Exception:
        return []


def _candidates(root: Path, project: str) -> Iterator[tuple[str, str, str]]:
    """Yield ``(name, kind, body)`` for a project's curated facts + status
    nodes. An invalid slug or missing tree yields nothing."""
    try:
        mem = store.memory_dir(project, root)
    except store.InvalidSlugError:
        return
    for doc in store.list_curated(root, project):
        yield (str(doc.get("name") or doc.file_path.stem), "curated", doc.body)
    nodes_dir = mem / "nodes"
    if nodes_dir.exists():
        for path in sorted(nodes_dir.glob("*.md")):
            try:
                doc = store.read_doc(path)
            except ValueError:
                continue
            yield (str(doc.get("name") or path.stem), "node", doc.body)


def search(
    root: Path,
    query: str,
    project: str | None = None,
    limit: int | None = _DEFAULT_LIMIT,
) -> list[SearchHit]:
    """Ranked hits over curated facts + nodes. Empty query (no word tokens) or
    no matches ⇒ ``[]``. Results sort by score desc, then project, then name
    for a stable order; ``limit`` caps the count (``None`` = uncapped)."""
    terms = _tokenize(query)
    if not terms:
        return []
    targets = [project] if project is not None else sorted(_all_projects(root))
    hits: list[SearchHit] = []
    for proj in targets:
        for name, kind, body in _candidates(root, proj):
            score = _score(terms, name, body)
            if score <= 0:
                continue
            hits.append(SearchHit(proj, name, kind, score, _snippet(terms, body)))
    hits.sort(key=lambda h: (-h.score, h.project, h.name))
    if limit is not None and limit >= 0:
        return hits[:limit]
    return hits
