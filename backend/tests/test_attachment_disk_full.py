"""P2-B E6: out-of-space during attachment upload.

Before the fix an ENOSPC (or Windows ERROR_DISK_FULL, winerror 112) from
`Path.write_bytes` bubbled up as a generic 500, and composer.js always appended
"Try again, or use a smaller file." The backend must return a DISTINCT 507
whose message says the disk is full, and the failed upload must not be
recorded in the DB (the row is inserted after the write, and that stays).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services.attachments_service as att_svc
from app.db.connection import get_connection
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _patch_attachments_dir(tmp_path, monkeypatch):
    d = tmp_path / "attachments"
    d.mkdir()
    monkeypatch.setattr(att_svc, "ATTACHMENTS_DIR", d)


def _upload(conv_id, filename="test.txt", content=b"hello", mime="text/plain"):
    return client.post(
        "/api/attachments",
        params={"conversation_id": conv_id},
        files={"file": (filename, content, mime)},
    )


def _make_conv():
    return client.post("/api/conversations", json={}).json()["id"]


def test_enospc_returns_507_disk_full(temp_db, monkeypatch):
    def boom(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", boom)
    conv_id = _make_conv()
    res = _upload(conv_id)
    assert res.status_code == 507
    assert "disk" in res.json()["detail"].lower()


def test_winerror_112_also_recognized(temp_db, monkeypatch):
    def boom(self, data):
        raise OSError(0, "There is not enough space on the disk.", None, 112)

    monkeypatch.setattr(Path, "write_bytes", boom)
    conv_id = _make_conv()
    res = _upload(conv_id)
    assert res.status_code == 507
    assert "disk" in res.json()["detail"].lower()


def test_disk_full_upload_not_recorded(temp_db, monkeypatch):
    def boom(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", boom)
    conv_id = _make_conv()
    res = _upload(conv_id)
    assert res.status_code == 507
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    assert count == 0, "a failed upload must not be recorded in the attachments table"
