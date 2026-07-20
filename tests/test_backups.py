"""Tests for config-file backups (spec §10) — ``core/backups.py``.

Focus: the F7 regression — two backups taken within the same wall-clock second
must not share a directory, or the second ``manifest.json`` clobbers the first
and rollback silently loses a touched file."""

from __future__ import annotations

from pathlib import Path

import pytest

from neurobase.core import backups
from neurobase.core.backups import BackupRestoreError


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


def test_distinct_sources_with_same_basename_do_not_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two sources sharing a basename (``a/config`` and ``b/config``) are stored
    side by side under a ``.N`` suffix, and both round-trip through restore with
    their own original contents."""
    root = tmp_path / "store"
    monkeypatch.setattr(backups, "_timestamp", lambda: "2026-07-10T00-00-00Z")

    a = tmp_path / "a" / "config"
    b = tmp_path / "b" / "config"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("from a\n", encoding="utf-8")
    b.write_text("from b\n", encoding="utf-8")

    backup_dir = backups.backup_files(root, [a, b])
    assert backup_dir is not None
    # Both sources landed in the one dir under distinct stored names.
    assert sorted(p.name for p in backup_dir.glob("config*")) == ["config", "config.1"]

    a.write_text("clobbered\n", encoding="utf-8")
    b.write_text("clobbered\n", encoding="utf-8")
    restored = backups.restore_backup(root, backup_dir.name)

    assert sorted(p.name for p in restored) == ["config", "config"]
    assert a.read_text(encoding="utf-8") == "from a\n"
    assert b.read_text(encoding="utf-8") == "from b\n"


def test_restore_rejects_non_leaf_timestamp(tmp_path: Path) -> None:
    """A timestamp that is not a single directory name (path traversal) is
    refused before any filesystem lookup."""
    root = tmp_path / "store"
    with pytest.raises(BackupRestoreError, match="single directory name"):
        backups.restore_backup(root, "../escape")


def test_restore_missing_manifest(tmp_path: Path) -> None:
    root = tmp_path / "store"
    (root / "backups" / "ts").mkdir(parents=True)
    with pytest.raises(BackupRestoreError, match="manifest not found"):
        backups.restore_backup(root, "ts")


def _write_manifest(root: Path, ts: str, text: str) -> None:
    backup_dir = root / "backups" / ts
    backup_dir.mkdir(parents=True)
    (backup_dir / "manifest.json").write_text(text, encoding="utf-8")


def test_restore_rejects_invalid_json_manifest(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _write_manifest(root, "ts", "{not json")
    with pytest.raises(BackupRestoreError, match="not valid JSON"):
        backups.restore_backup(root, "ts")


def test_restore_rejects_non_list_manifest(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _write_manifest(root, "ts", '{"original_abs_path": "x", "stored_as": "y"}')
    with pytest.raises(BackupRestoreError, match="not a JSON list"):
        backups.restore_backup(root, "ts")


def test_restore_rejects_non_object_entry(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _write_manifest(root, "ts", '["not an object"]')
    with pytest.raises(BackupRestoreError, match="non-object entry"):
        backups.restore_backup(root, "ts")


def test_restore_rejects_entry_missing_paths(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _write_manifest(root, "ts", '[{"original_abs_path": "x"}]')
    with pytest.raises(BackupRestoreError, match="missing paths"):
        backups.restore_backup(root, "ts")


def test_restore_rejects_missing_backed_up_file(tmp_path: Path) -> None:
    root = tmp_path / "store"
    original = tmp_path / "orig.md"
    gone = tmp_path / "backups" / "ts" / "orig.md"  # never created
    _write_manifest(
        root,
        "ts",
        f'[{{"original_abs_path": "{original}", "stored_as": "{gone}"}}]',
    )
    with pytest.raises(BackupRestoreError, match="backed-up file is missing"):
        backups.restore_backup(root, "ts")
