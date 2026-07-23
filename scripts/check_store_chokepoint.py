#!/usr/bin/env python3
"""ADR-0015 step 5 — the store-chokepoint CI guard.

The ``StoreHandle`` chokepoint (ADR-0015) only closes G1 if it is *unavoidable*:
production code must reach the store through a validated handle, never by calling a
raw-``root`` store/registry accessor or referencing the store-metadata filenames.
Steps 3/4a/4b converted every production caller onto ``open_store(...)`` + handle
methods; this check is what keeps them there — a new call site that reintroduces a
raw-``root`` accessor fails CI instead of silently re-opening the hole.

**Scope (deliberately ``src/`` only).** The raw-``root`` functions still *exist* on
``core.store`` / ``core.projects`` — the ADR's "remove the signatures" step is
deferred (they remain the low-level implementation the handle methods delegate to,
and the test suite's store-setup helpers). This guard enforces the invariant where
it matters — production modules under ``src/neurobase/`` — and exempts the three core
modules that ARE the store/registry implementation.

**What is forbidden** outside the exempt modules — the *accessor* contract, stated to
exactly match what is mechanically enforceable:
- calling a raw-``root`` store tree/metadata accessor (``memory_dir``, ``ensure_tree``,
  ``list_raw``, ``list_curated``, ``write_raw``, ``upsert_curated``, ``write_node``,
  ``rebuild_index``, …) or a registry accessor (``load_registry`` /
  ``register_project`` / ``resolve_project``) — whether reached as ``store.x`` /
  ``projects.x``, via a dotted module (``neurobase.core.store.memory_dir``), or by a
  direct/relative import (``from ..core.store import memory_dir``);
- the store-metadata filename literals ``"store.toml"`` / ``"registry.toml"``.

**What is deliberately *not* matched** (and why the contract is scoped to accessors,
not "any store path from a bare root"): appending a subdir to a *handle-derived* path
(``handle.memory_dir(p) / "nodes"``) is the sanctioned pattern; and a bare
``root / "projects" / … / "memory"`` layout cannot be distinguished by shape from the
Claude app's own ``~/.claude/projects/<x>/memory`` (``recommender/seed.py``) without
data-flow analysis — so path-fragment matching would false-positive. The guard keys on
the named accessors + metadata literals instead, and the §10 contract says exactly
that. See ``docs/neurobase-spec-appendix.md`` §10.

**Sanctioned raw-``root`` residuals** (pending the deferred signature removal — not
covered by this guard, documented in §10):
- ``doctor``'s two corrupt-``store.toml`` reads — ``projects.resolve_project(root, cwd)``
  and ``store.store_toml_path(root)`` in ``cli/diagnostics.py`` — allow-listed here by
  (file, name). Project resolution is a ``registry.toml`` concern, independent of the
  store-schema guard (ADR-0015 registry carve-out, F1), so it is legitimate when no
  handle can open.
- the recommender's ``proposals``/``ledger`` **path-builders**
  (``corpus.proposals_dir`` / ``proposal_path`` / ``ledger_path``), which stay
  root-taking but are guarded at the command entry that opens their handle. Not in the
  forbidden set (they are not the accessors this guard tracks).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "neurobase"

# The three modules that ARE the store/registry implementation — they construct
# store paths and read store.toml/registry.toml by definition, so they are exempt.
EXEMPT = {"core/store.py", "core/store_handle.py", "core/projects.py"}

# Raw-``root`` accessors on ``core.store``: project-scoped path builders + tree/
# metadata ops. ``read_doc``/``write_doc`` (path-primitives), ``resolve_root``,
# ``Document``, the exceptions and constants are NOT here — they take no root and
# are the sanctioned format/boundary primitives.
STORE_FORBIDDEN = frozenset(
    {
        "memory_dir",
        "ensure_tree",
        "ensure_store_metadata",
        "store_toml_path",
        "raw_path",
        "write_raw",
        "list_raw",
        "mark_consumed",
        "upsert_curated",
        "list_curated",
        "soft_delete_curated",
        "prune_tombstones",
        "write_node",
        "rebuild_index",
    }
)

# Raw-``root`` accessors on ``core.projects`` — every one reads/writes registry.toml.
PROJECTS_FORBIDDEN = frozenset({"load_registry", "register_project", "resolve_project"})

# Store-metadata filenames — a bare literal is a hand-rolled store path.
SENSITIVE_LITERALS = frozenset({"store.toml", "registry.toml"})

# The doctor's sanctioned raw-``root`` reads on its no-handle (corrupt-store) path.
# (posix-relative-to-``src/neurobase``, accessor name).
ALLOW: frozenset[tuple[str, str]] = frozenset(
    {
        ("cli/diagnostics.py", "resolve_project"),
        ("cli/diagnostics.py", "store_toml_path"),
    }
)


def _dotted(node: ast.expr) -> str | None:
    """The dotted name of a ``Name`` or nested ``Attribute`` chain, else ``None``.
    ``store`` → ``"store"``; ``neurobase.core.store`` → ``"neurobase.core.store"``.
    Lets the visitor treat a bare-``Name`` receiver and a dotted-module receiver
    (``import neurobase.core.store``) uniformly (Codex round-1 F1)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _module_kind(module: str | None, level: int) -> str | None:
    """Classify a ``from <module> import …`` target as the ``core`` package, the
    ``store`` module, or the ``projects`` module — matching absolute *and* relative
    spellings (``from ..core import store``), since a relative import reaches the
    same code and must be caught the same way (Codex round-1 F1)."""
    m = module or ""
    relative = level > 0
    if m == "neurobase.core" or (relative and (m == "core" or m.endswith(".core"))):
        return "core"
    if m == "neurobase.core.store" or (
        relative and (m == "core.store" or m.endswith(".core.store"))
    ):
        return "store"
    if m == "neurobase.core.projects" or (
        relative and (m == "core.projects" or m.endswith(".core.projects"))
    ):
        return "projects"
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        # Local names bound to the store / projects modules, and any directly
        # imported forbidden accessor (defense in depth — production uses the
        # ``store.x`` / ``projects.x`` attribute form today, but a future
        # ``from …store import memory_dir`` must be caught too).
        self.store_names: set[str] = set()
        self.projects_names: set[str] = set()
        self.direct: dict[str, str] = {}  # local name -> accessor name
        self.violations: list[tuple[int, str]] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        kind = _module_kind(node.module, node.level)
        if kind == "core":
            for alias in node.names:
                if alias.name == "store":
                    self.store_names.add(alias.asname or "store")
                elif alias.name == "projects":
                    self.projects_names.add(alias.asname or "projects")
        elif kind == "store":
            for alias in node.names:
                if alias.name in STORE_FORBIDDEN:
                    self.direct[alias.asname or alias.name] = alias.name
        elif kind == "projects":
            for alias in node.names:
                if alias.name in PROJECTS_FORBIDDEN:
                    self.direct[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "neurobase.core.store":
                self.store_names.add(alias.asname or "neurobase.core.store")
            elif alias.name == "neurobase.core.projects":
                self.projects_names.add(alias.asname or "neurobase.core.projects")
        self.generic_visit(node)

    def _flag(self, lineno: int, name: str, detail: str) -> None:
        if (self.relpath, name) in ALLOW:
            return
        self.violations.append((lineno, detail))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # The receiver may be a bare Name (``store.memory_dir``) or a dotted chain
        # (``neurobase.core.store.memory_dir``) — resolve both to one string.
        base = _dotted(node.value)
        if base in self.store_names and node.attr in STORE_FORBIDDEN:
            self._flag(node.lineno, node.attr, f"store.{node.attr}(...) — raw-root store access")
        elif base in self.projects_names and node.attr in PROJECTS_FORBIDDEN:
            self._flag(
                node.lineno, node.attr, f"projects.{node.attr}(...) — raw-root registry access"
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.direct:
            orig = self.direct[node.id]
            self._flag(node.lineno, orig, f"{orig}(...) — directly imported raw-root accessor")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value in SENSITIVE_LITERALS:
            self._flag(node.lineno, node.value, f'"{node.value}" — hand-built store metadata path')
        self.generic_visit(node)


def check_source(relpath: str, source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, detail)`` violations for one module's source. ``relpath``
    is posix-relative to ``src/neurobase`` (it drives both the exemption and the
    per-file allow-list). Exposed for direct unit testing of the guard."""
    if relpath in EXEMPT:
        return []
    visitor = _Visitor(relpath)
    visitor.visit(ast.parse(source, filename=relpath))
    return sorted(visitor.violations)


def _check_file(path: Path) -> list[tuple[int, str]]:
    relpath = path.relative_to(SRC).as_posix()
    return check_source(relpath, path.read_text(encoding="utf-8"))


def main() -> int:
    failures: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        relpath = path.relative_to(SRC).as_posix()
        if relpath in EXEMPT:
            continue
        for lineno, detail in _check_file(path):
            failures.append(f"src/neurobase/{relpath}:{lineno}: {detail}")

    if failures:
        print("Store-chokepoint violations (ADR-0015 step 5):\n", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nProduction code must reach the store through open_store(...) + a "
            "StoreHandle,\nnot a raw-root store/registry accessor. If this is a "
            "genuinely sanctioned\nread (like doctor's corrupt-store fallback), add "
            "it to ALLOW with a reason.",
            file=sys.stderr,
        )
        return 1

    print(
        "store-chokepoint: OK — no raw-root store/registry access outside the exempt core modules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
