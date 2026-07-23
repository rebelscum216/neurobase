"""Tests for the ADR-0015 step-5 store-chokepoint guard (``scripts/check_store_chokepoint.py``).

The guard closes G1's *accessor* class: production code must reach the store-tree /
registry accessors through ``open_store(...)`` + a ``StoreHandle``, never a raw-``root``
call. (The ``init --agent`` and ``uninstall --purge-store`` lifecycle commands are
command-guarded instead — a ``READ``/``PURGE`` handle at the command — ADR-0015 step 4d;
they are outside this accessor guard's scope.) These tests exercise the REAL guard
module (imported by path, like ``test_redact_audit``) so the check the developer runs is
the one under test — not a copy free to drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `scripts/` is not a package and is not on `sys.path` under pytest — import the
# guard module by path so these tests exercise the real script code.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_store_chokepoint as guard  # noqa: E402


def _details(relpath: str, source: str) -> list[str]:
    return [detail for _lineno, detail in guard.check_source(relpath, source)]


# --- the real tree is clean (the invariant this guard protects) ------------


def test_current_src_tree_has_no_violations() -> None:
    """The whole shipped ``src/neurobase`` tree passes — production reaches the
    store-tree/registry accessors through the handle, so the guard enforces a true
    invariant, not aspirational (the lifecycle commands are command-guarded, outside
    this accessor guard's scope)."""
    failures: list[str] = []
    for path in sorted(guard.SRC.rglob("*.py")):
        relpath = path.relative_to(guard.SRC).as_posix()
        if relpath in guard.EXEMPT:
            continue
        failures.extend(
            f"{relpath}:{lineno}: {detail}"
            for lineno, detail in guard.check_source(relpath, path.read_text(encoding="utf-8"))
        )
    assert failures == []


# --- what MUST be flagged --------------------------------------------------


def test_flags_raw_root_store_memory_dir() -> None:
    src = (
        "from neurobase.core import store\n\n"
        "def f(root, p):\n    return store.memory_dir(p, root)\n"
    )
    details = _details("curator/thing.py", src)
    assert len(details) == 1 and "store.memory_dir" in details[0]


def test_flags_raw_root_registry_resolve_project() -> None:
    src = (
        "from neurobase.core import projects\n\n"
        "def f(root, cwd):\n    return projects.resolve_project(root, cwd)\n"
    )
    details = _details("cli/other.py", src)
    assert len(details) == 1 and "projects.resolve_project" in details[0]


def test_flags_directly_imported_forbidden_accessor() -> None:
    """The attribute form (``store.memory_dir``) is what production uses today, but a
    future ``from …store import memory_dir`` must be caught too (defense in depth)."""
    src = (
        "from neurobase.core.store import memory_dir\n\n"
        "def f(root, p):\n    return memory_dir(p, root)\n"
    )
    details = _details("adapters/x.py", src)
    assert len(details) == 1 and "memory_dir" in details[0]


def test_flags_relative_import_of_store_module() -> None:
    """A future caller reaching the module via a relative import
    (``from ..core import store``) must be caught, not just the absolute spelling
    (Codex round-1 F1: the guard recognized only absolute ``neurobase.core``)."""
    src = "from ..core import store\n\ndef f(root, p):\n    return store.memory_dir(p, root)\n"
    details = _details("recommender/x.py", src)
    assert len(details) == 1 and "memory_dir" in details[0]


def test_flags_dotted_module_attribute_access() -> None:
    """``import neurobase.core.store`` then ``neurobase.core.store.memory_dir(...)``
    — the receiver is a dotted Attribute chain, not a bare Name (Codex round-1 F1)."""
    src = (
        "import neurobase.core.store\n\n"
        "def f(root, p):\n    return neurobase.core.store.memory_dir(p, root)\n"
    )
    details = _details("curator/x.py", src)
    assert len(details) == 1 and "memory_dir" in details[0]


def test_flags_relative_direct_import_of_accessor() -> None:
    """``from ..core.store import list_raw`` then ``list_raw(root, project)`` (Codex
    round-1 F1: only the absolute direct-import spelling was recognized)."""
    src = (
        "from ..core.store import list_raw\n\n"
        "def f(root, project):\n    return list_raw(root, project)\n"
    )
    details = _details("adapters/x.py", src)
    assert len(details) == 1 and "list_raw" in details[0]


def test_flags_store_toml_literal() -> None:
    src = 'from pathlib import Path\n\ndef f(root):\n    return Path(root) / "store.toml"\n'
    details = _details("mcp/server.py", src)
    assert any("store.toml" in d for d in details)


def test_flags_registry_toml_literal() -> None:
    src = 'from pathlib import Path\n\ndef f(root):\n    return root / "registry.toml"\n'
    details = _details("recommender/x.py", src)
    assert any("registry.toml" in d for d in details)


# --- what MUST NOT be flagged (false-positive guards) ----------------------


def test_handle_derived_subpath_is_clean() -> None:
    """Appending a subdir to a handle-derived path is the sanctioned pattern —
    the chokepoint already ran to produce ``handle.memory_dir(p)``."""
    src = (
        "def f(handle, project, name):\n"
        '    nodes = handle.memory_dir(project) / "nodes"\n'
        '    return nodes / f"{name}.md"\n'
    )
    assert _details("mcp/server.py", src) == []


def test_claude_app_memory_path_is_clean() -> None:
    """The Claude app's own ``~/.claude/projects/<x>/memory`` is a *different*
    filesystem; its ``projects``/``memory`` fragments must not be mistaken for the
    neurobase store (this is ``recommender/seed.py:76`` in the real tree)."""
    src = (
        "from pathlib import Path\n\n"
        "def f(encoded):\n"
        '    return Path.home() / ".claude" / "projects" / encoded / "memory"\n'
    )
    assert _details("recommender/seed.py", src) == []


def test_read_write_doc_and_resolve_root_are_clean() -> None:
    """The format/boundary primitives — ``read_doc``/``write_doc`` (path-taking) and
    ``resolve_root`` (resolves the root dir, touches no store path) — are sanctioned."""
    src = (
        "from neurobase.core import store\n\n"
        "def f(path, fm, body):\n"
        "    root = store.resolve_root()\n"
        "    store.write_doc(path, fm, body)\n"
        "    return store.read_doc(path)\n"
    )
    assert _details("curator/engine.py", src) == []


# --- the sanctioned exceptions (the doctor's corrupt-store fallback) --------


def test_doctor_resolve_project_is_allowlisted() -> None:
    src = (
        "from neurobase.core import projects\n\n"
        "def f(root, cwd):\n    return projects.resolve_project(root, cwd)\n"
    )
    assert _details("cli/diagnostics.py", src) == []


def test_doctor_store_toml_path_is_allowlisted() -> None:
    src = (
        "from neurobase.core import store\n\ndef f(root):\n    return store.store_toml_path(root)\n"
    )
    assert _details("cli/diagnostics.py", src) == []


def test_allowlist_is_scoped_to_diagnostics() -> None:
    """The allow-list is (file, name) — the same call from any other module is
    still a violation, so the exception can't leak."""
    src = (
        "from neurobase.core import projects\n\n"
        "def f(root, cwd):\n    return projects.resolve_project(root, cwd)\n"
    )
    assert len(_details("cli/__init__.py", src)) == 1


# --- exempt modules are never scanned --------------------------------------


def test_exempt_core_modules_are_skipped() -> None:
    src = (
        "from neurobase.core import store\n\n"
        "def f(root, p):\n    return store.memory_dir(p, root)\n"
    )
    assert guard.check_source("core/store.py", src) == []
    assert guard.check_source("core/projects.py", src) == []
    assert guard.check_source("core/store_handle.py", src) == []
