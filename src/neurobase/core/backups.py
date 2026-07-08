"""Config-file backups (spec §10).

Before Neurobase first modifies any agent config file in an ``init`` run, the
original is copied to ``<root>/backups/<UTC-ts>/`` alongside a ``manifest.json``
mapping each backed-up file to its stored copy. Backups are disaster recovery
only — uninstall is surgical (spec §7), not backup-restore.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path


class BackupRestoreError(RuntimeError):
    """A requested backup cannot be restored."""


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def backup_files(root: Path, paths: Iterable[Path]) -> Path | None:
    """Copy every existing path in ``paths`` into a fresh
    ``<root>/backups/<UTC-ts>/`` with a ``manifest.json``. Returns the backup
    dir, or ``None`` if nothing existed to back up."""
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None
    backup_dir = root / "backups" / _timestamp()
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for path in existing:
        dest = backup_dir / path.name
        counter = 1
        while dest.exists():  # distinct sources can share a basename
            dest = backup_dir / f"{path.stem}.{counter}{path.suffix}"
            counter += 1
        shutil.copy2(path, dest)
        manifest.append({"original_abs_path": str(path.resolve()), "stored_as": str(dest)})
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return backup_dir


def restore_backup(root: Path, timestamp: str) -> list[Path]:
    """Restore every file listed in ``<root>/backups/<timestamp>/manifest.json``.

    This is intentionally wholesale disaster recovery, not normal uninstall.
    """
    if Path(timestamp).name != timestamp:
        raise BackupRestoreError("backup timestamp must be a single directory name")
    backup_dir = root / "backups" / timestamp
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise BackupRestoreError(f"backup manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BackupRestoreError(f"backup manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, list):
        raise BackupRestoreError("backup manifest is not a JSON list")

    restored: list[Path] = []
    for entry in manifest:
        if not isinstance(entry, dict):
            raise BackupRestoreError("backup manifest contains a non-object entry")
        original = entry.get("original_abs_path")
        stored = entry.get("stored_as")
        if not isinstance(original, str) or not isinstance(stored, str):
            raise BackupRestoreError("backup manifest entry is missing paths")
        stored_path = Path(stored)
        if not stored_path.exists():
            raise BackupRestoreError(f"backed-up file is missing: {stored_path}")
        original_path = Path(original)
        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stored_path, original_path)
        restored.append(original_path)
    return restored
