"""Tests for the ADR-0015 step-5 store-chokepoint guard (``scripts/check_store_chokepoint.py``).

The guard is a conservative regression check over G1's *accessor* class: production code
must reach the store-tree / registry accessors through ``open_store(...)`` + a
``StoreHandle``, never a raw-``root`` call, and this check fails the gate when one of the
*enumerated* spellings reappears. It has recorded static misses and false positives (see
the guard's own docstring) — closing the class outright is ADR-0015's deferred
signature-removal step, not this. (The ``init --agent`` and ``uninstall --purge-store``
lifecycle commands are
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
    store-tree/registry accessors through the handle (the lifecycle commands are
    command-guarded, outside this accessor guard's scope).

    What this proves is bounded: the current tree is clean *in the spellings the guard
    enumerates*. Because the known misses are real (see
    ``test_known_static_misses_are_recorded_not_caught``), a green result here is
    evidence that the invariant holds today, not that it cannot be broken."""
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


# --- the registry PATH BUILDER, in every import shape (Codex P2-SAFETY-SECURITY-013) ---
#
# Round 4 promoted ``_registry_path`` to a public ``registry_path`` so a containment
# guard could name the file it was proving. That handed every production module a
# CI-approved way to reach the registry from a raw root: this guard matches accessors
# by NAME, and the ``"registry.toml"`` literal is hidden *inside* the helper, so
# SENSITIVE_LITERALS could not see it either — Codex's probes returned zero violations
# while the repo check still printed OK. The helper is private again AND both spellings
# are forbidden, so re-publishing it fails the gate. Each import shape gets its own test
# because a name-matching guard is exactly the kind that passes for the spelling you
# thought of and misses the one you didn't (round-1 F1 cost two of these).


def test_flags_registry_path_module_attribute() -> None:
    src = (
        "from neurobase.core import projects\n\n"
        "def f(root):\n    return projects.registry_path(root).read_text()\n"
    )
    details = _details("webui/other.py", src)
    assert len(details) == 1 and "projects.registry_path" in details[0]


def test_flags_private_registry_path_module_attribute() -> None:
    """The underscore spelling is what exists today, so reaching for it across
    modules must fail too — privacy is a convention, this guard is mechanical."""
    src = (
        "from neurobase.core import projects\n\n"
        "def f(root):\n    return projects._registry_path(root).read_text()\n"
    )
    details = _details("webui/other.py", src)
    assert len(details) == 1 and "projects._registry_path" in details[0]


def test_flags_directly_imported_registry_path() -> None:
    src = (
        "from neurobase.core.projects import registry_path\n\n"
        "def f(root):\n    return registry_path(root)\n"
    )
    details = _details("adapters/x.py", src)
    assert len(details) == 1 and "registry_path" in details[0]


def test_flags_aliased_registry_path_import() -> None:
    """``as`` is the cheapest way around a name-matching guard; the visitor binds the
    local alias to the original accessor, so the alias is flagged under its real name."""
    src = (
        "from neurobase.core.projects import registry_path as rp\n\n"
        "def f(root):\n    return rp(root)\n"
    )
    details = _details("adapters/x.py", src)
    assert len(details) == 1 and "registry_path" in details[0]


def test_flags_relative_import_of_registry_path() -> None:
    src = (
        "from ..core.projects import registry_path\n\n"
        "def f(root):\n    return registry_path(root)\n"
    )
    details = _details("recommender/x.py", src)
    assert len(details) == 1 and "registry_path" in details[0]


def test_flags_fully_dotted_registry_path() -> None:
    src = (
        "import neurobase.core.projects\n\n"
        "def f(root):\n    return neurobase.core.projects.registry_path(root)\n"
    )
    details = _details("curator/x.py", src)
    assert len(details) == 1 and "registry_path" in details[0]


def test_flags_registry_is_contained_outside_diagnostics() -> None:
    """The containment predicate names the same path. Read-side callers never need
    it — an uncontained registry already reads as empty — so only doctor's health
    check may ask, and only doctor."""
    src = (
        "from neurobase.core import projects\n\n"
        "def f(root):\n    return projects.registry_is_contained(root)\n"
    )
    details = _details("webui/graph_view.py", src)
    assert len(details) == 1 and "registry_is_contained" in details[0]


# --- module ALIASING by assignment (Codex P2-SAFETY-SECURITY-013, round 6) ---
#
# The eighth static shape, and the one that mattered: round 5 added seven IMPORT
# spellings, but binding the module to a second name is plain, lint-clean Python and
# no import ever names the receiver. Codex ran the probe below as a real production
# module and got ruff clean AND the real checker printing OK — so the guard, its
# tests, ADR-0015 and spec §10 were all asserting an enforcement property that did
# not hold. `import *` escapes too and is deliberately not chased: ruff's F403 stops
# it reaching a green gate, which is exactly what this shape survived.


def test_flags_reassigned_module_alias() -> None:
    """Codex's exact probe, verbatim."""
    src = (
        "from neurobase.core import projects\n\n"
        "project_api = projects\n\n"
        "def probe(root):\n    return project_api._registry_path(root)\n"
    )
    details = _details("webui/other.py", src)
    assert len(details) == 1 and "projects._registry_path" in details[0]


def test_flags_chained_module_alias() -> None:
    """``a = b = projects`` — every plain-Name target binds."""
    src = (
        "from neurobase.core import projects\n\n"
        "a = b = projects\n\n"
        "def f(root):\n    return b.load_registry(root)\n"
    )
    details = _details("webui/other.py", src)
    assert len(details) == 1 and "projects.load_registry" in details[0]


def test_flags_annotated_module_alias() -> None:
    src = (
        "from types import ModuleType\n"
        "from neurobase.core import projects\n\n"
        "p: ModuleType = projects\n\n"
        "def f(root, cwd):\n    return p.resolve_project(root, cwd)\n"
    )
    details = _details("adapters/x.py", src)
    assert len(details) == 1 and "projects.resolve_project" in details[0]


def test_flags_an_alias_of_an_alias() -> None:
    """Aliases accumulate, so a second hop cannot launder the first."""
    src = (
        "from neurobase.core import projects\n\n"
        "x = projects\ny = x\n\n"
        "def f(root):\n    return y.registry_is_contained(root)\n"
    )
    details = _details("curator/x.py", src)
    assert len(details) == 1 and "projects.registry_is_contained" in details[0]


def test_flags_an_alias_of_a_dotted_module_import() -> None:
    """The alias source may itself be a dotted chain, not a bare Name."""
    src = (
        "import neurobase.core.store\n\n"
        "m = neurobase.core.store\n\n"
        "def f(root, p):\n    return m.memory_dir(p, root)\n"
    )
    details = _details("recommender/x.py", src)
    assert len(details) == 1 and "store.memory_dir" in details[0]


def test_an_unrelated_assignment_does_not_become_a_receiver() -> None:
    """The false-positive side, aimed at the case that would actually hurt.

    Only an assignment whose VALUE names a tracked module may bind. If binding were
    unconditional, `handle = open_store(...)` would make `handle` a "projects" alias
    and `handle.load_registry()` — **the sanctioned pattern this entire guard exists
    to push people toward** — would be flagged in every module in the tree.

    Written after a mutation: an earlier version of this test used `z = 3` and a bare
    `return z`, which survived unconditional binding because a Name load is not an
    Attribute access. It asserted nothing about the risk it named."""
    src = (
        "from neurobase.core.store_handle import StoreMode, open_store\n\n"
        "def f(root, cwd):\n"
        "    handle = open_store(root, StoreMode.READ)\n"
        "    registry = handle.load_registry()\n"
        "    return registry, handle.resolve_project(cwd)\n"
    )
    assert _details("webui/other.py", src) == []


def test_parameter_shadowing_is_a_recorded_false_positive() -> None:
    """The known false positive, made executable (Codex P2-SAFETY-SECURITY-013, round 8).

    The alias sets are flat, so a module-level `project_api = projects` makes every
    later `project_api` a module receiver — including a parameter that merely reuses
    the name, whose value is a validated `StoreHandle`. So the *sanctioned* pattern is
    flagged when the parameter happens to share the alias's name.

    Round 7 "fixed" this by un-binding the name for the whole `FunctionDef` subtree.
    Round 8 removed that fix and this test inverted, because an AST subtree is not a
    Python scope: the un-binding suppressed three real violation shapes (see the three
    tests below) to silence one cosmetic failure — a strictly worse trade for a
    regression guard. Renaming the parameter is the workaround; a symbol table is the
    only real fix, and it is deliberately out of scope.

    This is a characterization test, not a sanction: if the checker ever grows real
    scope awareness these assertions fail loudly and the prose sites must move with
    them."""
    sync = (
        "from neurobase.core import projects\n"
        "from neurobase.core.store_handle import StoreHandle\n\n"
        "project_api = projects\n\n"
        "def probe(project_api: StoreHandle, cwd):\n"
        "    return project_api.resolve_project(cwd)\n"
    )
    asynchronous = (
        "from neurobase.core import projects\n\n"
        "project_api = projects\n\n"
        "async def probe(project_api, cwd):\n"
        "    return project_api.resolve_project(cwd)\n"
    )
    # Lambda parameters were never covered by the round-7 fix either, so this spelling
    # false-positived throughout — it is the same class, now stated as one.
    lambda_form = (
        "from neurobase.core import projects\n\n"
        "project_api = projects\n\n"
        "probe = lambda project_api, cwd: project_api.resolve_project(cwd)\n"
    )
    for src in (sync, asynchronous, lambda_form):
        details = _details("webui/other.py", src)
        assert len(details) == 1 and "projects.resolve_project" in details[0]


# --- what the round-7 shadow fix had suppressed (Codex round 8) -------------
#
# Each of these resolves in the *enclosing* scope, not under the parameter binding,
# yet each lives under the `FunctionDef` node — which is why un-binding across that
# subtree turned real violations into silent passes. They are lint-clean and
# production-shaped: ruff rejects an accessor call in a *default* (B008), but not
# these.


def test_flags_an_accessor_in_a_decorator_of_a_shadowing_function() -> None:
    """A decorator expression is evaluated in the enclosing scope, before the function
    exists — the parameter of the function it decorates cannot shadow anything in it."""
    src = (
        "from neurobase.core import projects\n\n"
        "ROOT = None\n"
        "project_api = projects\n\n"
        # Defined, not assumed: as a bare name this fixture was F821 and so could not
        # have reached a green gate on its own — which made the "lint-clean" premise
        # below false for exactly this one (Codex P2-SAFETY-SECURITY-013, round 9).
        "def keep_decorator_input(value):\n"
        "    return lambda fn: fn\n\n"
        "@keep_decorator_input(project_api.resolve_project(ROOT, ROOT))\n"
        "def decorated(project_api):\n"
        "    return project_api\n"
    )
    details = _details("webui/other.py", src)
    assert len(details) == 1 and "projects.resolve_project" in details[0]


def test_flags_an_accessor_in_an_eagerly_evaluated_annotation() -> None:
    """An annotation on the very parameter that shadows the alias still resolves in the
    enclosing scope (absent `from __future__ import annotations`)."""
    src = (
        "from neurobase.core import projects\n\n"
        "ROOT = None\n"
        "project_api = projects\n\n"
        "def annotated(project_api: project_api.resolve_project(ROOT, ROOT)):\n"
        "    return project_api\n"
    )
    details = _details("webui/other.py", src)
    assert len(details) == 1 and "projects.resolve_project" in details[0]


def test_flags_an_accessor_reached_through_a_nested_global_declaration() -> None:
    """`global` explicitly bypasses the enclosing function's parameter binding, so the
    inner reference is the module-level alias however the outer parameter is named."""
    src = (
        "from neurobase.core import projects\n\n"
        "project_api = projects\n\n"
        "def outer(project_api):\n"
        "    def inner(root, cwd):\n"
        "        global project_api\n"
        "        return project_api.resolve_project(root, cwd)\n"
        "    return inner\n"
    )
    details = _details("webui/other.py", src)
    assert len(details) == 1 and "projects.resolve_project" in details[0]


def test_known_static_misses_are_recorded_not_caught() -> None:
    """The honest contract, made executable (Codex P2-SAFETY-SECURITY-013, round 7).

    This guard is a **conservative, enumerated syntactic regression guard**, not proof
    that raw-root access is impossible. Three review rounds each found one more
    lint-clean spelling that reached a listed accessor with the check printing OK, so
    the limit is now stated rather than the guarantee re-asserted — in the guard's
    docstring, spec §10, ADR-0015 and G1.

    These two forms are the recorded residual. Asserting them keeps the documentation
    honest **and self-invalidating**: if someone later makes the checker scope- and
    dataflow-aware, this test fails loudly and every prose site describing the limit
    must be updated with it. It is a record of a known limit, never an endorsement of it — the real
    guarantee is ADR-0015's deferred removal of the raw-`Path` signatures."""
    tuple_unpacking = (
        "from neurobase.core import projects\n\n"
        "project_api, ignored = projects, None\n\n"
        "def probe(root):\n    return project_api._registry_path(root)\n"
    )
    class_attribute = (
        "from neurobase.core import projects\n\n"
        "class API:\n    module = projects\n\n"
        "def probe(root):\n    return API.module._registry_path(root)\n"
    )
    assert _details("webui/other.py", tuple_unpacking) == []
    assert _details("webui/other.py", class_attribute) == []


def test_doctor_registry_is_contained_is_allowlisted() -> None:
    """Doctor's no-handle branch must be able to tell an uncontained registry from an
    absent one even when ``store.toml`` is corrupt, so it has no handle to ask."""
    src = (
        "from neurobase.core import projects\n\n"
        "def f(root):\n    return projects.registry_is_contained(root)\n"
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
