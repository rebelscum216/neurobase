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


# --- display-safe config lookups (Phase P) -----------------------------------


def test_denylist_hit_names_the_entry_verbatim(tmp_path: Path) -> None:
    """The UI shows which configured entry matched, in the form the user wrote it."""
    repo = tmp_path / "umbrella" / "repo"
    repo.mkdir(parents=True)
    assert projects.denylist_hit(repo, [str(tmp_path / "umbrella")]) == str(tmp_path / "umbrella")
    assert projects.denylist_hit(repo, [str(tmp_path / "elsewhere")]) is None
    assert projects.denylist_hit(repo, []) is None


def test_config_hits_skip_relative_entries(tmp_path: Path) -> None:
    """A relative entry would resolve against the process cwd — non-deterministic
    scope (review F5), so it is dropped rather than honored."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert projects.denylist_hit(repo, ["relative/path"]) is None
    assert projects.auto_enable_hit(repo, ["relative/path"]) is None


def test_config_hits_do_not_match_a_sibling_by_string_prefix(tmp_path: Path) -> None:
    sibling = tmp_path / "Projects2"
    sibling.mkdir()
    assert projects.auto_enable_hit(sibling, [str(tmp_path / "Projects")]) is None


# --- Codex F2 (round 2): the registered path is the collapsed one ------------


def test_register_project_refuses_a_repo_at_the_store_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(projects.ProjectInsideStoreError):
        projects.register_project(root, root)
    assert projects.load_registry(root) == {}


def test_register_project_refuses_a_repo_under_the_store_root(root: Path) -> None:
    inside = root / "projects"
    inside.mkdir(parents=True)
    with pytest.raises(projects.ProjectInsideStoreError):
        projects.register_project(root, inside)
    assert projects.load_registry(root) == {}


def test_register_project_refuses_a_worktree_whose_common_root_is_the_store(
    root: Path, tmp_path: Path
) -> None:
    """The finding itself: the submitted path is outside the store, but the path
    that would actually be registered — its git common root — is the store."""
    root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", ".", cwd=root)
    (root / "f.txt").write_text("x", encoding="utf-8")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x", cwd=root)
    worktree = tmp_path / "outside-worktree"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=root)

    assert not worktree.resolve().is_relative_to(root.resolve())  # naive check passes
    with pytest.raises(projects.ProjectInsideStoreError):
        projects.register_project(root, worktree)
    assert projects.load_registry(root) == {}


def test_project_root_for_collapses_a_worktree_to_its_repo(root: Path, repo: Path) -> None:
    worktree = repo.parent / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "feature", cwd=repo)
    assert projects.project_root_for(worktree) == projects.git_common_root(repo)


def test_project_root_for_resolves_a_non_git_dir_to_itself(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert projects.project_root_for(plain) == plain.resolve()


def test_is_inside_store_is_the_predicate_form_of_the_refusal(root: Path, tmp_path: Path) -> None:
    assert projects.is_inside_store(root, root) is True
    assert projects.is_inside_store(root / "projects" / "x", root) is True
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert projects.is_inside_store(outside, root) is False
