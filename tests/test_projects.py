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
