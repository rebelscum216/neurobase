"""The detached session-end pass must be REPORTED, not just spawned.

The guarantee under test is the one that distinguishes the shipped design from
fire-and-forget (G16): a pass that is spawned and never comes back is *detected*.
Every test here is written so that removing the mechanism makes it fail — a green
run that would stay green with the marker deleted would be testing nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.adapters import recall_common
from neurobase.brain.base import BrainError
from neurobase.cli import app, diagnostics
from neurobase.core import spawn_marker, store
from neurobase.core.config import Config
from neurobase.core.store_handle import StoreMode, open_store

runner = CliRunner()

PROJECT = "myrepo"


class _FakeBrain:
    name = "fake"

    def plan_json(self, system: str, user: str) -> dict:
        return {
            "upserts": [{"slug": "fact-a", "body": "durable fact", "from_raw": ["r1.md"]}],
            "tombstones": [],
        }

    def text(self, system: str, user: str) -> str:
        return "# Node\n\ncurrent work"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    repo = tmp_path / PROJECT
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    root = tmp_path / "store"
    runner.invoke(app, ["enable", "--root", str(root), "--cwd", str(repo)])
    monkeypatch.setattr(cli, "resolve_brain", lambda config: (_FakeBrain(), None))
    return root, repo


def _write_raw(root: Path, name: str = "r1.md") -> None:
    """A raw old enough that `--if-stale` would fold it."""
    store.write_doc(
        store.memory_dir(PROJECT, root) / "raw" / name,
        {
            "agent": "claude",
            "session_id": "s1",
            "cwd": "/x",
            "branch": "main",
            "captured_at": "2026-07-07T12:00:00Z",
            "consumed": False,
        },
        "fix the login bug",
    )


def _handle(root: Path):
    """The tests address the store the way production does — through a handle."""
    return open_store(root, StoreMode.DOCTOR)


def _spawn(root: Path, *, trigger: str) -> str:
    """Write a marker and assert it exists — a `None` id threaded onward would
    make every downstream assertion vacuous."""
    spawn_id = spawn_marker.write(_handle(root), PROJECT, trigger=trigger)
    assert spawn_id, "the marker must have been written"
    return spawn_id


def _marker_dir(root: Path) -> Path:
    return store.memory_dir(PROJECT, root) / spawn_marker.SPAWN_DIR


def _markers(root: Path) -> list[str]:
    target = _marker_dir(root)
    return sorted(path.stem for path in target.glob("*.json")) if target.exists() else []


def _age(root: Path, spawn_id: str, *, hours: float) -> None:
    """Rewrite a marker's own timestamp so it reads as ``hours`` old."""
    path = _marker_dir(root) / f"{spawn_id}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["at"] = (datetime.now(UTC) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# The marker itself
# --------------------------------------------------------------------------


def test_write_records_the_intent(enabled: tuple[Path, Path]) -> None:
    root, _repo = enabled
    spawn_id = spawn_marker.write(_handle(root), PROJECT, trigger="session-end")

    assert spawn_id
    record = json.loads((_marker_dir(root) / f"{spawn_id}.json").read_text(encoding="utf-8"))
    assert record["trigger"] == "session-end"
    assert record["project"] == PROJECT
    assert record["spawn_id"] == spawn_id


def test_clear_resolves_only_its_own_marker(enabled: tuple[Path, Path]) -> None:
    root, _repo = enabled
    mine = _spawn(root, trigger="session-end")
    theirs = _spawn(root, trigger="session-start")

    spawn_marker.clear(_handle(root), PROJECT, mine)

    assert _markers(root) == [theirs]


def test_a_fresh_marker_is_not_abandoned(enabled: tuple[Path, Path]) -> None:
    """The false-positive guard: an in-flight pass must not read as a dead one.

    Without an age threshold every spawn would be flagged the moment it was
    written, and a check that fires on the healthy path is a check nobody reads.
    """
    root, _repo = enabled
    _spawn(root, trigger="session-end")

    assert spawn_marker.abandoned(_handle(root), PROJECT) == []


def test_an_old_marker_is_abandoned(enabled: tuple[Path, Path]) -> None:
    root, _repo = enabled
    spawn_id = _spawn(root, trigger="session-end")
    _age(root, spawn_id, hours=3)

    stale = spawn_marker.abandoned(_handle(root), PROJECT)

    assert [entry["spawn_id"] for entry in stale] == [spawn_id]
    assert stale[0]["trigger"] == "session-end"


def test_a_corrupt_marker_still_reports_via_mtime(enabled: tuple[Path, Path]) -> None:
    """A damaged record must not make an abandoned pass invisible — the same rule
    ``_read_curator_log`` follows: history we cannot vouch for is a diagnosis,
    never a silent pass."""
    root, _repo = enabled
    spawn_id = _spawn(root, trigger="session-end")
    path = _marker_dir(root) / f"{spawn_id}.json"
    path.write_text("{not json", encoding="utf-8")
    old = (datetime.now(UTC) - timedelta(hours=4)).timestamp()
    os.utime(path, (old, old))

    assert len(spawn_marker.abandoned(_handle(root), PROJECT)) == 1


def test_sweep_clears_only_abandoned_markers(enabled: tuple[Path, Path]) -> None:
    root, _repo = enabled
    dead = _spawn(root, trigger="session-end")
    live = _spawn(root, trigger="session-start")
    _age(root, dead, hours=5)

    assert spawn_marker.sweep(_handle(root), PROJECT) == 1
    assert _markers(root) == [live]


# --------------------------------------------------------------------------
# The wiring: the spawner writes, the child clears
# --------------------------------------------------------------------------


def _patch_curate_spawn(monkeypatch: pytest.MonkeyPatch, handler):
    """Intercept only the ``curate`` spawn, delegating everything else.

    ``subprocess.Popen`` is a module global, and ``resolve_project`` runs
    ``git rev-parse`` through it — so a blanket fake breaks *project resolution*
    rather than the spawn, and every assertion about markers then passes because
    nothing was ever written. The first draft of these tests did exactly that and
    went green while testing nothing.
    """
    real_popen = recall_common.subprocess.Popen

    def dispatch(argv, **kwargs):
        if list(argv[1:2]) == ["curate"]:
            return handler(argv, **kwargs)
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(recall_common.subprocess, "Popen", dispatch)


def test_spawner_writes_the_marker_before_forking(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order is the point: a marker written after the fork cannot describe a
    child that died immediately."""
    root, repo = enabled
    seen: dict[str, list[str]] = {}
    argv_seen: list[list[str]] = []

    def fake_popen(argv, **_kwargs):
        seen["markers"] = _markers(root)
        argv_seen.append(list(argv))
        return object()

    _patch_curate_spawn(monkeypatch, fake_popen)
    recall_common.spawn_curate_if_stale(root, repo, trigger="session-end")

    markers = seen["markers"]
    argv = argv_seen[0]
    assert markers, "the marker must exist by the time Popen is called"
    assert "--spawn-id" in argv
    assert argv[argv.index("--spawn-id") + 1] == markers[0]


def test_a_failed_spawn_clears_its_own_marker(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No child ⇒ nobody to clear it. A surviving marker must mean "a pass
    started and never came back", not "a fork failed"."""
    root, repo = enabled
    written: list[str] = []
    real_write = spawn_marker.write

    def spy_write(spawn_handle, project, *, trigger):
        spawn_id = real_write(spawn_handle, project, trigger=trigger)
        written.append(spawn_id or "")
        return spawn_id

    def boom(argv, **_kwargs):
        raise OSError("no fork for you")

    monkeypatch.setattr(recall_common.spawn_marker, "write", spy_write)
    _patch_curate_spawn(monkeypatch, boom)
    recall_common.spawn_curate_if_stale(root, repo, trigger="session-end")

    # Without this, the test passes when the marker was never written at all —
    # which is how it passed before `_patch_curate_spawn` stopped the fake from
    # swallowing the `git rev-parse` that resolves the project.
    assert written and written[0], "a marker must have been written to have something to clear"
    assert _markers(root) == []


def test_wrap_spawns_a_session_end_pass(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo = enabled
    calls: list[dict] = []

    def fake_spawn(spawn_root: Path, spawn_cwd: Path, *, trigger: str = "session-start") -> None:
        calls.append({"root": spawn_root, "cwd": spawn_cwd, "trigger": trigger})

    monkeypatch.setattr(recall_common, "spawn_curate_if_stale", fake_spawn)
    result = runner.invoke(
        app,
        ["wrap", "--summary", "an authored digest", "--root", str(root), "--cwd", str(repo)],
    )

    assert result.exit_code == 0, result.output
    assert [call["trigger"] for call in calls] == ["session-end"]
    assert calls[0]["root"] == root


def test_curate_clears_the_marker_when_it_is_not_stale(enabled: tuple[Path, Path]) -> None:
    """The load-bearing case for the ``finally``.

    "Not stale" is the most common outcome of a hook-fired pass and it journals
    nothing at all. If the marker survived it, doctor would report an abandoned
    pass after every ordinary session start.
    """
    root, repo = enabled
    spawn_id = _spawn(root, trigger="session-end")

    result = runner.invoke(
        app,
        ["curate", "--if-stale", "--root", str(root), "--cwd", str(repo), "--spawn-id", spawn_id],
    )

    assert result.exit_code == 0, result.output
    assert "Not stale" in result.output
    assert _markers(root) == []


def test_curate_clears_the_marker_when_no_brain_is_available(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other silent exit: a missing brain exits 1 and journals nothing."""
    root, repo = enabled
    _write_raw(root)
    spawn_id = _spawn(root, trigger="session-end")
    monkeypatch.setattr(
        cli, "resolve_brain", lambda config: (None, type("R", (), {"reason": "no backend"})())
    )

    result = runner.invoke(
        app,
        ["curate", "--root", str(root), "--cwd", str(repo), "--spawn-id", spawn_id],
    )

    assert result.exit_code == 1
    assert _markers(root) == []


# --------------------------------------------------------------------------
# The guarantee: a spawned-but-never-completed pass is DETECTED
# --------------------------------------------------------------------------


def test_doctor_detects_a_pass_that_was_spawned_and_never_completed(
    enabled: tuple[Path, Path],
) -> None:
    root, repo = enabled
    spawn_id = _spawn(root, trigger="session-end")
    _age(root, spawn_id, hours=6)

    check = diagnostics._abandoned_spawn_check(root, repo)

    assert check.status == "warn"
    assert "never completed" in check.detail
    assert "session-end" in check.detail


def test_doctor_is_quiet_when_every_spawn_was_resolved(enabled: tuple[Path, Path]) -> None:
    root, repo = enabled
    spawn_id = _spawn(root, trigger="session-end")
    _age(root, spawn_id, hours=6)
    spawn_marker.clear(_handle(root), PROJECT, spawn_id)

    assert diagnostics._abandoned_spawn_check(root, repo).status == "ok"


def test_the_check_is_wired_into_doctor(enabled: tuple[Path, Path]) -> None:
    """A check nothing calls diagnoses nothing."""
    root, repo = enabled

    names = {check.name for check in diagnostics.collect_checks(Config(), root, repo)}

    assert "curate spawns" in names


def test_a_completed_pass_records_what_it_swept(enabled: tuple[Path, Path]) -> None:
    """Sweeping must leave a durable trace, or clearing the alarm re-creates the
    silence the marker exists to end."""
    root, repo = enabled
    _write_raw(root)
    dead = _spawn(root, trigger="session-end")
    _age(root, dead, hours=8)

    result = runner.invoke(app, ["curate", "--root", str(root), "--cwd", str(repo)])
    assert result.exit_code == 0, result.output

    log = (store.memory_dir(PROJECT, root) / store.CURATOR_LOG).read_text(encoding="utf-8")
    records = [json.loads(line) for line in log.splitlines() if line.strip()]
    assert any(record.get("abandoned_spawns") == 1 for record in records), records
    assert _markers(root) == []


def test_a_failing_dry_run_sweeps_nothing(
    enabled: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """G13's rule, applied to the new write: a preview may not mutate the store.

    It must be a **failing** preview. A successful one never reaches ``_log_pass``
    at all (that *is* G13's fix), so a passing dry run exercises nothing — the
    first version of this test asserted against a successful preview and stayed
    green with the guard deleted.
    """
    root, repo = enabled
    _write_raw(root)
    dead = _spawn(root, trigger="session-end")
    _age(root, dead, hours=8)

    class _RefusingBrain(_FakeBrain):
        def plan_json(self, system: str, user: str) -> dict:
            raise BrainError("the model refused")

    monkeypatch.setattr(cli, "resolve_brain", lambda config: (_RefusingBrain(), None))

    result = runner.invoke(app, ["curate", "--dry-run", "--root", str(root), "--cwd", str(repo)])

    assert result.exit_code == 0, result.output
    log = (store.memory_dir(PROJECT, root) / store.CURATOR_LOG).read_text(encoding="utf-8")
    records = [json.loads(line) for line in log.splitlines() if line.strip()]
    assert any(record.get("dry_run") is True for record in records), (
        "the preview must have journaled a failure, or this test proves nothing"
    )
    assert _markers(root) == [dead]
