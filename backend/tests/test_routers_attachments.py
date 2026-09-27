import pytest
from fastapi.testclient import TestClient

import app.services.attachments_service as att_svc
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


def test_upload_download_delete(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    res = _upload(conv_id)
    assert res.status_code == 200
    att_id = res.json()["id"]

    res = client.get(f"/api/attachments/{att_id}/download")
    assert res.status_code == 200

    res = client.delete(f"/api/attachments/{att_id}")
    assert res.status_code == 200

    # 404 on double-delete
    res = client.delete(f"/api/attachments/{att_id}")
    assert res.status_code == 404


def test_disallowed_extension_returns_400(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    res = _upload(conv_id, filename="evil.exe", content=b"x", mime="application/octet-stream")
    assert res.status_code == 400


def test_oversized_file_returns_400(temp_db, monkeypatch):
    monkeypatch.setattr(att_svc, "MAX_UPLOAD_BYTES", 5)
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    res = _upload(conv_id, content=b"x" * 100)
    assert res.status_code == 400


def test_download_404_on_unknown(temp_db):
    assert client.get("/api/attachments/no-such-id/download").status_code == 404
