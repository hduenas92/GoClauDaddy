"""P2-B E1: a non-JSON CLI stdout line must be logged, forwarded to the client
as a NON-FATAL notice (not `text`, not `error`), and must never be persisted as
assistant message content.

Houston ruled 2026-09-25 (QA_AGENT_INSTRUCTIONS.md §4): non-JSON CLI stdout is
an error to surface, not Claude's reply. The stream must continue through later
valid JSON lines.
"""

import threading

from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli_mod
from app.main import app
from app.services import conversations_service as convs
from app.services.stream_parser import parse_line

client = TestClient(app)


def test_parse_line_non_json_becomes_notice_event():
    """The parser must not turn a stray banner line into assistant text."""
    assert parse_line("some banner output") == [
        {"type": "notice", "text": "some banner output"}
    ]


def test_non_json_line_logged_forwarded_and_not_persisted(temp_db, monkeypatch, caplog):
    """End-to-end through the WS handler with a fake CLI generator.

    The garbage line must:
      - reach the client as a `notice` frame (visible non-fatal notice),
      - appear in the server log,
      - NOT be appended to the assistant row's content,
    while later valid JSON lines are still processed and the turn ends normally.
    """
    gen_done = threading.Event()
    events = [
        {"type": "notice", "text": "BANNER-GARBAGE-42"},
        {"type": "text", "text": "real reply"},
    ]

    async def fake_run(**kwargs):
        for e in events:
            yield e
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    frames = []
    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "Hello"})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame.get("type") == "done":
                break
        assert gen_done.wait(timeout=2.0), "generator never exhausted"

    assert {"type": "notice", "text": "BANNER-GARBAGE-42"} in frames, frames
    assert {"type": "text", "text": "real reply"} in frames, frames
    assert frames[-1]["type"] == "done", f"turn did not end normally: {frames!r}"

    assistant = [m for m in convs.list_messages(conv_id) if m.role == "assistant"]
    assert len(assistant) == 1, f"expected one assistant row, got {len(assistant)}"
    assert assistant[0].content == "real reply"
    assert "BANNER-GARBAGE-42" not in assistant[0].content

    assert "BANNER-GARBAGE-42" in caplog.text, (
        "the non-JSON line was not logged server-side; caplog saw:\n" + caplog.text
    )
