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


# --- F3: recall (session-start) is the ADR's *primary* trigger --------------


def test_recall_auto_enables_at_session_start(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D42: recall creates the tree on the first session in a qualifying repo and
    injects nothing that run (no nodes yet); by the next session it injects."""
    from neurobase.adapters import recall_common

    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    cfg = tmp_path / "config.toml"
    _write_config(cfg, roots=[str(workspace)], denylist=[])
    monkeypatch.setattr(config_mod, "config_path", lambda: cfg)

    # First session-start: registers + creates the tree, but injects nothing.
    assert recall_common.emit(root, repo) is None
    assert store.memory_dir("app", root).exists()
    assert "app" in projects.load_registry(root)

    # Once a node exists, the next session injects it.
    store.write_node(root, "app", "status", "# Status\n\nauth.py had a null-check bug.")
    out = recall_common.emit(root, repo)
    assert out is not None
    assert "additionalContext" in out


def test_recall_read_only_path_never_auto_enables(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: build_context's default (read-only — the surface the MCP `recall`
    prompt uses) must never register a project or create a tree; only emit
    (auto_enable=True) does."""
    from neurobase.adapters import recall_common

    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    cfg = tmp_path / "config.toml"
    _write_config(cfg, roots=[str(workspace)], denylist=[])
    monkeypatch.setattr(config_mod, "config_path", lambda: cfg)

    assert recall_common.build_context(root, repo) is None  # default auto_enable=False
    assert not (root / "registry.toml").exists()
    assert not (root / "store.toml").exists()


# --- F3: Codex scribe backstop (separate copy — needs its own test) ---------


def _codex_rollout(path: Path, cwd: str) -> Path:
    events = [
        {
            "type": "session_meta",
            "payload": {
                "session_id": "019auto",
                "id": "019auto",
                "timestamp": "2026-07-05T23:21:06Z",
                "cwd": cwd,
                "git": {"branch": "main"},
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Fix the login bug", "images": []},
        },
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "Done — null check"}},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def test_codex_scribe_auto_enables_repo_under_configured_root(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from neurobase.adapters.codex import scribe as codex_scribe

    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    cfg = tmp_path / "config.toml"
    _write_config(cfg, roots=[str(workspace)], denylist=[])
    monkeypatch.setattr(config_mod, "config_path", lambda: cfg)

    rollout = _codex_rollout(tmp_path / "rollout.jsonl", str(repo))
    written = codex_scribe.scribe(root, rollout_path=rollout, cwd=str(repo))
    assert written is not None
    assert store.memory_dir("app", root).exists()
    assert "app" in projects.load_registry(root)


# --- F4: denylist is a *live* gate (revokes an already-enabled repo) ---------


def test_resolve_denylisted_registered_repo_stops(workspace: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    projects.register_project(root, repo, slug="app")
    store.ensure_tree("app", root)
    # Registered — but adding it to the denylist revokes capture (live gate).
    assert (
        resolve_or_auto_enable(
            root, repo, auto_enable_roots=[str(workspace)], denylist=[str(workspace / "app")]
        )
        is None
    )
    # Without the denylist entry it resolves normally.
    assert (
        resolve_or_auto_enable(root, repo, auto_enable_roots=[str(workspace)], denylist=[]) == "app"
    )


def test_scribe_denylisting_an_enabled_repo_stops_capture(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4: a repo that was already enabled stops capturing once denylisted —
    editing one config line revokes capture, as ADR-0019 promises."""
    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    projects.register_project(root, repo, slug="app")
    store.ensure_tree("app", root)
    cfg = tmp_path / "config.toml"
    _write_config(cfg, roots=[str(workspace)], denylist=[str(repo)])
    monkeypatch.setattr(config_mod, "config_path", lambda: cfg)

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in _FIXTURE_EVENTS), encoding="utf-8")
    assert scribe.scribe(root, transcript_path=transcript, cwd=str(repo), reason="x") is None


# --- F5: relative config paths are skipped (non-deterministic scope) ---------


def test_policy_relative_paths_are_skipped(workspace: Path) -> None:
    repo = _make_repo(workspace / "app")
    # A relative root would resolve against the hook's cwd — skip it, don't enable.
    assert projects.auto_enable_root_for(repo, ["some/relative/dir"], []) is None
    # A junk relative denylist entry must not block a valid absolute root.
    assert (
        projects.auto_enable_root_for(repo, [str(workspace)], ["relative/deny"]) == repo.resolve()
    )


def test_is_denylisted_skips_relative_entries(workspace: Path) -> None:
    repo = _make_repo(workspace / "app")
    assert projects.is_denylisted(repo, ["app"]) is False  # relative → skipped
    assert projects.is_denylisted(repo, [str(workspace / "app")]) is True


# --- F2: partial-failure safety (no poisoning, no store.toml on a skip) ------


def test_resolve_tree_failure_leaves_no_registration(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tree-before-register: if ensure_tree fails, nothing is registered — so a
    one-time FS error can't leave a registered-but-treeless repo that matches
    resolve_project forever and never gets retried (review F2)."""
    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")

    def boom(project: str, r: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(store, "ensure_tree", boom)
    assert (
        resolve_or_auto_enable(root, repo, auto_enable_roots=[str(workspace)], denylist=[]) is None
    )
    assert "app" not in projects.load_registry(root)  # not registered → retryable


def test_resolve_unsluggable_repo_writes_nothing(tmp_path: Path) -> None:
    """A qualifying repo whose name can't be slugified skips out *before* the WRITE
    handle opens — a pristine store stays pristine (review B3)."""
    root = tmp_path / "store"
    ws = tmp_path / "ws"
    ws.mkdir()
    bad = _make_repo(ws / "!!!")
    assert resolve_or_auto_enable(root, bad, auto_enable_roots=[str(ws)], denylist=[]) is None
    assert not (root / "store.toml").exists()
    assert not (root / "registry.toml").exists()


# --- F3: worktree collapse, sibling prefix, scribe-surface fail-closed -------


def test_worktree_collapses_to_main_project(workspace: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = _make_repo(workspace / "app")
    assert (
        resolve_or_auto_enable(root, repo, auto_enable_roots=[str(workspace)], denylist=[]) == "app"
    )
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), "-b", "feature", cwd=repo)
    # The linked worktree resolves to the SAME project; no second registration.
    assert (
        resolve_or_auto_enable(root, wt, auto_enable_roots=[str(workspace)], denylist=[]) == "app"
    )
    assert list(projects.load_registry(root).keys()) == ["app"]


def test_policy_sibling_prefix_does_not_match(tmp_path: Path) -> None:
    projects_dir = tmp_path / "Projects"
    projects_dir.mkdir()
    repo = _make_repo(tmp_path / "Projects2" / "app")  # sibling, NOT under Projects
    assert projects.auto_enable_root_for(repo, [str(projects_dir)], []) is None


def test_scribe_fails_closed_on_too_new_store(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    root.mkdir()
    (root / "store.toml").write_text(f"schema = {store.STORE_SCHEMA_VERSION + 1}\n")
    repo = _make_repo(workspace / "app")
    cfg = tmp_path / "config.toml"
    _write_config(cfg, roots=[str(workspace)], denylist=[])
    monkeypatch.setattr(config_mod, "config_path", lambda: cfg)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in _FIXTURE_EVENTS), encoding="utf-8")
    # A too-new store fails closed at the scribe surface — no capture, no registry.
    assert scribe.scribe(root, transcript_path=transcript, cwd=str(repo), reason="x") is None
    assert not (root / "registry.toml").exists()


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
