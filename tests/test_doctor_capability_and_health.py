"""`doctor`'s two post-2026-07-27 checks: hook capabilities, and curate health.

Both exist because `doctor` reported all-green while the machine was unsafe — the
hooks ran a shim three weeks older than the guards it was assumed to have, and two
status nodes had been frozen for 7 and 11 days with every failure recorded only in
a log nothing read.

Every finding from review round 1 has a named regression here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neurobase.cli import diagnostics
from neurobase.core import capabilities

CURRENT = "/opt/current/bin/neurobase"
STALE = "/opt/stale/bin/neurobase"


# --- fake installs: a real package layout, so nothing is stubbed ------------


def _install(root: Path, *, provides: list[str] | None) -> str:
    """A console script beside a `site-packages/neurobase/`, as uv/venv/pipx lay
    it out. ``provides=None`` ships **no manifest** — a build predating the
    profile, which is the case that matters."""
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = bin_dir / "neurobase"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True, exist_ok=True)
    if provides is not None:
        (package / capabilities.MANIFEST_NAME).write_text(
            json.dumps({"profile": capabilities.PROFILE, "provides": provides}),
            encoding="utf-8",
        )
    return str(executable)


def _current_install(tmp_path: Path) -> str:
    return _install(tmp_path / "current", provides=sorted(capabilities.PROVIDES))


def _stale_install(tmp_path: Path) -> str:
    return _install(tmp_path / "stale", provides=None)


def _write_claude_hook(home: Path, executable: str, *, user: bool, repo: Path) -> None:
    base = home if user else repo
    settings = base / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup|clear",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{executable} hook claude session-start",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    return r


# --- manifest reading (never execution) -------------------------------------


def test_manifest_is_read_from_the_installs_package(tmp_path: Path) -> None:
    executable = _current_install(tmp_path)
    assert capabilities.provided_by(executable) == capabilities.PROVIDES


def test_install_without_a_manifest_provides_nothing(tmp_path: Path) -> None:
    """A build predating the profile ships no manifest — absence is the signal,
    and it cannot be faked in the dangerous direction."""
    assert capabilities.provided_by(_stale_install(tmp_path)) == frozenset()


def test_missing_executable_provides_nothing(tmp_path: Path) -> None:
    assert capabilities.provided_by(tmp_path / "nope" / "neurobase") == frozenset()


def test_malformed_manifest_provides_nothing(tmp_path: Path) -> None:
    executable = _install(tmp_path / "broken", provides=[])
    package = tmp_path / "broken" / "lib" / "python3.14" / "site-packages" / "neurobase"
    (package / capabilities.MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    assert capabilities.provided_by(executable) == frozenset()


# --- R1-F1 (blocker): doctor must never execute a configured command --------


def test_foreign_command_is_never_collected(home: Path, repo: Path) -> None:
    """**Round-1 blocker regression.**

    An unwired, repo-local `.codex/hooks.json` naming an arbitrary binary was
    collected and executed, creating a file on disk. It must now fail the
    ownership predicate and never be inspected at all.
    """
    hooks = repo / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/bin/touch hook codex session-start",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert diagnostics._startup_hook_executables(repo) == []
    assert [c.status for c in diagnostics._capability_checks(repo)] == ["ok"]


def test_unwired_codex_project_hooks_are_not_enabled(home: Path, repo: Path) -> None:
    """Codex only discovers a project hooks file that `config.toml` wires *and*
    trusts. An inert file is not an enabled hook."""
    hooks = repo / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{STALE} hook codex session-start",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert diagnostics._startup_hook_executables(repo) == []


# --- R1-F2 (major): every scope, not one per agent --------------------------


def test_stale_user_hook_beside_current_project_hook_is_reported(
    tmp_path: Path, home: Path, repo: Path
) -> None:
    """**Round-1 F2 regression.**

    Claude runs matching hooks from *both* scopes. Keying by agent and
    `setdefault`-ing hid the stale executable behind the current one's green
    check — a false green on exactly the condition the gate exists to catch.
    """
    current = _current_install(tmp_path)
    stale = _stale_install(tmp_path)
    _write_claude_hook(home, current, user=False, repo=repo)
    _write_claude_hook(home, stale, user=True, repo=repo)

    collected = diagnostics._startup_hook_executables(repo)
    assert {executable for _label, executable in collected} == {current, stale}

    checks = diagnostics._capability_checks(repo)
    assert sorted(c.status for c in checks) == ["error", "ok"]
    failing = next(c for c in checks if c.status == "error")
    assert stale in failing.detail
    for required in capabilities.REQUIRED_FOR_STARTUP_HOOK:
        assert required in failing.detail


def test_identical_executable_in_both_scopes_reports_once(
    tmp_path: Path, home: Path, repo: Path
) -> None:
    current = _current_install(tmp_path)
    _write_claude_hook(home, current, user=False, repo=repo)
    _write_claude_hook(home, current, user=True, repo=repo)
    checks = diagnostics._capability_checks(repo)
    assert [c.status for c in checks] == ["ok"]
    assert "project" in checks[0].detail and "user" in checks[0].detail


def test_no_startup_hooks_needs_no_capabilities(home: Path, repo: Path) -> None:
    checks = diagnostics._capability_checks(repo)
    assert [c.status for c in checks] == ["ok"]
    assert "no startup hooks enabled" in checks[0].detail


def test_current_install_satisfies_the_profile(tmp_path: Path, home: Path, repo: Path) -> None:
    _write_claude_hook(home, _current_install(tmp_path), user=True, repo=repo)
    checks = diagnostics._capability_checks(repo)
    assert [c.status for c in checks] == ["ok"]
    assert capabilities.PROFILE in checks[0].detail


def test_partial_capabilities_name_only_what_is_missing(
    tmp_path: Path, home: Path, repo: Path
) -> None:
    executable = _install(tmp_path / "partial", provides=[capabilities.HOOK_REENTRANCY_SUPPRESSION])
    _write_claude_hook(home, executable, user=True, repo=repo)
    checks = diagnostics._capability_checks(repo)
    assert [c.status for c in checks] == ["error"]
    missing_clause = checks[0].detail.split("is missing")[1]
    assert capabilities.CURATE_SINGLE_FLIGHT in missing_clause
    assert capabilities.HOOK_REENTRANCY_SUPPRESSION not in missing_clause


def test_executable_path_with_spaces_is_extracted(tmp_path: Path, home: Path, repo: Path) -> None:
    spaced = _install(tmp_path / "a b", provides=sorted(capabilities.PROVIDES))
    assert " " in spaced
    _write_claude_hook(home, spaced, user=True, repo=repo)
    collected = diagnostics._startup_hook_executables(repo)
    assert [executable for _label, executable in collected] == [spaced]


# --- R1-F3 (major): the curator's real status vocabulary --------------------


def _store(tmp_path: Path, entries: list[dict]) -> tuple[Path, Path]:
    root = tmp_path / "store"
    repo = tmp_path / "curated-repo"
    repo.mkdir()
    memory = root / "projects" / "p" / "memory"
    memory.mkdir(parents=True)
    (root / "store.toml").write_text("schema = 1\n", encoding="utf-8")
    (root / "registry.toml").write_text(
        f'[projects.p]\nroots = [\n    "{repo}",\n]\n', encoding="utf-8"
    )
    (memory / ".curator-log.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )
    return root, repo


def test_noops_after_success_are_not_failures(tmp_path: Path) -> None:
    """**Round-1 F3 regression.** Three noops after an `ok` were reported as
    "3 failed pass(es)" with a non-zero exit, though nothing failed."""
    root, repo = _store(
        tmp_path,
        [{"at": "2026-07-27T10:00:00Z", "status": "ok"}]
        + [{"at": f"2026-07-27T1{i}:00:00Z", "status": "noop", "raw": 0} for i in range(1, 4)],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok"


def test_resynth_counts_as_recovery(tmp_path: Path) -> None:
    """A successful regeneration clears the frozen node, so it clears health."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok"},
            {"at": "2026-07-21T10:00:00Z", "status": "error", "error": "boom"},
            {"at": "2026-07-22T10:00:00Z", "status": "resynth", "active_facts": 12},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok"
    assert "2026-07-22T10:00:00Z" in check.detail


def test_skipped_locked_is_neutral(tmp_path: Path) -> None:
    """`skipped-locked` is the single-flight guard working — not a failure."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-27T10:00:00Z", "status": "ok"},
            {"at": "2026-07-27T11:00:00Z", "status": "skipped-locked", "reason": "held"},
        ],
    )
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_trailing_noop_does_not_mask_the_real_failure_reason(tmp_path: Path) -> None:
    """A noop landing after real failures must not become the displayed reason."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok"},
            {"at": "2026-07-21T10:00:00Z", "status": "error", "error": "claude -p timed out"},
            {"at": "2026-07-22T10:00:00Z", "status": "noop", "raw": 0},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "claude -p timed out" in check.detail
    assert "1 failed pass" in check.detail


def test_partial_is_a_failure(tmp_path: Path) -> None:
    """`partial` consumes raws then dies before synthesis — exactly the shape
    that leaves a node frozen while facts move on."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok"},
            {"at": "2026-07-21T10:00:00Z", "status": "partial", "error": "budget exhausted"},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "1 failed pass" in check.detail


def test_unknown_future_status_is_neutral(tmp_path: Path) -> None:
    """A newer engine's status must not make an older doctor cry wolf."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok"},
            {"at": "2026-07-21T10:00:00Z", "status": "some-future-outcome"},
        ],
    )
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_three_failures_since_success_is_an_error(tmp_path: Path) -> None:
    root, repo = _store(
        tmp_path,
        [{"at": "2026-07-20T10:00:00Z", "status": "ok"}]
        + [
            {"at": f"2026-07-2{d}T10:00:00Z", "status": "error", "error": "claude -p exited 1"}
            for d in range(4, 7)
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "error"
    assert "3 failed pass" in check.detail


def test_never_succeeded_is_an_error(tmp_path: Path) -> None:
    root, repo = _store(tmp_path, [{"at": "2026-07-27T10:00:00Z", "status": "error", "error": "x"}])
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "error"
    assert "has ever succeeded" in check.detail


def test_only_noops_ever_is_not_an_error(tmp_path: Path) -> None:
    """An enabled project that has never had anything to curate is healthy."""
    root, repo = _store(tmp_path, [{"at": "2026-07-27T10:00:00Z", "status": "noop", "raw": 0}])
    assert diagnostics._curate_health_check(root, repo).status == "ok"


# --- R1-F4 (major): unknown history must not read as healthy ----------------


def test_unreadable_log_warns_rather_than_reporting_healthy(tmp_path: Path) -> None:
    """**Round-1 F4 regression.** An unreadable log collapsed into "no curate
    passes recorded" — restoring the all-green blindness this check prevents.

    A directory standing in for the log raises `OSError` on every platform
    (`IsADirectoryError` on POSIX, `PermissionError` on Windows), so this needs
    no monkeypatching and stays honest on the Windows leg of the matrix.
    """
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    log.unlink()
    log.mkdir()

    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "unreadable" in check.detail


def test_wholly_unparseable_log_warns(tmp_path: Path) -> None:
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    log.write_text("not json\nalso not json\n", encoding="utf-8")
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "no readable records" in check.detail


def test_torn_trailing_line_stays_fail_soft(tmp_path: Path) -> None:
    """The writer appends, so the last line can be mid-write. That single torn
    record must not turn a healthy history into a warning."""
    root, repo = _store(tmp_path, [{"at": "2026-07-27T10:00:00Z", "status": "ok"}])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    with log.open("a", encoding="utf-8") as fh:
        fh.write('{"at": "2026-07-27T11:00:00Z", "stat\n')
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_missing_log_is_fine(tmp_path: Path) -> None:
    root, repo = _store(tmp_path, [])
    (root / "projects" / "p" / "memory" / ".curator-log.jsonl").unlink()
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_unenabled_project_is_not_a_curate_problem(tmp_path: Path) -> None:
    root, _repo = _store(tmp_path, [{"at": "x", "status": "ok"}])
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    check = diagnostics._curate_health_check(root, elsewhere)
    assert check.status == "ok"
    assert "not an enabled project" in check.detail
