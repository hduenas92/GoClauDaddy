"""Tests for the ~/.claudioui → ~/.goclaudaddy one-shot data migration."""

import sqlite3
from pathlib import Path

import pytest

from app.data_migration import DataMigrationError, migrate_data_dir


# Minimal schema — only what the migration touches (no FK constraints needed).
_MINIMAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    stored_path TEXT NOT NULL
);
"""


def _seed_old_dir(old_dir: Path, attachment_rows: list[tuple[str, str]] | None = None) -> None:
    old_dir.mkdir()
    (old_dir / "logs").mkdir()
    att_dir = old_dir / "attachments"
    att_dir.mkdir()

    db = old_dir / "claudioui.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_MINIMAL_SCHEMA)
        for row_id, path in (attachment_rows or []):
            conn.execute("INSERT INTO attachments (id, stored_path) VALUES (?, ?)", (row_id, path))


def test_no_op_when_old_dir_absent(tmp_path):
    new_dir = tmp_path / "new"
    migrate_data_dir(old_dir=tmp_path / "absent", new_dir=new_dir)
    assert not new_dir.exists()


def test_no_op_when_marker_exists(tmp_path):
    old_dir = tmp_path / "old"
    _seed_old_dir(old_dir)
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    (new_dir / ".migrated_from_claudioui").write_text("migrated")

    migrate_data_dir(old_dir=old_dir, new_dir=new_dir)

    # New DB should NOT have been created (marker prevented migration).
    assert not (new_dir / "goclaudaddy.db").exists()


def test_db_copied_and_paths_rewritten(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_att_path = str(old_dir / "attachments" / "conv1" / "uuid_file.txt")
    _seed_old_dir(old_dir, attachment_rows=[("att1", old_att_path)])

    migrate_data_dir(old_dir=old_dir, new_dir=new_dir)

    new_db = new_dir / "goclaudaddy.db"
    assert new_db.exists()
    with sqlite3.connect(str(new_db)) as conn:
        row = conn.execute("SELECT stored_path FROM attachments WHERE id = 'att1'").fetchone()
    expected = str(new_dir / "attachments" / "conv1" / "uuid_file.txt")
    assert row[0] == expected


def test_attachment_files_copied(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    _seed_old_dir(old_dir)
    conv_dir = old_dir / "attachments" / "conv1"
    conv_dir.mkdir(parents=True)
    (conv_dir / "file.txt").write_bytes(b"hello")

    migrate_data_dir(old_dir=old_dir, new_dir=new_dir)

    assert (new_dir / "attachments" / "conv1" / "file.txt").read_bytes() == b"hello"


def test_marker_written_on_success(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    _seed_old_dir(old_dir)

    migrate_data_dir(old_dir=old_dir, new_dir=new_dir)

    assert (new_dir / ".migrated_from_claudioui").exists()


def test_idempotent(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    _seed_old_dir(old_dir, attachment_rows=[("a1", str(old_dir / "attachments" / "f.txt"))])

    migrate_data_dir(old_dir=old_dir, new_dir=new_dir)
    migrate_data_dir(old_dir=old_dir, new_dir=new_dir)  # second call is no-op

    with sqlite3.connect(str(new_dir / "goclaudaddy.db")) as conn:
        count = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    assert count == 1  # not doubled


def test_no_old_db_still_migrates_attachments(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    att_dir = old_dir / "attachments" / "c1"
    att_dir.mkdir(parents=True)
    (att_dir / "img.png").write_bytes(b"\x89PNG")

    migrate_data_dir(old_dir=old_dir, new_dir=new_dir)

    assert (new_dir / "attachments" / "c1" / "img.png").read_bytes() == b"\x89PNG"
    assert (new_dir / ".migrated_from_claudioui").exists()
    assert not (new_dir / "goclaudaddy.db").exists()
