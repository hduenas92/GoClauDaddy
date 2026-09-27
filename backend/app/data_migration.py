"""One-shot: copy ~/.claudioui data → ~/.goclaudaddy on first launch after rename.

Call migrate_data_dir() at startup (from run.py, after setup_logging). It is a
no-op on every subsequent run because it writes a marker file on completion.
The backup uses sqlite3.Connection.backup() which is WAL-safe: it produces a
consistent snapshot even if the source DB has unflushed WAL frames.
"""

import shutil
import sqlite3
from pathlib import Path

from app.logging_setup import get_logger

log = get_logger("data_migration")

_OLD_DATA_DIR = Path.home() / ".claudioui"
_NEW_DATA_DIR = Path.home() / ".goclaudaddy"


class DataMigrationError(RuntimeError):
    pass


def migrate_data_dir(
    old_dir: Path = _OLD_DATA_DIR,
    new_dir: Path = _NEW_DATA_DIR,
) -> None:
    """Copy old data dir to new location. No-op if already done or source absent."""
    marker = new_dir / ".migrated_from_claudioui"
    if marker.exists() or not old_dir.exists():
        return
    log.info("Migrating data from %s to %s", old_dir, new_dir)
    _migrate(old_dir, new_dir, marker)
    log.info("Data migration complete")


def _migrate(old_dir: Path, new_dir: Path, marker: Path) -> None:
    new_dir.mkdir(exist_ok=True)
    (new_dir / "logs").mkdir(exist_ok=True)
    (new_dir / "attachments").mkdir(exist_ok=True)

    old_db = old_dir / "claudioui.db"
    new_db = new_dir / "goclaudaddy.db"
    if old_db.exists():
        _copy_and_rewrite_db(old_db, new_db, old_dir, new_dir)

    old_att = old_dir / "attachments"
    new_att = new_dir / "attachments"
    if old_att.exists():
        shutil.copytree(str(old_att), str(new_att), dirs_exist_ok=True)

    # Only written after full success — rerunning on partial failure restarts cleanly.
    marker.write_text("migrated", encoding="utf-8")


def _copy_and_rewrite_db(old_db: Path, new_db: Path, old_dir: Path, new_dir: Path) -> None:
    src = sqlite3.connect(str(old_db))
    try:
        dst = sqlite3.connect(str(new_db))
        try:
            src.backup(dst)
        finally:
            dst.close()
        old_count = src.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    finally:
        src.close()

    old_prefix = str(old_dir / "attachments")
    new_prefix = str(new_dir / "attachments")
    with sqlite3.connect(str(new_db)) as conn:
        conn.execute(
            "UPDATE attachments SET stored_path = ? || SUBSTR(stored_path, ?) "
            "WHERE stored_path LIKE ?",
            (new_prefix, len(old_prefix) + 1, old_prefix + "%"),
        )
        new_count = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]

    if old_count != new_count:
        raise DataMigrationError(
            f"Attachment row count mismatch after migration: source={old_count}, dest={new_count}"
        )
