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
from app.db.connection import get_connection
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


# ===========================================================================
# Regenerate — task 2.1's write path.
#
# These pin the defect that was MEASURED on 2026-09-17 14:05, not reasoned about:
# regenerate was routed through the ordinary send path, so the question was
# written to the DB a second time, the transcript read [user][user][assistant],
# and the duplicate survived a reload because it was a real row rather than a DOM
# artifact. The fix moved the authority to the server: a `regenerate: true`
# intent re-reads the stored question, never re-writes it, and supersedes the
# previous answer inside the same turn.
# ===========================================================================

def _read_frames(ws, label: str, max_frames: int = 6, timeout: float = 5.0):
    """Read frames off the socket with a deadline. Never blocks forever.

    Starlette's TestClient `receive_json()` takes no timeout in the installed
    version, so a read for a frame the server never sends does not fail — it blocks
    the entire suite. That is measured, not feared: it wedged three pytest
    processes during this session (empty output files, killed by hand), and
    faulthandler parked the blocked thread in `queue.get()` inside
    `starlette/testclient.py:205`.

    The read runs on a short-lived daemon thread so it can be abandoned, and the
    caller gets `timed_out=True` instead of a hang. Callers assert on the result
    *after* the socket is closed, because an exception raised inside the
    `websocket_connect` context is masked by TestClient joining the ASGI thread on
    exit — a failing assertion there hangs rather than reports.
    """
    frames: list = []
    result: dict = {"timed_out": False, "error": None}

    def _read() -> None:
        try:
            while len(frames) < max_frames:
                msg = ws.receive_json()
                frames.append(msg)
                if isinstance(msg, dict) and msg.get("type") == "done":
                    return
        except BaseException as exc:  # noqa: BLE001 — surfaced through `result`
            result["error"] = exc

    reader = threading.Thread(target=_read, name=f"ws-read-{label}", daemon=True)
    reader.start()
    reader.join(timeout)
    result["timed_out"] = reader.is_alive()
    return frames, result


def _stored_row_count(conv_id: str) -> int:
    """Every row on disk, superseded ones included."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,)
        ).fetchone()[0]


def _wait_until(pred, timeout: float = 5.0) -> bool:
    """Poll `pred` until it holds, or the deadline passes. No blind sleeps."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def test_regenerate_does_not_duplicate_the_user_turn(temp_db, monkeypatch):
    """Regenerate re-asks the stored question; the live transcript stays [user, assistant]."""
    turns: list[str] = []

    async def fake_run(**kwargs):
        turns.append(kwargs["prompt"])
        yield {"type": "text", "text": f"answer {len(turns)}"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "the question"})
        first_frames, first_read = _read_frames(ws, "first-turn")
        # The assistant row is written in _handle_send's finally block, AFTER the
        # `done` frame is sent (chat_socket.py:258 vs :266). Waiting on the row
        # instead of sleeping is what makes the next send deterministic:
        # registry.clear() runs before the row is written, so a stored row proves
        # the busy guard is already released — no 50 ms race, no flake.
        first_persisted = _wait_until(lambda: _stored_row_count(conv_id) == 2)

        # Second turn on the same conversation is a regenerate. The payload still
        # carries the question text, exactly as the frontend now sends it, and
        # that is the point: the text in the payload must NOT be what gets written.
        if first_read["timed_out"]:
            # The reader thread is still parked on the socket queue; sending again
            # would let it steal the next turn's frames, so skip the second turn and
            # report the first one as the failure it is.
            second_frames: list = []
            second_read: dict = {"timed_out": True, "error": None}
            second_persisted = False
        else:
            ws.send_json({"type": "send", "message": "the question", "regenerate": True})
            second_frames, second_read = _read_frames(ws, "regenerate-turn")
            second_persisted = _wait_until(lambda: _stored_row_count(conv_id) > 2)

    # Assertions live outside the `with` so a failure reports instead of hanging.
    assert not first_read["timed_out"], f"first turn never answered; frames={first_frames!r}"
    assert first_persisted, f"first turn never persisted; frames={first_frames!r}"
    assert not second_read["timed_out"], (
        f"regenerate turn never answered (the server sent nothing); frames={second_frames!r}"
    )
    assert second_persisted, f"regenerate turn never persisted; frames={second_frames!r}"
    assert len(turns) == 2, f"the regenerate turn never reached the CLI; turns={turns!r}"

    assert [m.role for m in convs.list_messages(conv_id)] == ["user", "assistant"]

    # Superseded, not deleted: both assistant rows remain on disk so the
    # accounting views keep counting the money that was actually spent.
    with get_connection() as conn:
        stored = conn.execute(
            "SELECT role, superseded_by FROM messages WHERE conversation_id = ? ORDER BY seq ASC",
            (conv_id,),
        ).fetchall()
    assert [r["role"] for r in stored] == ["user", "assistant", "assistant"]
    assert stored[1]["superseded_by"] is not None, "the replaced answer must be marked superseded"
    assert stored[2]["superseded_by"] is None, "the new answer must be the live one"


def test_regenerate_with_no_prior_question_reports_it(temp_db, monkeypatch):
    """An empty conversation cannot be regenerated — say so instead of writing nothing.

    Also pins the guard's shape: a regenerate carries an empty `message` by
    design, so the empty-message check must not fire first and blame the user for
    a blank prompt they never typed.
    """

    async def fake_run(**kwargs):  # pragma: no cover — must never be reached
        yield {"type": "text", "text": ""}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "", "regenerate": True})
        frames, read = _read_frames(ws, "no-prior-question", max_frames=2)

    assert not read["timed_out"], f"the guard answered nothing; frames={frames!r}"
    assert [f.get("type") for f in frames] == ["error", "done"], f"unexpected frames: {frames!r}"
    assert "no previous question" in frames[0]["error"].lower()
    assert convs.list_messages(conv_id) == []

def test_setup_failure_sends_error_then_done_and_leaves_status_idle(temp_db, monkeypatch):
    """N11: a failure in the per-turn SETUP must not make the turn vanish.

    Everything before the streaming `try:` used to run unguarded inside a
    fire-and-forget task. A DB error while loading the conversation therefore
    sent NO frame at all, never cleared the registry, could leave the
    conversation `busy` forever, and was never logged — asyncio does not
    GC-log a task that is still referenced by `registry._tasks`.

    The composer's whole recovery model is "restore on `done`" (see
    chat_socket.py:59-65 and :231-232, which always follow `stopped` and
    `error` with `done`). This asserts that contract holds on the setup path
    too, which is the one place it did not.
    """
    conv_id = _conv(temp_db)
    original_get = convs.get_conversation

    def boom(_cid):
        raise RuntimeError("simulated DB failure during per-turn setup")

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        monkeypatch.setattr(chat_socket.convs, "get_conversation", boom)
        ws.send_json({"type": "send", "message": "this turn dies during setup"})
        first = ws.receive_json()
        second = ws.receive_json()

    assert first["type"] == "error", f"expected an error frame, got {first!r}"
    assert second["type"] == "done", (
        f"expected `done` after the error, got {second!r} — the composer only "
        "restores itself on `done`, so without it the UI stays wedged"
    )

    # Read through the ORIGINAL function; the patched one raises.
    assert original_get(conv_id).status == "idle", (
        "a turn that died during setup left the conversation wedged"
    )

def test_setup_guard_is_what_converts_a_raise_into_error_then_done(temp_db, monkeypatch):
    """N11, proven at the guard itself rather than through a socket.

    The obvious mutation proof — bypass the guard in the dispatcher and watch
    the WS test fail — does not work: without the guard nothing is ever sent,
    so the test HANGS, and `websocket_connect.__exit__` then blocks trying to
    unwind a connection whose server task has already died. A hang is not a
    failure; it is an ambiguous result that wedges the suite.

    So assert the difference directly. The unguarded function must propagate,
    and the guarded one must turn the same failure into `error` then `done`.
    This cannot pass if the guard is removed, which is exactly what a mutation
    proof is for — without a socket that can hang.
    """
    import pytest

    conv_id = _conv(temp_db)
    sent: list[dict] = []

    class _FakeWS:
        async def send_json(self, payload):
            sent.append(payload)

    def boom(_cid):
        raise RuntimeError("simulated DB failure during per-turn setup")

    monkeypatch.setattr(chat_socket.convs, "get_conversation", boom)

    # Pre-fix behaviour: the exception escapes into a fire-and-forget task and
    # the client is told nothing at all.
    with pytest.raises(RuntimeError):
        asyncio.run(
            chat_socket._handle_send_inner(_FakeWS(), conv_id, {"message": "x"}, asyncio.Queue())
        )
    assert sent == [], "the unguarded path must send nothing - that is the defect"

    # Guarded: same failure, but the done-always contract holds.
    asyncio.run(
        chat_socket._handle_send(_FakeWS(), conv_id, {"message": "x"}, asyncio.Queue())
    )
    assert [f["type"] for f in sent] == ["error", "done"], (
        f"expected error then done from the guard, got {[f.get('type') for f in sent]!r}"
    )

