"""Tests for config-file backups (spec §10) — ``core/backups.py``.

Focus: the F7 regression — two backups taken within the same wall-clock second
must not share a directory, or the second ``manifest.json`` clobbers the first
and rollback silently loses a touched file."""

from __future__ import annotations

from pathlib import Path

import pytest

from neurobase.core import backups


def test_same_second_backups_do_not_clobber_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F7: with the timestamp pinned to one second, two ``backup_files`` calls
    must land in distinct dirs, each with a manifest naming its own source — so a
    later ``restore_backup`` can recover every file from either call."""
    root = tmp_path / "store"
    monkeypatch.setattr(backups, "_timestamp", lambda: "2026-07-10T00-00-00Z")

    first_src = tmp_path / "first.md"
    first_src.write_text("first original\n", encoding="utf-8")
    second_src = tmp_path / "second.md"
    second_src.write_text("second original\n", encoding="utf-8")

    first_dir = backups.backup_files(root, [first_src])
    second_dir = backups.backup_files(root, [second_src])

    assert first_dir is not None and second_dir is not None
    assert first_dir != second_dir  # the collision is avoided, not merged over

    # Each manifest still names exactly its own backed-up source.
    first_restored = backups.restore_backup(root, first_dir.name)
    second_restored = backups.restore_backup(root, second_dir.name)
    assert [p.name for p in first_restored] == ["first.md"]
    assert [p.name for p in second_restored] == ["second.md"]
