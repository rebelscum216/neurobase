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
