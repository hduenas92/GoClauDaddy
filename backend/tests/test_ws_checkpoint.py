"""P2-E unit tests: streaming checkpoint trigger rules + DB write count.

The trigger rules under test (chat_socket.py):
  - a `tool_call` event writes immediately
  - a `tool_result` event writes immediately
  - text/thinking/usage share a 2 s throttle (CHECKPOINT_INTERVAL_SECONDS)

A fake clock replaces chat_socket._monotonic_now so none of these tests sleeps
2 real seconds. A counting connection wrapper records every write statement the
conversations service executes so the tests assert on what was WRITTEN, not
just on what the final row happens to contain.
"""

import asyncio
import contextlib
import json
import threading
import time

from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli_mod
import app.services.conversations_service as convs_mod
from app.main import app
from app.ws import chat_socket
from tests.fixtures import fake_claude_cli as fake

client = TestClient(app)


class FakeClock:
    """Monotonic clock double; only the checkpoint throttle reads it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _conv(temp_db) -> str:
    return client.post("/api/conversations", json={}).json()["id"]


class _CountingConnection:
    """Proxy that counts the statements the app itself executes.

    sqlite3's trace callback is unusable here: with an FTS5 external-content
    table, SQLite re-traces the same INSERT several times while it syncs the
    index, so the count would measure FTS internals instead of app writes.
    Wrapping execute() counts each explicit app statement exactly once.
    """

    def __init__(self, conn, writes: list[str]) -> None:
        self._conn = conn
        self._writes = writes

    def execute(self, sql, params=()):
        if isinstance(sql, str) and sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
            self._writes.append(sql)
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _install_write_counter(monkeypatch) -> list[str]:
    """Record every INSERT/UPDATE/DELETE/REPLACE the conversations service runs."""
    writes: list[str] = []
    real = convs_mod.get_connection

    @contextlib.contextmanager
    def counting_connection():
        with real() as conn:
            yield _CountingConnection(conn, writes)

    monkeypatch.setattr(convs_mod, "get_connection", counting_connection)
    return writes


def _app_writes(writes: list[str]) -> list[str]:
    """The statements the app explicitly executed (kept for clarity)."""
    return list(writes)


def _app_writes(writes: list[str]) -> list[str]:
    """The statements the app explicitly executed (FTS sync triggers excluded)."""
    return [w for w in writes if "messages_fts" not in w.upper()]


def _assistants(conv_id: str) -> list:
    return [m for m in convs_mod.list_messages(conv_id) if m.role == "assistant"]


def _wait_until(pred, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def _drain_until_done(ws) -> None:
    while ws.receive_json().get("type") != "done":
        pass


def test_tool_call_checkpoint_is_immediate(temp_db, monkeypatch):
    """A tool_call 0.5 s after the previous checkpoint must still write at once."""
    clock = FakeClock()
    monkeypatch.setattr(chat_socket, "_monotonic_now", clock)
    writes = _install_write_counter(monkeypatch)
    conv_id = _conv(temp_db)

    after_text = threading.Event()
    after_tool_call = threading.Event()
    release_text = threading.Event()
    release_tool_call = threading.Event()
    gen_done = threading.Event()

    async def fake_run(**kwargs):
        yield {"type": "text", "text": "hello "}
        after_text.set()
        await asyncio.to_thread(release_text.wait, 5)
        clock.advance(0.5)
        yield {"type": "tool_call", "id": "t1", "name": "Read", "input": {"file_path": "/tmp/a"}}
        after_tool_call.set()
        await asyncio.to_thread(release_tool_call.wait, 5)
        yield {"type": "text", "text": "world"}
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        try:
            ws.send_json({"type": "send", "message": "go"})
            assert ws.receive_json()["type"] == "text"
            assert after_text.wait(2)
            writes_after_text = len(_app_writes(writes))
            rows = _assistants(conv_id)
            assert len(rows) == 1
            assert rows[0].content == "hello "
            release_text.set()

            assert ws.receive_json()["type"] == "tool_call"
            assert after_tool_call.wait(2)
            # 0.5 s later but a tool_call: the immediate rule must have written.
            assert len(_app_writes(writes)) > writes_after_text
            rows = _assistants(conv_id)
            assert len(rows) == 1, "tool_call checkpoint must UPDATE the row, not insert a second"
            calls = json.loads(rows[0].tool_calls)
            assert calls[0]["name"] == "Read"
            assert calls[0]["input"] == {"file_path": "/tmp/a"}
            assert "output" not in calls[0], "started tool call has no output yet"
            assert rows[0].stopped is True, "streaming row must be marked incomplete"
        finally:
            release_text.set()
            release_tool_call.set()
        _drain_until_done(ws)
        assert gen_done.wait(2)
        assert _wait_until(lambda: any(m.stopped is False for m in _assistants(conv_id)))

    rows = _assistants(conv_id)
    assert len(rows) == 1
    assert rows[0].content == "hello world"
    assert rows[0].stopped is False


def test_text_burst_within_interval_gives_no_extra_write(temp_db, monkeypatch):
    """Text arriving inside the 2 s throttle window must not checkpoint again."""
    clock = FakeClock()
    monkeypatch.setattr(chat_socket, "_monotonic_now", clock)
    writes = _install_write_counter(monkeypatch)
    conv_id = _conv(temp_db)

    after_first = threading.Event()
    after_second = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()
    gen_done = threading.Event()

    async def fake_run(**kwargs):
        yield {"type": "text", "text": "one "}
        after_first.set()
        await asyncio.to_thread(release_first.wait, 5)
        clock.advance(0.5)
        yield {"type": "text", "text": "two "}
        after_second.set()
        await asyncio.to_thread(release_second.wait, 5)
        clock.advance(0.5)
        yield {"type": "text", "text": "three "}
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        try:
            ws.send_json({"type": "send", "message": "go"})
            assert ws.receive_json()["type"] == "text"
            assert after_first.wait(2)
            writes_after_first = len(_app_writes(writes))
            assert [m.content for m in _assistants(conv_id)] == ["one "]
            release_first.set()

            assert ws.receive_json()["type"] == "text"
            assert after_second.wait(2)
            # 0.5 s since the first checkpoint: no extra write, row unchanged.
            assert len(_app_writes(writes)) == writes_after_first
            assert [m.content for m in _assistants(conv_id)] == ["one "]
            release_second.set()
        finally:
            release_first.set()
            release_second.set()
        _drain_until_done(ws)
        assert gen_done.wait(2)
        assert _wait_until(lambda: any(m.stopped is False for m in _assistants(conv_id)))

    rows = _assistants(conv_id)
    assert len(rows) == 1
    assert rows[0].content == "one two three "


def test_text_gap_of_interval_writes_again(temp_db, monkeypatch):
    """A 2.0 s gap since the previous checkpoint permits the next text write."""
    clock = FakeClock()
    monkeypatch.setattr(chat_socket, "_monotonic_now", clock)
    writes = _install_write_counter(monkeypatch)
    conv_id = _conv(temp_db)

    after_first = threading.Event()
    after_second = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()
    gen_done = threading.Event()

    async def fake_run(**kwargs):
        yield {"type": "text", "text": "one "}
        after_first.set()
        await asyncio.to_thread(release_first.wait, 5)
        clock.advance(2.0)
        yield {"type": "text", "text": "two "}
        after_second.set()
        await asyncio.to_thread(release_second.wait, 5)
        clock.advance(2.0)
        yield {"type": "text", "text": "three "}
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        try:
            ws.send_json({"type": "send", "message": "go"})
            assert ws.receive_json()["type"] == "text"
            assert after_first.wait(2)
            writes_after_first = len(_app_writes(writes))
            release_first.set()

            assert ws.receive_json()["type"] == "text"
            assert after_second.wait(2)
            # Gap >= CHECKPOINT_INTERVAL_SECONDS: exactly one more UPDATE.
            assert len(_app_writes(writes)) == writes_after_first + 1
            assert [m.content for m in _assistants(conv_id)] == ["one two "]
            release_second.set()
        finally:
            release_first.set()
            release_second.set()
        _drain_until_done(ws)
        assert gen_done.wait(2)
        assert _wait_until(lambda: any(m.stopped is False for m in _assistants(conv_id)))

    rows = _assistants(conv_id)
    assert len(rows) == 1
    assert rows[0].content == "one two three "


def test_db_write_count_for_fake_cli_turn(temp_db, monkeypatch):
    """Throughput pin: count every DB write for the fake CLI's seven-event turn.

    Expected app-level writes: 2 (user INSERT + conversations touch) + 1 (busy)
    + 2 (first checkpoint INSERT + conversations touch) + 1 (tool_call UPDATE)
    + 1 (tool_result UPDATE) + 1 (idle) + 1 (final UPDATE) = 9.
    """
    clock = FakeClock()
    monkeypatch.setattr(chat_socket, "_monotonic_now", clock)
    conv_id = _conv(temp_db)
    writes = _install_write_counter(monkeypatch)

    events = [
        {"type": "thinking", "thinking": fake.THINKING},
        {"type": "usage", "usage": dict(fake.TOKENS)},
        {"type": "tool_call", "id": fake.TOOL_USE_ID, "name": fake.TOOL_NAME, "input": fake.TOOL_INPUT},
        {"type": "tool_result", "tool_use_id": fake.TOOL_USE_ID, "content": fake.TOOL_RESULT, "is_error": False},
        {"type": "text", "text": fake.TEXT},
        {"type": "usage", "usage": dict(fake.TOKENS)},
        {"type": "result", "usage": dict(fake.TOKENS)},
    ]
    gen_done = threading.Event()

    async def fake_run(**kwargs):
        for event in events:
            yield event
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "go"})
        _drain_until_done(ws)
        assert gen_done.wait(2)
        assert _wait_until(lambda: any(m.stopped is False for m in _assistants(conv_id)))

    rows = _assistants(conv_id)
    assert len(rows) == 1, "exactly one assistant row per turn"
    row = rows[0]
    assert row.content == fake.TEXT
    assert row.thinking == fake.THINKING
    assert row.input_tokens == fake.TOKENS["input_tokens"]
    assert row.output_tokens == fake.TOKENS["output_tokens"]
    assert row.cache_read_tokens == fake.TOKENS["cache_read_input_tokens"]
    assert row.cache_creation_tokens == fake.TOKENS["cache_creation_input_tokens"]
    calls = json.loads(row.tool_calls)
    assert calls[0]["output"] == fake.TOOL_RESULT
    assert row.stopped is False

    app_writes = _app_writes(writes)
    assert len(app_writes) == 9, f"app-level writes: {app_writes!r}"
