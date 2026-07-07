"""Linkify (spec §6): project frontmatter edges into Obsidian ``[[wikilinks]]``.

Runs after every curate. Adds a single idempotent fenced block to each
``curated/`` and ``nodes/`` file's **body** — frontmatter is preserved
byte-for-byte, and ``raw/`` / ``.tombstones/`` are never touched.
"""

from __future__ import annotations

import re
from pathlib import Path

from neurobase.core import store

LINEAGE_START = "<!-- lineage:auto (generated — edits here are overwritten) -->"
LINEAGE_END = "<!-- /lineage:auto -->"

# The fenced block, including its markers and any surrounding blank lines, so a
# rerun replaces it wholesale rather than stacking blocks.
_BLOCK_RE = re.compile(
    r"\n*" + re.escape(LINEAGE_START) + r".*?" + re.escape(LINEAGE_END) + r"\n*",
    re.DOTALL,
)

# Frontmatter + body split that keeps the frontmatter text verbatim (unlike
# store.read_doc, which parses it — we must NOT re-serialize the frontmatter).
_DOC_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n\n(?P<body>.*)\Z", re.DOTALL)


def _wikilink(basename: str) -> str:
    return f"[[{basename}]]"


def _strip_block(body: str) -> str:
    """Remove any existing lineage:auto block, leaving a single trailing
    newline discipline."""
    return _BLOCK_RE.sub("\n", body).rstrip() + "\n" if body.strip() else ""


def _apply_block(path: Path, block: str | None) -> None:
    """Rewrite ``path`` with ``block`` as its lineage:auto section (or none),
    preserving the frontmatter bytes exactly and touching only the body."""
    text = path.read_text(encoding="utf-8")
    match = _DOC_RE.match(text)
    if not match:
        return  # not a frontmatter doc; leave it alone
    frontmatter = match.group("frontmatter")
    body = _strip_block(match.group("body"))
    if block:
        body = f"{body.rstrip()}\n\n{block}\n" if body.strip() else f"{block}\n"
    new_text = f"---\n{frontmatter}\n---\n\n{body}"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)


def _curated_block(doc: store.Document) -> str | None:
    """`## Lineage` block from ``provenance`` (Sources) + ``supersedes``. Skip
    the block entirely if both are empty."""
    provenance = [str(p) for p in (doc.get("provenance") or [])]
    supersedes = [str(s) for s in (doc.get("supersedes") or [])]
    if not provenance and not supersedes:
        return None
    lines = [LINEAGE_START, "## Lineage"]
    if provenance:
        # provenance is like "raw/<basename>.md"; wikilink the basename w/o .md.
        sources = [_wikilink(Path(p).stem) for p in provenance]
        lines.append(f"**Sources:** {' · '.join(sources)}")
    if supersedes:
        lines.append(f"**Supersedes:** {' · '.join(_wikilink(s) for s in supersedes)}")
    lines.append(LINEAGE_END)
    return "\n".join(lines)


def _node_block(active_slugs: list[str]) -> str | None:
    """`## Synthesized from` block linking every active curated fact. Skip if
    there are none."""
    if not active_slugs:
        return None
    links = " · ".join(_wikilink(slug) for slug in active_slugs)
    return "\n".join([LINEAGE_START, "## Synthesized from", links, LINEAGE_END])


def linkify(root: Path, project: str) -> None:
    """Rewrite the lineage:auto block in every ``curated/`` and ``nodes/`` file.
    Idempotent; frontmatter preserved byte-for-byte; ``raw/`` and
    ``.tombstones/`` never modified."""
    mem = store.memory_dir(project, root)

    active = store.list_curated(root, project)
    active_slugs = [str(doc.get("name", doc.file_path.stem)) for doc in active]

    for doc in active:
        _apply_block(doc.file_path, _curated_block(doc))

    nodes_dir = mem / "nodes"
    if nodes_dir.exists():
        node_block = _node_block(active_slugs)
        for node_path in sorted(nodes_dir.glob("*.md")):
            _apply_block(node_path, node_block)
