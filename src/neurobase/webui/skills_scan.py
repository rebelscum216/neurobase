"""Discover ``SKILL.md`` skills across the machine — user scope
(``~/.claude/skills``) and each registered project's ``<root>/.claude/skills`` —
so the Skills gallery can show *every* skill on the machine, not only the ones
Neurobase installed.

Neurobase-owned skills carry ``neurobase_managed`` / ``neurobase_slug``
frontmatter (``recommender/emitters.py``); everything else is **external** —
hand-authored, or installed by some other tool. This module only reads; it never
touches the store, so it sits outside the ADR-0015 chokepoint by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Leading YAML frontmatter, tolerant of a BOM and whitespace around the fences,
# and not requiring the blank line the store's stricter _DOC_RE wants.
_FM_RE = re.compile(r"\A﻿?---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", re.DOTALL)


@dataclass(frozen=True)
class InstalledSkill:
    slug: str  # the skill directory name
    name: str  # frontmatter name (falls back to slug)
    description: str
    scope: str  # "user" | "project"
    project: str | None  # the registered project slug, for project scope
    path: str  # ~-collapsed SKILL.md path
    managed: bool  # carries neurobase_managed: true
    nb_slug: str | None  # neurobase_slug — links back to the proposal


def user_skills_root() -> Path:
    """The user-scope skills directory. A seam so tests can point it at a tmp
    home instead of scanning the real ``~/.claude/skills``."""
    return Path.home() / ".claude" / "skills"


def _tilde(path: Path) -> str:
    try:
        return f"~/{path.resolve().relative_to(Path.home())}"
    except (ValueError, OSError):
        return str(path)


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = _FM_RE.match(text)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _scan(skills_dir: Path, scope: str, project: str | None) -> list[InstalledSkill]:
    out: list[InstalledSkill] = []
    try:
        entries = sorted(skills_dir.iterdir())
    except OSError:
        return out  # missing/unreadable dir — no skills here, never fatal
    for sub in entries:
        skill_md = sub / "SKILL.md"
        if not skill_md.is_file():
            continue
        fm = _frontmatter(skill_md)
        nb_slug = fm.get("neurobase_slug")
        out.append(
            InstalledSkill(
                slug=sub.name,
                name=str(fm.get("name") or sub.name),
                description=str(fm.get("description") or ""),
                scope=scope,
                project=project,
                path=_tilde(skill_md),
                managed=bool(fm.get("neurobase_managed")),
                nb_slug=str(nb_slug) if nb_slug else None,
            )
        )
    return out


def discover_skills(handle: Any) -> list[InstalledSkill]:
    """Every ``SKILL.md`` under the user skills dir and each registered project's
    ``.claude/skills``. Fail-soft: an unreadable dir or file is skipped, never
    fatal. Ordered user-first, then by name."""
    skills = _scan(user_skills_root(), "user", None)
    seen: set[Path] = set()
    for project, roots in handle.load_registry().items():
        for raw in roots:
            root_path = Path(raw)
            if root_path in seen:
                continue  # a project may list a root more than once
            seen.add(root_path)
            skills.extend(_scan(root_path / ".claude" / "skills", "project", project))
    skills.sort(key=lambda s: (s.scope != "user", s.name.lower()))
    return skills
