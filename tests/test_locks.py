"""Cross-process single-flight lock tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from neurobase.core import locks


def test_file_lock_is_nonblocking_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "curate.lock"
    with locks.try_file_lock(path) as first:
        assert first
        with locks.try_file_lock(path) as second:
            assert not second
    with locks.try_file_lock(path) as after_release:
        assert after_release


def test_file_lock_releases_after_exception(tmp_path: Path) -> None:
    path = tmp_path / "curate.lock"
    with pytest.raises(RuntimeError, match="boom"), locks.try_file_lock(path) as acquired:
        assert acquired
        raise RuntimeError("boom")
    with locks.try_file_lock(path) as after_exception:
        assert after_exception


def test_twenty_child_processes_cannot_enter_held_lock(tmp_path: Path) -> None:
    path = tmp_path / "curate.lock"
    script = """
import sys
from pathlib import Path
from neurobase.core.locks import try_file_lock

with try_file_lock(Path(sys.argv[1])) as acquired:
    raise SystemExit(2 if acquired else 0)
"""
    with locks.try_file_lock(path) as acquired:
        assert acquired
        children = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(20)
        ]
        completed = [child.communicate(timeout=20) + (child.returncode,) for child in children]

    failures = [result for result in completed if result[2] != 0]
    assert failures == []


def test_curate_lock_is_scoped_by_store_and_project(tmp_path: Path) -> None:
    first = locks.curate_lock_path(tmp_path / "one", "project")
    second_store = locks.curate_lock_path(tmp_path / "two", "project")
    second_project = locks.curate_lock_path(tmp_path / "one", "other")
    assert len({first, second_store, second_project}) == 3
