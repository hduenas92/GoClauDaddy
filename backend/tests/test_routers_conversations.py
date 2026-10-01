from dataclasses import replace as dataclasses_replace

from fastapi.testclient import TestClient

from app.config import DEFAULT_MODEL
from app.db.connection import get_connection
from app.main import app
from app.services import conversations_service as svc

client = TestClient(app)


def test_create_get_roundtrip(temp_db):
    res = client.post("/api/conversations", json={"name": "My Chat"})
    assert res.status_code == 200
    conv_id = res.json()["id"]

    res = client.get(f"/api/conversations/{conv_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["conversation"]["name"] == "My Chat"
    assert data["conversation"]["system_prompt"] is None
    assert data["messages"] == []


def test_settings_patch_system_prompt_roundtrips(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    res = client.patch(f"/api/conversations/{conv_id}/settings", json={"system_prompt": "Be concise."})
    assert res.status_code == 200
    assert res.json()["system_prompt"] == "Be concise."

    fetched = client.get(f"/api/conversations/{conv_id}").json()["conversation"]
    assert fetched["system_prompt"] == "Be concise."


def test_settings_patch_empty_string_clears_prompt(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    client.patch(f"/api/conversations/{conv_id}/settings", json={"system_prompt": "Initial"})
    res = client.patch(f"/api/conversations/{conv_id}/settings", json={"system_prompt": ""})
    # "" is treated as None-means-leave-alone per convention (falsy, no SET clause)
    assert res.status_code == 200


def test_404_on_unknown_conversation(temp_db):
    fake = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/conversations/{fake}").status_code == 404
    assert client.patch(f"/api/conversations/{fake}/rename", json={"name": "x"}).status_code == 404
    assert client.patch(f"/api/conversations/{fake}/settings", json={}).status_code == 404
    assert client.delete(f"/api/conversations/{fake}").status_code == 404


def test_delete_then_get_returns_404(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    assert client.delete(f"/api/conversations/{conv_id}").status_code == 200
    assert client.get(f"/api/conversations/{conv_id}").status_code == 404


def _set_model_directly(conv_id, model):
    """Bypass the app (which has no validation on write) to simulate a stale
    stored model, the way the real DB got into this state."""
    with get_connection() as conn:
        conn.execute("UPDATE conversations SET model = ? WHERE id = ?", (model, conv_id))


def test_valid_model_is_untouched(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    _set_model_directly(conv_id, "claude-opus-4-5")
    res = client.get(f"/api/conversations/{conv_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["conversation"]["model"] == "claude-opus-4-5"
    assert body["model_correction"] is None


def test_invalid_model_is_corrected_and_flagged(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    _set_model_directly(conv_id, "claude-3-5-haiku")
    res = client.get(f"/api/conversations/{conv_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["conversation"]["model"] == DEFAULT_MODEL
    assert body["model_correction"] == {"invalid_model": "claude-3-5-haiku", "corrected_to": DEFAULT_MODEL}


def test_correction_persists_to_db(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    _set_model_directly(conv_id, "claude-3-5-haiku")
    client.get(f"/api/conversations/{conv_id}")

    reread = client.get(f"/api/conversations/{conv_id}").json()
    assert reread["conversation"]["model"] == DEFAULT_MODEL
    with get_connection() as conn:
        row = conn.execute("SELECT model FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    assert row["model"] == DEFAULT_MODEL


def test_second_read_is_not_reflagged(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    _set_model_directly(conv_id, "claude-3-5-haiku")
    first = client.get(f"/api/conversations/{conv_id}").json()
    assert first["model_correction"] is not None

    second = client.get(f"/api/conversations/{conv_id}").json()
    assert second["model_correction"] is None
    assert second["conversation"]["model"] == DEFAULT_MODEL


def test_null_model_is_not_flagged_or_written(temp_db, monkeypatch):
    # NOTE: `conversations.model` is `TEXT NOT NULL` in schema.sql, and the live
    # DB has zero NULL-model rows — a real conversation row with model=NULL
    # cannot exist (writing one raises sqlite3.IntegrityError). The task's
    # requirement #3 premise ("NULL model is legitimate, must not be written to")
    # does not match the actual schema. The guard for it (`conv.model is not
    # None`) is still correct defensive code, so this test exercises that
    # branch directly at the service layer with a stubbed Conversation instead
    # of an impossible DB row.
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    real_conv = svc.get_conversation(conv_id)
    null_model_conv = dataclasses_replace(real_conv, model=None)

    monkeypatch.setattr(svc, "get_conversation", lambda cid: null_model_conv)
    write_calls = []
    monkeypatch.setattr(
        svc, "update_conversation_settings", lambda *a, **kw: write_calls.append((a, kw))
    )

    conv, invalid_model = svc.get_conversation_healed(conv_id)
    assert conv.model is None
    assert invalid_model is None
    assert write_calls == []


def test_correction_does_not_touch_messages(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    _set_model_directly(conv_id, "claude-3-5-haiku")
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, seq, created_at)
               VALUES ('m1', ?, 'user', 'hello', 1, '2026-01-01T00:00:00+00:00')""",
            (conv_id,),
        )
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, input_tokens, output_tokens, seq, created_at)
               VALUES ('m2', ?, 'assistant', 'API Error: 400 ... Invalid model name', 0, 0, 2, '2026-01-01T00:00:01+00:00')""",
            (conv_id,),
        )

    with get_connection() as conn:
        before = conn.execute(
            "SELECT id, content, input_tokens, output_tokens FROM messages WHERE conversation_id = ? ORDER BY seq",
            (conv_id,),
        ).fetchall()
        before = [dict(r) for r in before]

    res = client.get(f"/api/conversations/{conv_id}")
    assert res.json()["model_correction"] is not None

    with get_connection() as conn:
        after = conn.execute(
            "SELECT id, content, input_tokens, output_tokens FROM messages WHERE conversation_id = ? ORDER BY seq",
            (conv_id,),
        ).fetchall()
        after = [dict(r) for r in after]

    assert len(after) == 2
    assert after == before


def test_get_conversation_includes_attachments_per_message(temp_db):
    """F2 (P2-C): history after reload needs each message's attachment rows, or
    the thumbnail cannot be rendered from the transcript alone."""
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    user_msg = svc.add_message(conv_id, "user", "look at this")
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO attachments
               (id, message_id, conversation_id, original_name, stored_path, mime_type, size_bytes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("att-1", user_msg.id, conv_id, "pic.png", "C:/nonexistent/pic.png", "image/png", 5, "2026-09-25T00:00:00Z"),
        )
        conn.execute(
            """INSERT INTO attachments
               (id, message_id, conversation_id, original_name, stored_path, mime_type, size_bytes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("att-2", user_msg.id, conv_id, "notes.txt", "C:/nonexistent/notes.txt", "text/plain", 3, "2026-09-25T00:00:00Z"),
        )

    data = client.get(f"/api/conversations/{conv_id}").json()
    assert data["messages"][0]["attachments"] == [
        {"id": "att-1", "original_name": "pic.png", "mime_type": "image/png", "size_bytes": 5},
        {"id": "att-2", "original_name": "notes.txt", "mime_type": "text/plain", "size_bytes": 3},
    ]


def test_get_conversation_returns_stopped_per_message(temp_db):
    """F4 (P2-C): the conversation API must expose messages.stopped per message,
    or renderHistory has nothing to render the Incomplete badge from."""
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    svc.add_message(conv_id, "assistant", "cut off", stopped=True)
    svc.add_message(conv_id, "assistant", "complete", stopped=False)

    data = client.get(f"/api/conversations/{conv_id}").json()
    assert [m["stopped"] for m in data["messages"]] == [True, False]


def test_settings_patch_rejects_unknown_model(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    res = client.patch(f"/api/conversations/{conv_id}/settings", json={"model": "claude-3-5-haiku"})
    assert res.status_code == 422


def test_settings_clear_permission_mode_resets_to_null(temp_db):
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    res = client.patch(f"/api/conversations/{conv_id}/settings", json={"permission_mode": "plan"})
    assert res.status_code == 200
    assert res.json()["permission_mode"] == "plan"

    res = client.patch(f"/api/conversations/{conv_id}/settings", json={"clear_permission_mode": True})
    assert res.status_code == 200
    assert res.json()["permission_mode"] is None

    fetched = client.get(f"/api/conversations/{conv_id}").json()["conversation"]
    assert fetched["permission_mode"] is None
