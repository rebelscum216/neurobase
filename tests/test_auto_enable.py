"""Tests for folder-scoped auto-enable (prototype; pending ADR).

Covers three layers: the pure path policy (``projects.auto_enable_root_for``),
the shared resolve-or-register seam (``enable.resolve_or_auto_enable``), and an
end-to-end path through the Claude scribe so a repo under a configured
``auto_enable_root`` is captured with no prior ``neurobase enable``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from neurobase.adapters.claude import scribe
from neurobase.core import config as config_mod
from neurobase.core import projects, store
from neurobase.core.enable import resolve_or_auto_enable


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "T", cwd=path)
    (path / "README.md").write_text("hi")
    _git("add", "README.md", cwd=path)
    _git("commit", "-q", "-m", "init", cwd=path)
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An umbrella folder that will act as the auto_enable_root."""
    ws = tmp_path / "Projects"
    ws.mkdir()
    return ws


# --- policy: projects.auto_enable_root_for ---------------------------------


def test_policy_off_when_no_roots(workspace: Path) -> None:
    repo = _make_repo(workspace / "app")
    assert projects.auto_enable_root_for(repo, [], []) is None


def test_policy_matches_repo_under_root(workspace: Path) -> None:
    repo = _make_repo(workspace / "app")
    assert projects.auto_enable_root_for(repo, [str(workspace)], []) == repo.resolve()


def test_policy_matches_from_subdirectory(workspace: Path) -> None:
    """A cwd deep inside the repo still resolves to the repo's git root."""
    repo = _make_repo(workspace / "app")
    subdir = repo / "src" / "pkg"
    subdir.mkdir(parents=True)
    assert projects.auto_enable_root_for(subdir, [str(workspace)], []) == repo.resolve()


def test_policy_repo_outside_root_does_not_match(workspace: Path, tmp_path: Path) -> None:
    outside = _make_repo(tmp_path / "elsewhere" / "app")
    assert projects.auto_enable_root_for(outside, [str(workspace)], []) is None


def test_policy_non_git_dir_never_matches(workspace: Path) -> None:
    """Auto-enable is git-repo-scoped: a plain folder under the root (even the
    umbrella folder itself) is never captured as one giant project."""
    plain = workspace / "notes"
    plain.mkdir()
    assert projects.auto_enable_root_for(plain, [str(workspace)], []) is None
    assert projects.auto_enable_root_for(workspace, [str(workspace)], []) is None


def test_policy_denylist_wins_over_root(workspace: Path) -> None:
    client = workspace / "client-work"
    repo = _make_repo(client / "secret-app")
    # Repo is under the auto_enable_root, but also under a denylisted subtree.
    assert projects.auto_enable_root_for(repo, [str(workspace)], [str(client)]) is None
    # Remove the denylist and it qualifies again.
    assert projects.auto_enable_root_for(repo, [str(workspace)], []) == repo.resolve()


def test_policy_tilde_and_missing_paths_are_safe(workspace: Path) -> None:
    repo = _make_repo(workspace / "app")
    # A non-existent configured root matches nothing rather than raising.
    assert projects.auto_enable_root_for(repo, ["~/does-not-exist-xyz"], []) is None


# --- seam: enable.resolve_or_auto_enable -----------------------------------


def test_resolve_returns_existing_without_registering(workspace: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    projects.register_project(root, repo, slug="app")
    store.ensure_tree("app", root)
    # Already registered → returns the slug, no auto-enable path taken.
    assert (
        resolve_or_auto_enable(root, repo, auto_enable_roots=[str(workspace)], denylist=[]) == "app"
    )


def test_resolve_auto_registers_and_creates_tree(workspace: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    slug = resolve_or_auto_enable(root, repo, auto_enable_roots=[str(workspace)], denylist=[])
    assert slug == "app"
    # Registered in the registry...
    assert projects.load_registry(root)["app"] == [str(repo.resolve())]
    # ...and given a memory tree (opt-in downstream now passes).
    assert store.memory_dir("app", root).exists()


def test_resolve_untracked_non_qualifying_is_none_and_writes_nothing(
    workspace: Path, tmp_path: Path
) -> None:
    root = tmp_path / "store"
    outside = _make_repo(tmp_path / "elsewhere" / "app")
    assert (
        resolve_or_auto_enable(root, outside, auto_enable_roots=[str(workspace)], denylist=[])
        is None
    )
    # A non-qualifying resolution only READ-inspects — it must not create store.toml.
    assert not (root / "store.toml").exists()
    assert not (root / "registry.toml").exists()


def test_resolve_fails_closed_on_too_new_store(workspace: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    (root / "store.toml").write_text(f"schema = {store.STORE_SCHEMA_VERSION + 1}\n")
    repo = _make_repo(workspace / "app")
    # A store newer than we support must fail closed (→ None), never register.
    assert (
        resolve_or_auto_enable(root, repo, auto_enable_roots=[str(workspace)], denylist=[]) is None
    )
    assert not (root / "registry.toml").exists()


def test_resolve_skips_on_slug_collision(workspace: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    # Pre-register a *different* repo under the slug "app".
    other = _make_repo(tmp_path / "other-place" / "app")
    projects.register_project(root, other, slug="app")
    store.ensure_tree("app", root)
    # A new repo that would derive the same slug must not be auto-guessed.
    collides = _make_repo(workspace / "app")
    assert (
        resolve_or_auto_enable(root, collides, auto_enable_roots=[str(workspace)], denylist=[])
        is None
    )
    # The colliding repo was not added to the existing slug's roots.
    assert projects.load_registry(root)["app"] == [str(other.resolve())]


# --- integration: Claude scribe honors the config ---------------------------

# The §11.1 fixture shape (a single typed prompt + an assistant reply). `cwd` is
# overridden by the scribe call, so the transcript's cwd here is irrelevant.
_FIXTURE_EVENTS = [
    {
        "type": "user",
        "isSidechain": False,
        "cwd": "/whatever",
        "gitBranch": "main",
        "sessionId": "deadbeef",
        "message": {"role": "user", "content": "Fix the login bug"},
    },
    {
        "type": "assistant",
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done — added the null check"}],
        },
    },
]


def _write_config(path: Path, roots: list[str], denylist: list[str]) -> None:
    roots_toml = ", ".join(f'"{r}"' for r in roots)
    deny_toml = ", ".join(f'"{d}"' for d in denylist)
    path.write_text(
        f"[enable]\nauto_enable_roots = [{roots_toml}]\ndenylist = [{deny_toml}]\n",
        encoding="utf-8",
    )


def test_scribe_auto_enables_repo_under_configured_root(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    cfg = tmp_path / "config.toml"
    _write_config(cfg, roots=[str(workspace)], denylist=[])
    monkeypatch.setattr(config_mod, "config_path", lambda: cfg)

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in _FIXTURE_EVENTS), encoding="utf-8")

    # The repo was never `neurobase enable`d, yet capture lands because it sits
    # under the auto_enable_root.
    written = scribe.scribe(root, transcript_path=transcript, cwd=str(repo), reason="x")
    assert written is not None
    assert written.exists()
    assert store.memory_dir("app", root).exists()
    raws = projects.load_registry(root)
    assert "app" in raws


def test_scribe_denylisted_repo_is_not_captured(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    secret_area = workspace / "client-work"
    repo = _make_repo(secret_area / "secret-app")
    cfg = tmp_path / "config.toml"
    _write_config(cfg, roots=[str(workspace)], denylist=[str(secret_area)])
    monkeypatch.setattr(config_mod, "config_path", lambda: cfg)

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in _FIXTURE_EVENTS), encoding="utf-8")

    assert scribe.scribe(root, transcript_path=transcript, cwd=str(repo), reason="x") is None
    assert not (root / "store.toml").exists()


def test_scribe_without_config_stays_opt_in(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: with no auto_enable_roots, an unregistered repo still no-ops
    (today's per-repo opt-in behavior is unchanged)."""
    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    cfg = tmp_path / "config.toml"  # absent → all defaults (empty roots)
    monkeypatch.setattr(config_mod, "config_path", lambda: cfg)

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in _FIXTURE_EVENTS), encoding="utf-8")

    assert scribe.scribe(root, transcript_path=transcript, cwd=str(repo), reason="x") is None
    assert not (root / "store.toml").exists()
