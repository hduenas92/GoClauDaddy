"""Confirms a genuinely unhandled exception in a route never crashes the
server or leaks internals to the client - it must come back as a safe,
generic 500 with the real traceback only in the log.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.services import conversations_service as svc


def test_unhandled_exception_returns_safe_500(monkeypatch):
    def boom(conversation_id):
        raise RuntimeError("simulated real failure - disk full, whatever")

    monkeypatch.setattr(svc, "get_conversation", boom)

    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/api/conversations/anything")

    assert res.status_code == 500
    body = res.json()
    assert body == {"error": "Something went wrong. Check the logs folder for details."}
    # The real exception message must never reach the client.
    assert "simulated real failure" not in res.text


def test_server_survives_and_serves_the_next_request_normally(monkeypatch, temp_db):
    """One bad request must not take the whole process down - the very next
    unrelated request on the same app should succeed normally."""

    def boom(conversation_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "get_conversation", boom)
    client = TestClient(app, raise_server_exceptions=False)

    crashy = client.get("/api/conversations/anything")
    assert crashy.status_code == 500

    monkeypatch.undo()
    healthy = client.get("/api/health")
    assert healthy.status_code == 200
    assert healthy.json() == {"status": "ok"}
