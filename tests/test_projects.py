"""Tests for project registry + resolution (spec §10, decision D6)."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from neurobase.core import projects


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store-root"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "My Cool Repo!"
    repo_dir.mkdir()
    _git("init", "-q", cwd=repo_dir)
    _git("config", "user.email", "test@example.com", cwd=repo_dir)
    _git("config", "user.name", "Test", cwd=repo_dir)
    (repo_dir / "README.md").write_text("hi")
    _git("add", "README.md", cwd=repo_dir)
    _git("commit", "-q", "-m", "init", cwd=repo_dir)
    return repo_dir


# --- slugify --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("My Cool Repo!", "my-cool-repo"),
        ("already-a-slug", "already-a-slug"),
        ("  leading/trailing--junk___", "leading-trailing-junk"),
        ("UPPER_CASE", "upper-case"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert projects.slugify(name) == expected


# --- registry round-trip ---------------------------------------------------


def test_register_project_creates_registry_entry(root: Path, repo: Path) -> None:
    slug = projects.register_project(root, repo)
    assert slug == "my-cool-repo"
    registry = projects.load_registry(root)
    assert registry[slug] == [str(repo.resolve())]


def test_register_project_explicit_slug(root: Path, repo: Path) -> None:
    slug = projects.register_project(root, repo, slug="Custom Name")
    assert slug == "custom-name"


def test_register_project_rejects_empty_derived_slug(root: Path, tmp_path: Path) -> None:
    """A directory name that slugifies to "" must be rejected, not silently
    registered under an empty project slug."""
    all_punctuation_dir = tmp_path / "!!!"
    all_punctuation_dir.mkdir()
    with pytest.raises(projects.InvalidSlugError):
        projects.register_project(root, all_punctuation_dir)


def test_register_project_rejects_empty_explicit_slug(root: Path, repo: Path) -> None:
    with pytest.raises(projects.InvalidSlugError):
        projects.register_project(root, repo, slug="!!!")


def test_register_project_collision_raises(root: Path, repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "My Cool Repo (copy)"
    other.mkdir()
    _git("init", "-q", cwd=other)
    projects.register_project(root, repo)  # slugifies to "my-cool-repo"

    # A second, different root that happens to slugify to the same name
    # collides when the slug is auto-derived...
    monkey_repo = tmp_path / "my-cool-repo"
    monkey_repo.mkdir()
    with pytest.raises(projects.ProjectSlugCollisionError):
        projects.register_project(root, monkey_repo, slug=None)

    # ...but an explicit slug always bypasses the collision guard.
    slug = projects.register_project(root, other, slug="my-cool-repo-2")
    assert slug == "my-cool-repo-2"


# --- a corrupt registry: fail-soft to read, strict to rewrite ---------------
#
# Spec §10: "registry parseability is a separate, fail-soft concern — a corrupt
# registry.toml is *not* folded into the schema guard: registry reads
# (load_registry, resolve_project) treat it as empty". The contract lives at the
# accessors, so it is tested at the accessors — a reader-local wrapper only makes
# the surface that remembers to write one compliant.


def _corrupt(root: Path) -> Path:
    """A registry.toml that exists and does not parse."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "registry.toml"
    path.write_text('[projects."x"\nroots = [')
    return path


def _unreadable(root: Path) -> Path:
    """A registry.toml that cannot even be read — a directory in its place, which
    raises OSError from ``read_text`` before any parse is attempted."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "registry.toml"
    path.mkdir()
    return path


@pytest.mark.parametrize("arrange", [_corrupt, _unreadable])
def test_load_registry_treats_an_unusable_registry_as_empty(root: Path, arrange) -> None:  # type: ignore[no-untyped-def]
    arrange(root)
    assert projects.load_registry(root) == {}


@pytest.mark.parametrize("arrange", [_corrupt, _unreadable])
def test_resolve_project_treats_an_unusable_registry_as_untracked(
    root: Path,
    repo: Path,
    arrange,  # type: ignore[no-untyped-def]
) -> None:
    """The capture path resolves through here on every hook fire: a corrupt
    registry must read as *untracked*, not raise into the hook."""
    arrange(root)
    assert projects.resolve_project(root, repo) is None


@pytest.mark.parametrize("arrange", [_corrupt, _unreadable])
def test_register_project_refuses_to_clobber_an_unusable_registry(
    root: Path,
    repo: Path,
    arrange,  # type: ignore[no-untyped-def]
) -> None:
    """The one place fail-soft would be *destructive*. ``register_project`` reads
    the registry in order to rewrite it, so a silent empty read would overwrite a
    corrupt-but-present file and lose every other project's roots. Read-for-rewrite
    stays strict, and the file on disk must survive untouched."""
    path = arrange(root)
    before = path.read_text() if path.is_file() else None

    with pytest.raises((tomllib.TOMLDecodeError, OSError)):
        projects.register_project(root, repo)

    assert (path.read_text() if path.is_file() else None) == before


# --- valid TOML that is not a registry --------------------------------------
#
# Every one of these is reachable by hand-editing registry.toml, and each used to
# detonate *later* than the read — two of them inside ``Path()`` on the capture
# path — rather than at the accessor that promises to absorb them.

_SHAPES = {
    "entry-not-a-table": "[projects]\nx = 1\n",
    "root-not-a-string": "[projects.x]\nroots = [1]\n",
    "projects-not-a-table": "projects = []\n",
    "roots-not-a-list": '[projects.x]\nroots = "/tmp/x"\n',
}


@pytest.mark.parametrize("shape", list(_SHAPES.values()), ids=list(_SHAPES))
def test_load_registry_treats_a_wrongly_shaped_registry_as_empty(root: Path, shape: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "registry.toml").write_text(shape)
    assert projects.load_registry(root) == {}


@pytest.mark.parametrize("shape", list(_SHAPES.values()), ids=list(_SHAPES))
def test_resolve_project_survives_a_wrongly_shaped_registry(
    root: Path, repo: Path, shape: str
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "registry.toml").write_text(shape)
    assert projects.resolve_project(root, repo) is None


def test_one_mangled_entry_does_not_untrack_every_other_project(root: Path, repo: Path) -> None:
    """The deliberate deviation from "treat the whole file as empty": a lenient
    read skips the offending *entry* and keeps the rest. ``resolve_project`` runs
    at hook time, so one hand-mangled entry must untrack that project alone — not
    silently kill capture everywhere."""
    projects.register_project(root, repo)
    registry = root / "registry.toml"
    registry.write_text(registry.read_text() + "\n[projects.mangled]\nroots = [1]\n")

    assert projects.load_registry(root) == {"my-cool-repo": [str(repo.resolve())]}
    assert projects.resolve_project(root, repo) == "my-cool-repo"


@pytest.mark.parametrize("shape", list(_SHAPES.values()), ids=list(_SHAPES))
def test_register_project_refuses_a_wrongly_shaped_registry(
    root: Path, repo: Path, shape: str
) -> None:
    """Read-for-rewrite stays strict for shape violations too: skipping an entry
    and *then* rewriting the file is how the skipped project's roots would be
    lost for good."""
    root.mkdir(parents=True, exist_ok=True)
    registry = root / "registry.toml"
    registry.write_text(shape)

    with pytest.raises(projects.RegistryShapeError):
        projects.register_project(root, repo)

    assert registry.read_text() == shape


# --- resolution: git root, worktrees, non-git, no-match ---------------------


def test_resolve_project_from_repo_root(root: Path, repo: Path) -> None:
    projects.register_project(root, repo)
    assert projects.resolve_project(root, repo) == "my-cool-repo"


def test_resolve_project_from_subdirectory(root: Path, repo: Path) -> None:
    projects.register_project(root, repo)
    subdir = repo / "src" / "pkg"
    subdir.mkdir(parents=True)
    assert projects.resolve_project(root, subdir) == "my-cool-repo"


def test_resolve_project_worktree_collapses_to_same_project(
    root: Path, repo: Path, tmp_path: Path
) -> None:
    projects.register_project(root, repo)
    worktree_dir = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree_dir), "-b", "feature", cwd=repo)
    assert projects.resolve_project(root, worktree_dir) == "my-cool-repo"


def test_resolve_project_no_match_returns_none(root: Path, tmp_path: Path) -> None:
    untracked = tmp_path / "untracked-dir"
    untracked.mkdir()
    assert projects.resolve_project(root, untracked) is None


def test_resolve_project_non_git_cwd_matches_by_prefix(root: Path, tmp_path: Path) -> None:
    plain_dir = tmp_path / "plain-project"
    plain_dir.mkdir()
    projects.register_project(root, plain_dir, slug="plain")
    nested = plain_dir / "nested"
    nested.mkdir()
    assert projects.resolve_project(root, nested) == "plain"


def test_resolve_project_longest_prefix_wins(root: Path, tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    projects.register_project(root, outer, slug="outer-proj")
    projects.register_project(root, inner, slug="inner-proj")
    assert projects.resolve_project(root, inner) == "inner-proj"
    assert projects.resolve_project(root, outer) == "outer-proj"


def test_git_common_root_none_for_non_git_dir(tmp_path: Path) -> None:
    assert projects.git_common_root(tmp_path) is None


# --- registry containment: the SELECTOR channel (Codex P2-SAFETY-SECURITY-010) ---
#
# The registry is not another document — it decides which project namespaces get
# walked at all. Round 4 guarded ``StoreHandle.load_registry`` only; round 5's probe
# walked past it through ``resolve_project`` → the low-level ``load_registry``, still
# resolving an attacker-selected slug on the capture, recall, status, curate, doctor
# and MCP paths, and ``register_project`` carried an external ``injected`` entry into
# the mapping handed to the writer. The guard therefore lives at this module's
# accessor boundary, which every registry read in the process reaches.


def _external_registry(root: Path, tmp_path: Path, body: str) -> Path:
    """A store whose ``registry.toml`` is a symlink to a valid registry OUTSIDE it.

    Returns the external file. It is deliberately *valid* TOML naming a *real* slug:
    a fixture the parser rejects would be dropped as malformed before containment was
    ever consulted, and would pass with the guard deleted — the vacuity that bit this
    review loop twice already."""
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    external = outside / "registry.toml"
    external.write_text(body, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    link = root / "registry.toml"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(external)
    return external


def test_registry_is_contained_for_an_ordinary_store(root: Path, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    projects.register_project(root, plain, slug="plain")
    assert projects.registry_is_contained(root) is True


def test_registry_is_contained_when_absent(root: Path) -> None:
    """Absent is not hostile: a missing registry resolves to its own would-be
    location, is contained, and reads as empty one layer down."""
    root.mkdir(parents=True)
    assert projects.registry_is_contained(root) is True
    assert projects.load_registry(root) == {}


def test_registry_is_contained_through_a_symlinked_store_root(root: Path, tmp_path: Path) -> None:
    """The failure mode of guarding a path built from ``root`` itself is refusing a
    whole LEGITIMATE store (macOS ``/var`` → ``/private/var``). Both sides resolve."""
    plain = tmp_path / "plain"
    plain.mkdir()
    projects.register_project(root, plain, slug="plain")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(root, target_is_directory=True)

    assert projects.registry_is_contained(linked_root) is True
    assert projects.load_registry(linked_root) == {"plain": [str(plain)]}


def test_registry_is_contained_through_a_symlinked_PARENT(root: Path, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    projects.register_project(root, plain, slug="plain")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(root.parent, target_is_directory=True)

    via_parent = linked_parent / root.name
    assert projects.registry_is_contained(via_parent) is True
    assert projects.load_registry(via_parent) == {"plain": [str(plain)]}


def test_registry_is_contained_for_a_relative_root(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    projects.register_project(root, plain, slug="plain")
    monkeypatch.chdir(tmp_path)

    relative = Path(root.name)
    assert projects.registry_is_contained(relative) is True
    assert projects.load_registry(relative) == {"plain": [str(plain)]}


def test_an_external_registry_is_not_contained(root: Path, tmp_path: Path) -> None:
    _external_registry(root, tmp_path, '[projects.injected]\nroots = ["/tmp/injected"]\n')
    assert projects.registry_is_contained(root) is False


def test_load_registry_reads_an_external_registry_as_empty(root: Path, tmp_path: Path) -> None:
    external = _external_registry(
        root, tmp_path, '[projects.injected]\nroots = ["/tmp/injected"]\n'
    )
    # The fixture really is a valid registry: read from its own directory it parses,
    # so this test cannot pass because the file was malformed.
    assert projects.load_registry(external.parent) == {"injected": ["/tmp/injected"]}
    assert projects.load_registry(root) == {}


def test_resolve_project_refuses_an_external_registrys_selection(
    root: Path, tmp_path: Path
) -> None:
    """**The round-5 bypass, verbatim.** ``handle.load_registry()`` returned ``{}``
    while ``resolve_project`` still resolved the attacker-selected slug, because it
    called the low-level reader directly."""
    target = tmp_path / "victim"
    target.mkdir()
    _external_registry(root, tmp_path, f'[projects.externally-selected]\nroots = ["{target}"]\n')

    assert projects.resolve_project(root, target) is None


def test_register_project_fails_closed_on_an_external_registry(root: Path, tmp_path: Path) -> None:
    """Read-for-rewrite must refuse *before* reading: fail-soft-to-empty here would
    both import the external entries and rewrite a file outside the store."""
    external = _external_registry(
        root, tmp_path, '[projects.injected]\nroots = ["/tmp/injected"]\n'
    )
    before = external.read_text(encoding="utf-8")
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(projects.RegistryNotContainedError):
        projects.register_project(root, plain, slug="plain")

    assert external.read_text(encoding="utf-8") == before, "the external file was rewritten"
    assert "plain" not in before


def test_write_registry_refuses_an_external_target(root: Path, tmp_path: Path) -> None:
    """The writer guards independently of the reader. ``register_project`` already
    fails one step earlier, so this covers a future second writer that would otherwise
    have to remember the rule."""
    external = _external_registry(root, tmp_path, '[projects.a]\nroots = ["/tmp/a"]\n')
    before = external.read_text(encoding="utf-8")

    with pytest.raises(projects.RegistryNotContainedError):
        projects._write_registry(root, {"attacker": ["/tmp/attacker"]})

    assert external.read_text(encoding="utf-8") == before


def test_a_registry_symlink_loop_never_raises_and_selects_nothing(root: Path) -> None:
    """A self-referential ``registry.toml``.

    The *containment verdict* is deliberately not asserted, because it is
    Python-version-dependent in exactly the way ``StoreHandle.contains`` already
    documents (``test_contains_never_raises_on_a_symlink_loop``): on ≤3.12
    ``Path.resolve()`` raises and the ``except`` branch returns False; on 3.13 it
    returns the loop path itself, which is under the root, so the verdict is True.
    Pinning either answer would make this test pass on one leg of the matrix and
    fail on the other — the py3.13-only failure this project has already been bitten
    by twice.

    What must hold on every version is what a caller can actually observe: no
    exception escapes into a read path (spec §13 fail-soft), and the loop selects
    nothing. On 3.13 that second guarantee comes from one layer down — ``exists()``
    is False for a loop, so the file reads as absent."""
    root.mkdir(parents=True)
    link = root / "registry.toml"
    link.symlink_to(link)

    assert isinstance(projects.registry_is_contained(root), bool)
    assert projects.load_registry(root) == {}
    assert projects.resolve_project(root, root) is None


def test_strict_read_refuses_an_external_registry_directly(root: Path, tmp_path: Path) -> None:
    """The read-for-rewrite guard, asserted at its own layer.

    Found by mutation: deleting this guard left every other test green, because
    ``register_project``'s *writer* refuses the same target a moment later and
    raises the same error. The observable outcome was identical while the
    dangerous behaviour — reading an external registry and importing its entries
    into the mapping handed to the writer — had come back. A guard whose only
    witness is a sibling guard is not tested."""
    _external_registry(root, tmp_path, '[projects.injected]\nroots = ["/tmp/injected"]\n')
    with pytest.raises(projects.RegistryNotContainedError):
        projects._read_registry(root)


def test_containment_is_checked_BEFORE_the_registry_is_read(root: Path, tmp_path: Path) -> None:
    """Ordering, made observable.

    The external registry here is **unparseable**. If containment is checked first
    the error is ``RegistryNotContainedError``; if the file is read first, TOML
    parsing fails and the error is ``TOMLDecodeError``. So the exception *type*
    witnesses the order — which is the whole requirement ("fail closed before
    reading or rewriting"), and is otherwise invisible because neither path
    produces an observable write."""
    _external_registry(root, tmp_path, "this is not valid TOML {{{\n")
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(projects.RegistryNotContainedError):
        projects.register_project(root, plain, slug="plain")


# (A third test asserting "external entries are never imported" was written and then
# DELETED: it unlinked the symlink before registering, so it exercised ordinary
# registration and passed with every guard removed. The import is not observable from
# outside — no write survives either way — so the ordering test above, which witnesses
# it through the exception type, is the real assertion. A test that cannot fail is
# worse than no test, because it reads like coverage.)
