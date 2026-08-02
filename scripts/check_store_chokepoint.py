#!/usr/bin/env python3
"""ADR-0015 step 5 — the store-chokepoint CI guard.

The ``StoreHandle`` chokepoint (ADR-0015) only closes G1 if it is *unavoidable*:
production code must reach the store through a validated handle, never by calling a
raw-``root`` store/registry accessor or referencing the store-metadata filenames.
Steps 3/4a/4b converted every production caller of the store-tree/registry **accessors**
onto ``open_store(...)`` + handle methods; this check is what catches the **ordinary**
regression — a new call site that reintroduces a raw-``root`` accessor through one of
the *enumerated* spellings below fails CI instead of silently re-opening the hole. It
does not make the hole unreachable; see "What this check IS" below for the spellings it
is known to miss, and where the real guarantee comes from.
(The ``init --agent`` and ``uninstall --purge-store`` lifecycle
commands run the D11 guard command-side — a ``READ`` handle before installing hooks, a
``PURGE`` handle before deleting — ADR-0015 step 4d. The config-backup facility
(``backups.backup_files``/``restore_backup``) is a schema-independent maintenance
exception, not tracked here; its non-purge-uninstall/restore callers open no handle, and
purge deliberately skips the backup entirely — see spec §10.)

**Scope (deliberately ``src/`` only).** The raw-``root`` functions still *exist* on
``core.store`` / ``core.projects`` — the ADR's "remove the signatures" step is
deferred (they remain the low-level implementation the handle methods delegate to,
and the test suite's store-setup helpers). This guard runs where it matters —
production modules under ``src/neurobase/`` — and exempts the three core modules that
ARE the store/registry implementation.

**What this check MATCHES** outside the exempt modules — the *accessor* contract. This
is what the guard looks for, not a statement of everything that would be a violation;
the misses and false positives below are the difference between the two:
- calling a raw-``root`` store tree/metadata accessor (``memory_dir``, ``ensure_tree``,
  ``list_raw``, ``list_curated``, ``write_raw``, ``upsert_curated``, ``write_node``,
  ``rebuild_index``, …) or a registry accessor (``load_registry`` /
  ``register_project`` / ``resolve_project``) — whether reached as ``store.x`` /
  ``projects.x``, via a dotted module (``neurobase.core.store.memory_dir``), or by a
  direct/relative import (``from ..core.store import memory_dir``);
- the store-metadata filename literals ``"store.toml"`` / ``"registry.toml"``.

**What this check IS — and what it is not** _(Codex P2-SAFETY-SECURITY-013, rounds 7–10)._
This is a **conservative, enumerated syntactic regression guard**. It recognizes the
listed accessor names reached through the *enumerated* receiver spellings below. It is
**not** a proof that raw-``root`` access is impossible, and it is **not** free of false
positives. Three rounds of review each found one more lint-clean spelling that reached a
listed accessor with this check printing OK (a public path helper, a reassigned module
alias, then tuple unpacking); a fourth found that the *fix* for a false positive had
introduced three new misses of its own. That is the evidence for stating the limit
rather than re-asserting the guarantee:

- **Known static misses** — reachable, lint-clean, and NOT flagged: **tuple unpacking**
  (``project_api, _ = projects, None``), a **class-held alias** (``class API: module =
  projects``), a named-expression alias, and an alias bound *before* its import in
  traversal order. Making these fail would need a scope- and dataflow-aware
  implementation; adding one spelling per review round demonstrably does not converge.
- **Known false positives** — the alias sets are flat, with no symbol table:
  - a name rebound to a non-module later in a file stays treated as the module;
  - a **parameter that merely reuses an alias name** — of a function, an async
    function, or a lambda — is still read as the module, so the *sanctioned*
    ``handle.resolve_project(...)`` pattern is flagged when the parameter happens to be
    called ``project_api``. Round 7 special-cased this by un-binding the name for the
    ``FunctionDef`` subtree; round 8 **removed** that, because an AST subtree is not a
    Python scope — decorators, defaults and eager annotations resolve in the enclosing
    scope, and a nested ``global`` bypasses the parameter, so the un-binding suppressed
    three real violation shapes to fix one cosmetic failure. Renaming the parameter is
    the workaround; a symbol table is the only real fix, and it is out of scope here.
- **Out of scope by design:** anything dynamic — ``globals()[...]``,
  ``importlib.import_module``. ``import *`` needs no handling here: ruff rejects it with
  F403/F405, so it cannot reach a green gate.

**Where the real guarantee comes from.** The unavoidability of the chokepoint rests on
ADR-0015's *deferred* step — removing the raw-``Path`` signatures from ``core/store.py``
and ``core/projects.py`` so there is nothing for any spelling to reach. Until that lands,
this check is what stops the **ordinary** reintroduction (a new module reaching for a
familiar accessor), and it should be read as exactly that much. See spec §10 and
ADR-0015, which state the same limit in the same words.

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
- ``doctor``'s **three** corrupt-``store.toml`` reads — ``projects.resolve_project(root,
  cwd)``, ``projects.registry_is_contained(root)`` and ``store.store_toml_path(root)`` in
  ``cli/diagnostics.py`` — allow-listed here by (file, name). Project resolution is a
  ``registry.toml`` concern, independent of the store-schema guard (ADR-0015 registry
  carve-out, F1), so it is legitimate when no handle can open; the containment predicate
  is there so a corrupt ``store.toml`` cannot *mask* an out-of-store registry, and that
  branch has no handle to ask (spec §10).
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
        "list_tombstoned",
        "soft_delete_curated",
        "prune_tombstones",
        "write_node",
        "rebuild_index",
    }
)

# Raw-``root`` accessors on ``core.projects`` — every one reads, writes, or *names*
# registry.toml.
#
# ``registry_path`` / ``_registry_path`` are here because of Codex
# P2-SAFETY-SECURITY-013: round 4 promoted the path helper to public so a guard
# could name the file it was proving, which handed every production module a
# CI-approved way to reach the registry from a raw root — this checker matches
# accessors by NAME, so the ``"registry.toml"`` literal hidden inside the helper is
# invisible to SENSITIVE_LITERALS below. The helper is private again, and both
# spellings are listed so re-publishing it (or reaching for the underscore form
# across modules) fails the gate instead of silently reopening the hole.
#
# ``registry_is_contained`` is the containment predicate itself: it names the same
# path, and doctor's no-handle branch is the one place that may ask it without a
# validated handle — allow-listed by (file, name) below, like doctor's other two
# sanctioned raw-root reads.
PROJECTS_FORBIDDEN = frozenset(
    {
        "load_registry",
        "register_project",
        "resolve_project",
        "registry_path",
        "_registry_path",
        "registry_is_contained",
    }
)

# Store-metadata filenames — a bare literal is a hand-rolled store path.
SENSITIVE_LITERALS = frozenset({"store.toml", "registry.toml"})

# The doctor's THREE sanctioned raw-``root`` reads on its no-handle (corrupt-store)
# path. (posix-relative-to-``src/neurobase``, accessor name.) Keep this count in step
# with the module docstring above, spec §10 and ADR-0015 — the guard's safety model is
# a small auditable exception list, so a stale count leaves the next reviewer unable to
# tell a sanctioned exemption from an accidental one (Codex P3-DOCS-PLAN-ACCURACY-015).
ALLOW: frozenset[tuple[str, str]] = frozenset(
    {
        ("cli/diagnostics.py", "resolve_project"),
        ("cli/diagnostics.py", "store_toml_path"),
        # The health question doctor must be able to ask without a handle: is this
        # store's registry really this store's? Read-side callers never need it —
        # they get empty either way — so this stays a one-file exemption.
        ("cli/diagnostics.py", "registry_is_contained"),
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

    # --- module ALIASING by assignment (Codex P2-SAFETY-SECURITY-013, round 6) ---
    #
    # Import forms alone are not the whole static surface. Binding the module to a
    # second name is plain, lint-clean Python:
    #
    #     from neurobase.core import projects
    #     project_api = projects
    #     project_api._registry_path(root)      # ← reached the registry, guard silent
    #
    # The receiver is then `project_api`, which no import ever named, so `_dotted()`
    # matched nothing and the check printed OK on a module ruff was also happy with.
    # Codex demonstrated this as a real production module — the EIGHTH static shape,
    # after the seven import spellings round 5 added. Propagating simple aliases
    # closes it without any dataflow analysis: the alias set simply grows.
    #
    # `import *` also escapes, and is deliberately NOT chased here — ruff's F403
    # already rejects it, so it cannot reach a green gate. This shape survived both.

    def _bind_alias(self, targets: list[ast.expr], value: ast.expr) -> None:
        """Bind every plain-``Name`` target to whichever module ``value`` names."""
        source = _dotted(value)
        if source in self.store_names:
            bucket = self.store_names
        elif source in self.projects_names:
            bucket = self.projects_names
        else:
            return
        for target in targets:
            if isinstance(target, ast.Name):
                bucket.add(target.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Covers `a = projects` and the chained `a = b = projects`.
        self._bind_alias(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # Covers the annotated form, e.g. `a: ModuleType = projects`.
        if node.value is not None:
            self._bind_alias([node.target], node.value)
        self.generic_visit(node)

    # --- parameter shadowing is NOT handled (Codex P2-SAFETY-SECURITY-013, round 8) ---
    #
    # Round 7 tried to fix the false positive below by un-binding a shadowed name for
    # the whole `FunctionDef` subtree. That was WRONG, and round 8 removed it: **an AST
    # subtree is not a Python scope.** A function's decorators, defaults and eagerly
    # evaluated annotations resolve in the *enclosing* scope before the function object
    # exists, and a nested `global` explicitly bypasses the parameter binding — yet all
    # of them live under the `FunctionDef` node. Un-binding therefore SUPPRESSED real
    # violations (verified false negatives, each lint-clean and production-shaped):
    #
    #     project_api = projects
    #     @keep_decorator_input(project_api.resolve_project(ROOT, ROOT))   # enclosing scope
    #     def decorated(project_api): ...
    #
    #     def annotated(project_api: project_api.resolve_project(ROOT, ROOT)): ...
    #
    #     def outer(project_api):
    #         def inner(root, cwd):
    #             global project_api                    # bypasses the parameter
    #             return project_api.resolve_project(root, cwd)
    #
    # Trading three false negatives for one false positive is the wrong direction for a
    # regression guard, and making it correct means the scope-aware implementation this
    # check deliberately is not. So parameter shadowing — of an ordinary function, an
    # async function, or a lambda (whose parameters the round-7 fix never covered
    # anyway, so lambdas false-positived throughout) — is recorded as a known FALSE
    # POSITIVE class in the module docstring, alongside the flat-alias-set rebinding it
    # is a special case of. The honest limit, not a suppressed one.

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
        "store-chokepoint: OK — no raw-root store/registry access in the enumerated "
        "spellings\n(a conservative regression check, not proof of unreachability — "
        "see the module docstring)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
