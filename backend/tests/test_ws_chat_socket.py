"""Concurrency-contract tests for the WS chat handler.

These tests monkeypatch claude_cli.run with an async generator so no real
subprocess or network call is needed. All sync/async coordination uses
threading.Event (thread-safe) because the server runs in a background thread
(TestClient) while the test body runs in the main thread.
"""

import asyncio
import json
import threading
import time

from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli_mod
from app.services import conversations_service as convs
from app.main import app
from app.ws import chat_socket

client = TestClient(app)


def _conv(temp_db):
    return client.post("/api/conversations", json={}).json()["id"]


def test_busy_guard_blocks_second_send(temp_db, monkeypatch):
    """A second send while the first is in-flight gets an error response."""
    can_finish = threading.Event()

    async def fake_run(**kwargs):
        await asyncio.to_thread(can_finish.wait, 5.0)
        yield {"type": "text", "text": "done"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "First"})
        time.sleep(0.1)  # let the task start and block
        ws.send_json({"type": "send", "message": "Second"})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert "already processing" in resp["error"].lower()
        can_finish.set()


def test_stop_lands_mid_stream(temp_db, monkeypatch):
    """Stop is receivable while _handle_send is suspended — proves fire-and-forget.

    If the receive loop were blocking on _handle_send (i.e. awaiting the task
    instead of fire-and-forgetting it), the 60-second sleep would hold it for
    60 seconds before it could read the stop message, and receive_json() here
    would timeout. The test passing quickly proves the concurrency contract.
    """

    async def fake_run(**kwargs):
        await asyncio.sleep(60)
        yield {"type": "text", "text": ""}  # never reached

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "Hello"})
        time.sleep(0.1)  # let the task start and enter the sleep
        ws.send_json({"type": "stop"})
        resp = ws.receive_json()
        assert resp["type"] == "stopped"
        assert resp["did_stop"] is True


def test_reply_persists_via_finally_block(temp_db, monkeypatch):
    """_handle_send's finally block always commits accumulated text to the DB.

    This tests the persistence guarantee that makes a browser-refresh safe:
    even if the socket raises RuntimeError on send_json (simulated here by
    letting the WS session close while events are still being forwarded), the
    finally block writes the reply to DB.

    Note: TestClient's anyio task scope cancels background asyncio.Tasks when
    the WS handler returns, so we can't test true mid-stream persistence here
    (that would require a live uvicorn server). What we CAN test is the finally
    block itself: it runs on normal completion, on exception, and on cancellation
    — so any disruption still commits whatever text was accumulated.
    """
    gen_exhausted = threading.Event()

    async def fake_run(**kwargs):
        yield {"type": "text", "text": "persisted reply"}
        gen_exhausted.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "Hello"})
        resp = ws.receive_json()
        assert resp == {"type": "text", "text": "persisted reply"}
        assert gen_exhausted.wait(timeout=2.0), "generator never exhausted"
        time.sleep(0.05)  # let the finally block commit before WS closes

    messages = convs.list_messages(conv_id)
    assert any(m.role == "assistant" and "persisted" in m.content for m in messages)


def test_user_message_persisted_before_subprocess(temp_db, monkeypatch):
    """User message is in the DB before the generator yields its first event."""
    user_msg_present_at_gen_start = threading.Event()
    gen_done = threading.Event()
    conv_id = _conv(temp_db)

    async def fake_run(**kwargs):
        msgs = convs.list_messages(conv_id)
        if any(m.role == "user" for m in msgs):
            user_msg_present_at_gen_start.set()
        yield {"type": "text", "text": "hi"}
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "Hello"})
        assert user_msg_present_at_gen_start.wait(timeout=2.0)

    assert user_msg_present_at_gen_start.is_set(), "user message not in DB before generator ran"


def test_conversation_not_found_error(temp_db, monkeypatch):
    async def fake_run(**kwargs):
        yield {"type": "text", "text": ""}  # pragma: no cover

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)

    with client.websocket_connect("/ws/chat/no-such-id") as ws:
        ws.send_json({"type": "send", "message": "Hello"})
        resp = ws.receive_json()
    assert resp["type"] == "error"
    assert "conversation not found" in resp["error"].lower()


def test_empty_message_error(temp_db, monkeypatch):
    async def fake_run(**kwargs):
        yield {"type": "text", "text": ""}  # pragma: no cover

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "   "})
        resp = ws.receive_json()
    assert resp["type"] == "error"
    assert "empty message" in resp["error"].lower()


# ===========================================================================
# Token accounting and tool-call persistence.
#
# These were previously proven only by reading chat_socket.py, which is a proof
# that dies at the context boundary — every session re-read the same five files
# to re-confirm them. These tests make that permanent and cheap.
# ===========================================================================

def _usage(inp, out, cache_read=0, cache_creation=0):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }


def _run_turn(client_, conv_id, events, gen_done):
    """Send one message, drain the socket until `done`, let the finally block commit."""
    with client_.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "go"})
        for _ in range(len(events) + 4):  # +slack for the synthetic done
            if ws.receive_json().get("type") == "done":
                break
        assert gen_done.wait(timeout=2.0), "generator never exhausted"
        time.sleep(0.05)  # finally block commits before the WS closes


def _assistant_row(conv_id):
    rows = [m for m in convs.list_messages(conv_id) if m.role == "assistant"]
    assert len(rows) == 1, f"expected exactly one assistant row, got {len(rows)}"
    return rows[0]


def test_tool_only_turn_persists_assistant_row(temp_db, monkeypatch):
    """A turn producing ONLY tool calls — no text, no thinking — must still persist.

    Before the fix this wrote nothing at all: the row, its tool calls and its
    tokens were discarded, which silently understated cost and made tool
    activity vanish on reload.
    """
    gen_done = threading.Event()
    events = [
        {"type": "tool_call", "id": "t1", "name": "Read", "input": {"file_path": "/tmp/a"}},
        {"type": "tool_result", "tool_use_id": "t1", "content": "file body", "is_error": False},
        {"type": "usage", "usage": _usage(12, 34)},
    ]

    async def fake_run(**kwargs):
        for e in events:
            yield e
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    _run_turn(client, conv_id, events, gen_done)

    row = _assistant_row(conv_id)
    assert not row.content, "no text was produced, so content must be empty"
    assert row.tool_calls, "tool_calls JSON must be persisted"
    calls = json.loads(row.tool_calls)
    assert len(calls) == 1
    assert calls[0]["name"] == "Read"
    assert calls[0]["input"] == {"file_path": "/tmp/a"}
    assert calls[0]["output"] == "file body"
    assert calls[0]["is_error"] is False
    assert row.output_tokens == 34
    assert row.input_tokens == 12


def test_usage_events_sum_output_tokens_across_a_multi_step_turn(temp_db, monkeypatch):
    """output_tokens must ACCUMULATE, not be overwritten by the last event.

    A multi-step tool turn emits one usage block per assistant message. If the
    handler replaced instead of summing, only the final step's tokens were
    billed — undercounting every agentic turn.
    """
    gen_done = threading.Event()
    events = [
        {"type": "text", "text": "step one "},
        {"type": "usage", "usage": _usage(100, 10)},
        {"type": "text", "text": "step two"},
        {"type": "usage", "usage": _usage(100, 25)},
    ]

    async def fake_run(**kwargs):
        for e in events:
            yield e
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    _run_turn(client, conv_id, events, gen_done)

    row = _assistant_row(conv_id)
    assert row.output_tokens == 35, "10 + 25 — replacing would give 25"
    assert row.input_tokens == 100, "input is cumulative per event, so take the latest"
    assert row.content == "step one step two"


def test_result_event_overrides_accumulated_usage(temp_db, monkeypatch):
    """The result event carries authoritative cumulative totals and wins outright."""
    gen_done = threading.Event()
    events = [
        {"type": "usage", "usage": _usage(100, 10)},
        {"type": "usage", "usage": _usage(100, 25)},
        {"type": "result", "usage": _usage(500, 999, cache_read=7, cache_creation=3)},
    ]

    async def fake_run(**kwargs):
        for e in events:
            yield e
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    _run_turn(client, conv_id, events, gen_done)

    row = _assistant_row(conv_id)
    assert row.output_tokens == 999, "result overrides the running total"
    assert row.input_tokens == 500
    assert row.cache_read_tokens == 7
    assert row.cache_creation_tokens == 3


def test_cache_tokens_persist_from_usage_events(temp_db, monkeypatch):
    gen_done = threading.Event()
    events = [
        {"type": "text", "text": "hi"},
        {"type": "usage", "usage": _usage(3, 7, cache_read=150055, cache_creation=978)},
    ]

    async def fake_run(**kwargs):
        for e in events:
            yield e
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    _run_turn(client, conv_id, events, gen_done)

    row = _assistant_row(conv_id)
    assert row.cache_read_tokens == 150055
    assert row.cache_creation_tokens == 978


def test_cache_tokens_are_replaced_not_summed_across_usage_events(temp_db, monkeypatch):
    """CHARACTERIZATION — settles an open design question with an assertion.

    Two usage events each report cache_read=1000. Summing gives 2000; taking the
    latest gives 1000. Replacing is correct: within one turn each step re-reads
    the SAME cached prefix, so summing multiply-counts a cost incurred once.

    If this ever needs to become a sum, change it deliberately and change this
    test with it — do not let it drift silently.
    """
    gen_done = threading.Event()
    events = [
        {"type": "usage", "usage": _usage(5, 1, cache_read=1000)},
        {"type": "usage", "usage": _usage(5, 1, cache_read=1000)},
    ]

    async def fake_run(**kwargs):
        for e in events:
            yield e
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    _run_turn(client, conv_id, events, gen_done)

    row = _assistant_row(conv_id)
    assert row.cache_read_tokens == 1000, "replaced, not summed (2000 would multiply-count)"
    assert row.output_tokens == 2, "output still sums: 1 + 1"


# ===========================================================================
# /stop mid-stream: partial text + tokens must persist, marked as stopped.
# ===========================================================================

def test_stop_mid_stream_persists_partial_text_and_tokens_with_marker(temp_db, monkeypatch):
    """Real cancellation via registry.stop(): text+usage accumulated before the
    stop must survive, and the row must carry the stopped marker so the
    transcript never implies Claude finished the thought."""
    resumed = threading.Event()

    async def fake_run(**kwargs):
        yield {"type": "usage", "usage": _usage(50, 20)}
        yield {"type": "text", "text": "partial answer"}
        resumed.set()
        await asyncio.sleep(60)
        yield {"type": "text", "text": "SHOULD NOT APPEAR"}  # pragma: no cover

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "Hello"})
        ws.receive_json()  # usage event echoed
        ws.receive_json()  # text event echoed
        assert resumed.wait(timeout=2.0), "generator never reached the pre-cancel point"
        ws.send_json({"type": "stop"})
        stop_resp = ws.receive_json()
        assert stop_resp["type"] == "stopped"
        assert stop_resp["did_stop"] is True
        time.sleep(0.2)  # let the cancelled task's finally block commit

    row = _assistant_row(conv_id)
    assert row.content == "partial answer" + chat_socket.STOPPED_MARKER
    assert row.input_tokens == 50
    assert row.output_tokens == 20


def test_stop_before_any_content_persists_nothing(temp_db, monkeypatch):
    """A stop with nothing accumulated yet must not create a blank row."""
    started = threading.Event()

    async def fake_run(**kwargs):
        started.set()
        await asyncio.sleep(60)
        yield {"type": "text", "text": "unused"}  # pragma: no cover

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "Hello"})
        assert started.wait(timeout=2.0)
        ws.send_json({"type": "stop"})
        stop_resp = ws.receive_json()
        assert stop_resp["type"] == "stopped"
        time.sleep(0.2)

    assert [m for m in convs.list_messages(conv_id) if m.role == "assistant"] == []


def test_normal_completion_not_marked_stopped(temp_db, monkeypatch):
    """The stopped marker must not leak into turns that complete on their own."""
    gen_done = threading.Event()
    events = [{"type": "text", "text": "all done"}]

    async def fake_run(**kwargs):
        for e in events:
            yield e
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    _run_turn(client, conv_id, events, gen_done)

    row = _assistant_row(conv_id)
    assert row.content == "all done"
    assert "claudioui:stopped" not in row.content


def test_turn_with_no_content_and_no_usage_persists_nothing(temp_db, monkeypatch):
    """The gate must stay a gate — an empty turn should not create a blank row."""
    gen_done = threading.Event()

    async def fake_run(**kwargs):
        if False:  # pragma: no cover — yields nothing, stays an async generator
            yield {}
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    _run_turn(client, conv_id, [], gen_done)

    assert [m for m in convs.list_messages(conv_id) if m.role == "assistant"] == []
