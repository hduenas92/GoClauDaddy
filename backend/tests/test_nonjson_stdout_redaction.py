"""P2-B E1 / R3 N1: a non-JSON CLI stdout line may contain credentials.

The server log keeps the line but only after redaction. The WebSocket client
must never receive any part of the raw line: the notice frame carries fixed
copy that is identical regardless of what the CLI printed.
"""

import json
import threading

from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli_mod
from app.main import app

client = TestClient(app)


def _collect_frames(raw_line, reply_text, monkeypatch):
    """Run one fake WS turn whose CLI emits a raw notice line, then a reply."""
    gen_done = threading.Event()
    events = [
        {"type": "notice", "text": raw_line},
        {"type": "text", "text": reply_text},
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
    return frames


def test_non_json_stdout_log_redacts_credentials(temp_db, monkeypatch, caplog):
    """The server log keeps the non-JSON line, but with credentials replaced."""
    fake_sk = "sk-ant-api03-FAKE" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "abcd"
    fake_bearer = "Bearer FAKEtoken123456789"
    fake_env = "FAKEenvTOKENvalue987654321"
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", fake_env)

    raw_line = f"banner line {fake_sk} {fake_bearer} {fake_env}"
    _collect_frames(raw_line, "real reply", monkeypatch)

    records = [
        r for r in caplog.records
        if "Non-JSON CLI stdout line ignored" in r.getMessage()
    ]
    assert records, "non-JSON notice: no matching log record was produced"
    rec = records[-1]
    msg = rec.getMessage()
    assert "[REDACTED]" in msg, msg
    for secret in (fake_sk, fake_bearer, fake_env):
        assert secret not in msg, (secret, msg)


def test_non_json_notice_frame_carries_only_fixed_copy(temp_db, monkeypatch):
    """The forwarded notice frame contains no part of either raw line."""
    raw_a = (
        "BANNER-REDACT-A "
        "sk-ant-api03-FAKE" + "A" * 30 + " "
        "Bearer tokenA123456789 envTokenA123456789"
    )
    raw_b = (
        "BANNER-REDACT-B "
        "sk-ant-api03-FAKE" + "B" * 30 + " "
        "Bearer tokenB987654321 envTokenB987654321"
    )

    frames_a = _collect_frames(raw_a, "reply one", monkeypatch)
    frames_b = _collect_frames(raw_b, "reply two", monkeypatch)

    for raw, frames in ((raw_a, frames_a), (raw_b, frames_b)):
        dumped_frames = [json.dumps(frame) for frame in frames]
        for dumped in dumped_frames:
            assert raw not in dumped, (raw, dumped)
        for token in raw.split():
            assert all(token not in dumped for dumped in dumped_frames), (
                token,
                dumped_frames,
            )

    notices_a = [f for f in frames_a if f.get("type") == "notice"]
    notices_b = [f for f in frames_b if f.get("type") == "notice"]
    assert len(notices_a) == 1, frames_a
    assert len(notices_b) == 1, frames_b

    text_a = notices_a[0].get("text")
    text_b = notices_b[0].get("text")
    assert text_a, frames_a
    assert text_b, frames_b
    assert text_a == text_b, (text_a, text_b)
