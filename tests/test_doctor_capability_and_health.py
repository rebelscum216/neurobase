"""`doctor`'s two post-2026-07-27 checks: hook capabilities, and curate health.

Both exist because `doctor` reported all-green while the machine was in an unsafe
state — the hooks ran a shim three weeks older than the guards it was assumed to
have, and two status nodes had been frozen for 7 and 11 days with every failure
recorded only in a log nothing read.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from neurobase.cli import diagnostics
from neurobase.core import capabilities

SHIM = "/usr/local/bin/neurobase"


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _runner_returning(payload: object, returncode: int = 0):
    text = payload if isinstance(payload, str) else json.dumps(payload)

    def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(text, returncode)

    return _run


def _current_build_payload() -> dict:
    return capabilities.describe()


def _write_claude_start_hook(home: Path, executable: str) -> None:
    settings = home / ".claude" / "settings.json"
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


# --- hook capability gate ---------------------------------------------------


def test_no_startup_hooks_needs_no_capabilities(home: Path, tmp_path: Path) -> None:
    """Capture-only installs are not gated — `SessionEnd`/`Stop` never spawn."""
    checks = diagnostics._capability_checks(tmp_path, run=_runner_returning({}))
    assert [c.status for c in checks] == ["ok"]
    assert "no startup hooks enabled" in checks[0].detail


def test_current_build_satisfies_the_profile(home: Path, tmp_path: Path) -> None:
    _write_claude_start_hook(home, SHIM)
    checks = diagnostics._capability_checks(
        tmp_path, run=_runner_returning(_current_build_payload())
    )
    assert [c.status for c in checks] == ["ok"]
    assert capabilities.PROFILE in checks[0].detail


def test_stale_shim_without_the_command_is_an_error(home: Path, tmp_path: Path) -> None:
    """**The 2026-07-27 regression.**

    A build predating the profile has no `capabilities` subcommand, so the probe
    exits non-zero. That must be an error, not a shrug: this is the exact state
    that let a 2026-07-09 shim be certified healthy while it ran unguarded.
    """
    _write_claude_start_hook(home, "/opt/old/bin/neurobase")
    checks = diagnostics._capability_checks(
        tmp_path, run=_runner_returning("Usage: neurobase [OPTIONS]", returncode=2)
    )
    assert [c.status for c in checks] == ["error"]
    detail = checks[0].detail
    assert "/opt/old/bin/neurobase" in detail
    for required in capabilities.REQUIRED_FOR_STARTUP_HOOK:
        assert required in detail
    assert checks[0].remedy is not None and "uv tool install" in checks[0].remedy


def test_missing_executable_is_an_error(home: Path, tmp_path: Path) -> None:
    _write_claude_start_hook(home, "/nope/neurobase")

    def _explode(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("no such executable")

    checks = diagnostics._capability_checks(tmp_path, run=_explode)
    assert [c.status for c in checks] == ["error"]


def test_probe_timeout_is_an_error_not_a_hang(home: Path, tmp_path: Path) -> None:
    _write_claude_start_hook(home, SHIM)

    def _timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="neurobase", timeout=10)

    checks = diagnostics._capability_checks(tmp_path, run=_timeout)
    assert [c.status for c in checks] == ["error"]


def test_unparseable_probe_output_is_an_error(home: Path, tmp_path: Path) -> None:
    _write_claude_start_hook(home, SHIM)
    checks = diagnostics._capability_checks(tmp_path, run=_runner_returning("not json at all"))
    assert [c.status for c in checks] == ["error"]


def test_partial_capabilities_name_only_what_is_missing(home: Path, tmp_path: Path) -> None:
    """A build with *some* guards is still unsafe, and the report says which."""
    _write_claude_start_hook(home, SHIM)
    partial = {
        "profile": "automatic-curation-safety-v1",
        "provides": [capabilities.HOOK_REENTRANCY_SUPPRESSION],
    }
    checks = diagnostics._capability_checks(tmp_path, run=_runner_returning(partial))
    assert [c.status for c in checks] == ["error"]
    assert capabilities.CURATE_SINGLE_FLIGHT in checks[0].detail
    assert capabilities.HOOK_REENTRANCY_SUPPRESSION not in checks[0].detail.split("is missing")[1]


def test_executable_path_with_spaces_is_extracted(home: Path, tmp_path: Path) -> None:
    """The command is split on `" hook "`, not whitespace, so an installed path
    containing a space still resolves to the right executable."""
    spaced = "/Users/a b/.local/bin/neurobase"
    _write_claude_start_hook(home, spaced)
    found = diagnostics._startup_hook_executables(tmp_path)
    assert found == {"claude SessionStart": spaced}


def test_probe_asks_the_executable_under_test_not_the_diagnosing_build(
    home: Path, tmp_path: Path
) -> None:
    """The requirement comes from *this* build; the probe only ever asks the hook's
    own executable what it provides."""
    _write_claude_start_hook(home, "/opt/old/bin/neurobase")
    seen: list[list[str]] = []

    def _record(argv, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        return _completed(json.dumps(_current_build_payload()))

    diagnostics._capability_checks(tmp_path, run=_record)
    assert seen == [["/opt/old/bin/neurobase", "capabilities"]]


# --- curate health ----------------------------------------------------------


def _enabled_store(tmp_path: Path, entries: list[dict]) -> tuple[Path, Path]:
    """A store with one enabled project whose curator log holds ``entries``."""
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir()
    memory = root / "projects" / "p" / "memory"
    memory.mkdir(parents=True)
    (root / "store.toml").write_text("schema = 1\n", encoding="utf-8")
    (root / "registry.toml").write_text(
        f'[projects.p]\nroots = [\n    "{repo}",\n]\n', encoding="utf-8"
    )
    log = memory / ".curator-log.jsonl"
    log.write_text(
        "".join(json.dumps(e) + "\n" for e in entries),
        encoding="utf-8",
    )
    return root, repo


def test_healthy_curate_reports_last_success(tmp_path: Path) -> None:
    root, repo = _enabled_store(
        tmp_path,
        [
            {"at": "2026-07-26T10:00:00Z", "status": "error", "error": "transient"},
            {"at": "2026-07-27T10:00:00Z", "status": "ok"},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok"
    assert "2026-07-27T10:00:00Z" in check.detail


def test_two_failures_since_success_warns(tmp_path: Path) -> None:
    root, repo = _enabled_store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok"},
            {"at": "2026-07-26T10:00:00Z", "status": "error", "error": "claude -p timed out"},
            {"at": "2026-07-27T10:00:00Z", "status": "error", "error": "claude -p timed out"},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "2 failed pass" in check.detail
    assert "claude -p timed out" in check.detail


def test_three_failures_since_success_is_an_error(tmp_path: Path) -> None:
    """The reporting machine sat at 1,282 consecutive failures while doctor was
    green. Three is where a provider hiccup stops explaining it."""
    root, repo = _enabled_store(
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
    root, repo = _enabled_store(
        tmp_path,
        [{"at": "2026-07-27T10:00:00Z", "status": "error", "error": "boom"}],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "error"
    assert "never" in check.detail or "no curate pass has ever succeeded" in check.detail


def test_no_log_yet_is_fine(tmp_path: Path) -> None:
    root, repo = _enabled_store(tmp_path, [])
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok"


def test_unenabled_project_is_not_a_curate_problem(tmp_path: Path) -> None:
    root, _repo = _enabled_store(tmp_path, [{"at": "x", "status": "ok"}])
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    check = diagnostics._curate_health_check(root, elsewhere)
    assert check.status == "ok"
    assert "not an enabled project" in check.detail


def test_corrupt_log_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    """doctor is a read-only reporting surface — a half-written log line must
    never crash it."""
    root, repo = _enabled_store(tmp_path, [{"at": "2026-07-27T10:00:00Z", "status": "ok"}])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    with log.open("a", encoding="utf-8") as fh:
        fh.write("{ truncated mid-writ\n")
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok"
