"""WebSocket edge-case tests for /ws/chat/{conversation_id} (app/ws/chat_socket.py).

ENUMERATION (content quoted; line numbers are pointers, not proof of position).

Server -> client event types emitted by chat_socket -- 14 distinct:

  "stopped"            chat_socket.py:90    {"type": "stopped", "did_stop": bool}
  "done"               chat_socket.py:93, :157, :175, :183, :213, :438
                       (also forwarded from claude_cli at :419)
  "error"              chat_socket.py:97, :101, :153, :174, :180, :210, :430
                       (also forwarded at :419)
  "approval_needed"    chat_socket.py:323   event dict + timeout/remaining
  "approval_extended"  chat_socket.py:340
  "text"               chat_socket.py:362   (approval-timeout notice)
                       (also forwarded at :419)
  "thinking"           forwarded at chat_socket.py:419
  "tool_call"          forwarded at chat_socket.py:419
  "tool_result"        forwarded at chat_socket.py:419
  "session"            forwarded at chat_socket.py:419
  "usage"              forwarded at chat_socket.py:419
  "result"             forwarded at chat_socket.py:419
  "notice"             forwarded at chat_socket.py:419
  "timeout"            forwarded at chat_socket.py:419

  The forwarded set is claude_cli.run's output. stream_parser.py normalizes to
  session / approval_needed / error / result / thinking / text / tool_call /
  tool_result / usage / notice; claude_cli.run adds timeout and done.

Client -> server message types handled by chat_socket -- 5 distinct:

  "approve", "deny", "approval_extend"     chat_socket.py:77
  "stop"                                   chat_socket.py:88
  "send"                                   chat_socket.py:96
  any other type -> {"type": "error", "error": "Unknown message type: ..."}
          chat_socket.py:96-98

frontend/static/js/api/socket.js:

  Explicit inbound names: "done", "error", "stopped", "timeout"
      (TERMINAL_TYPES, socket.js:9). It forwards every frame generically via
      `_emit(data.type, data)` (socket.js:33); it has no other per-type code.
  Outbound message types it sends: "send" (socket.js:157), "stop" (:177),
      "approve" (:181), "deny" (:182), "approval_extend" (:190).

  Emitted by the server but NOT named/handled in socket.js (10):
      approval_needed, approval_extended, text, thinking, tool_call,
      tool_result, usage, result, notice, session.
  "session" is the only server event with no listener anywhere in the frontend:
      it is emitted at chat_socket.py:419 but no JS file registers
      `.on("session")` (the value is consumed server-side at
      chat_socket.py:369-370 and still forwarded). Every other server event has
      a listener.
  Handled but never emitted: none -- all 5 outbound types socket.js sends are
      handled by the server, and all 4 terminal types it watches are emitted.

HOW THESE TESTS DRIVE THE SOCKET (pattern copied from test_ws_chat_socket.py):
the TestClient, a per-test temp_db from conftest.py, and claude_cli.run
monkeypatched with an async generator, so no real CLI or network is used.

Test 1 is the one exception: Starlette's TestClient cancels the WS handler's
fire-and-forget `_handle_send` task when the `websocket_connect` context exits,
which the production server does NOT do. A TestClient "close mid-stream" test
therefore cannot observe the persistence guarantee at all (verified: only the
partial text survives). Test 1 drives chat_socket.handle_chat_socket directly
with a WebSocket double whose receive_json() raises WebSocketDisconnect and
whose send_json() then raises RuntimeError -- the same shape the handler is
built to tolerate -- so the guarantee can be exercised.
"""

import asyncio
import threading
import time

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli_mod
from app.main import app
from app.services import conversations_service as convs
from app.services.process_registry import registry
from app.ws import chat_socket

client = TestClient(app)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _conv(temp_db) -> str:
    """Create a conversation (temp_db guarantees the DB is the throwaway one)."""
    return client.post("/api/conversations", json={}).json()["id"]


def _assistants(conv_id: str) -> list:
    return [m for m in convs.list_messages(conv_id) if m.role == "assistant"]


def _wait_until(pred, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def _read_n(ws, n: int, timeout: float = 5.0):
    """Read exactly `n` frames on a daemon thread; never block the suite forever.

    Returns (frames, still_reading). `frames` may be shorter than `n` (or contain
    a {"type": "_exception", ...} marker) if the socket died first. Copied from
    test_ws_chat_socket.py's _read_frames for the same reason it exists there:
    Starlette's receive_json() takes no timeout and a read for a frame the server
    never sends would wedge the whole pytest process.
    """
    frames: list = []

    def _reader() -> None:
        try:
            for _ in range(n):
                frames.append(ws.receive_json())
        except BaseException as exc:  # noqa: BLE001 -- surfaced through `frames`
            frames.append({"type": "_exception", "exc": repr(exc)})

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout)
    return frames, t.is_alive()


def _collect_until(ws, predicate, timeout: float = 5.0, max_frames: int = 50):
    """Read until `predicate(frames)` holds, the socket errors, or timeout."""
    frames: list = []

    def _reader() -> None:
        try:
            while len(frames) < max_frames:
                frames.append(ws.receive_json())
                if predicate(frames):
                    return
        except BaseException as exc:  # noqa: BLE001
            frames.append({"type": "_exception", "exc": repr(exc)})

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout)
    return frames, t.is_alive()


def _transcript(conv_id: str) -> list:
    return [(m.role, m.content, m.stopped) for m in convs.list_messages(conv_id)]


class _FakeStdin:
    def is_closing(self) -> bool:
        return False

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        return None


class _FakeProc:
    """Records kill() so a test can prove the subprocess was terminated."""

    def __init__(self) -> None:
        self.killed = False
        self.stdin = _FakeStdin()

    def kill(self) -> None:
        self.killed = True


# --------------------------------------------------------------------------
# 1. client disconnects mid-stream -> reply still completes and is persisted
# --------------------------------------------------------------------------

class _DisconnectingWebSocket:
    """Accepts one `send` frame, then reports the client as gone.

    After the disconnect every send_json() raises RuntimeError -- exactly what
    Starlette does when a frame is sent on a websocket the peer has closed, and
    exactly the exception chat_socket wraps in contextlib.suppress().
    """

    def __init__(self, first_message: dict) -> None:
        self._first = first_message
        self._receive_calls = 0
        self.frames: list = []
        self.disconnected = False

    async def accept(self) -> None:
        pass

    async def receive_json(self):
        self._receive_calls += 1
        if self._receive_calls == 1:
            return self._first
        self.disconnected = True
        raise WebSocketDisconnect()

    async def send_json(self, payload: dict) -> None:
        if self.disconnected:
            raise RuntimeError("cannot send on a websocket closed by the client")
        self.frames.append(payload)


def test_disconnect_mid_stream_reply_still_completes_and_is_persisted(temp_db, monkeypatch):
    """A closed socket must not lose the reply: it finishes headless and commits.

    chat_socket.py:112-116 owns this guarantee ("a browser refresh mid-response
    shouldn't lose the reply"). The send-side is wrapped in suppress(RuntimeError)
    at :418 so forwarding keeps draining after the client is gone, and the
    finally block at :433 commits whatever the generator produced.
    """
    gen_released = threading.Event()

    async def fake_run(**kwargs):
        yield {"type": "text", "text": "chunk1 "}
        await asyncio.sleep(0.05)  # window for the disconnect to land
        yield {"type": "text", "text": "chunk2 "}
        yield {"type": "text", "text": "chunk3"}
        gen_released.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    ws = _DisconnectingWebSocket({"type": "send", "message": "go"})

    async def drive() -> None:
        await chat_socket.handle_chat_socket(ws, conv_id)
        # handle_chat_socket returned on WebSocketDisconnect; the fire-and-forget
        # task is still owed. Await it the way a live server's loop would.
        task = registry._tasks.get(conv_id)
        if task is not None and not task.done():
            await asyncio.wait_for(task, timeout=5)

    asyncio.run(drive())

    assert gen_released.is_set(), "the generator never ran to completion after disconnect"
    rows = _assistants(conv_id)
    assert len(rows) == 1, f"expected exactly one assistant row, got {len(rows)}"
    assert rows[0].content == "chunk1 chunk2 chunk3", (
        f"the reply was truncated at the disconnect: {rows[0].content!r}"
    )
    assert rows[0].stopped is False, "a reply that ran to completion must not be marked stopped"


# --------------------------------------------------------------------------
# 2. send, stop, send within 100 ms
# --------------------------------------------------------------------------

def test_send_stop_send_within_100ms_one_process_per_accepted_send(temp_db, monkeypatch):
    """Rapid send/stop/send: two accepted sends -> two processes, stop honoured,
    and neither the stopped turn's partial text nor the second turn is lost.

    NOTE: a ~10 ms gap is left between stop and the follow-up send. The stop
    frame and the next send are separate network frames in production; sending
    them in the same TestClient batch (verified, unverified as a product bug)
    hits an un-exercised race where registry.clear() has not run yet and the
    follow-up is rejected as busy. That is not what "within 100 ms" needs to
    mean here, and this test pins the stronger, intended behaviour.
    """
    run_prompts: list[str] = []
    turn1_started = threading.Event()
    release_turn1 = threading.Event()
    turn1_cancelled = threading.Event()

    async def fake_run(**kwargs):
        run_prompts.append(kwargs["prompt"])
        if len(run_prompts) == 1:
            turn1_started.set()
            try:
                yield {"type": "text", "text": "partial-one"}
                await asyncio.to_thread(release_turn1.wait, 5.0)
                yield {"type": "text", "text": "tail-one"}
            except asyncio.CancelledError:
                turn1_cancelled.set()
                raise
        else:
            yield {"type": "text", "text": "answer-two"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    expected = [
        ("user", "one", False),
        ("assistant", "partial-one", True),
        ("user", "two", False),
        ("assistant", "answer-two", False),
    ]

    frames: list = []
    persisted = False
    try:
        with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
            ws.send_json({"type": "send", "message": "one"})
            assert turn1_started.wait(2.0), "first turn never reached the CLI"

            stop_sent = time.perf_counter()
            ws.send_json({"type": "stop"})
            time.sleep(0.01)
            ws.send_json({"type": "send", "message": "two"})
            window = time.perf_counter() - stop_sent
            assert window < 0.1, f"stop -> send took {window:.3f}s, not < 100 ms"

            frames, _ = _collect_until(
                ws,
                lambda fs: any(f.get("type") == "text" and f.get("text") == "answer-two" for f in fs),
                timeout=5.0,
            )
            # The DB row is written AFTER `done` is sent (chat_socket.py:438 vs
            # :467), so wait for it here -- exiting the socket context cancels a
            # still-running task and would flip the stopped flag.
            persisted = _wait_until(lambda: _transcript(conv_id) == expected)
    finally:
        release_turn1.set()

    assert run_prompts == ["one", "two"], (
        f"expected exactly one CLI process per accepted send, got {run_prompts!r} "
        f"(frames={[f.get('type') for f in frames]!r})"
    )
    assert turn1_cancelled.is_set(), "the stop was not honoured: turn 1 was never cancelled"
    assert any(
        f.get("type") == "text" and f.get("text") == "answer-two" for f in frames
    ), f"the second turn's reply never reached the client: {frames!r}"
    assert persisted, f"nothing must be lost; transcript={_transcript(conv_id)!r}"
    assert _transcript(conv_id) == expected


# --------------------------------------------------------------------------
# 3. a second send while busy
# --------------------------------------------------------------------------

def test_second_send_while_busy_rejected_and_no_second_process(temp_db, monkeypatch):
    """A second send during a live turn gets the busy error and spawns nothing."""
    run_prompts: list[str] = []
    started = threading.Event()
    release = threading.Event()

    async def fake_run(**kwargs):
        run_prompts.append(kwargs["prompt"])
        started.set()
        await asyncio.to_thread(release.wait, 5.0)
        yield {"type": "text", "text": "first-reply"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)
    rest: list = []

    try:
        with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
            ws.send_json({"type": "send", "message": "first"})
            assert started.wait(2.0), "first turn never reached the CLI"

            ws.send_json({"type": "send", "message": "second"})
            busy, _ = _read_n(ws, 1, timeout=3.0)
            assert busy and busy[0].get("type") == "error", f"expected a busy error, got {busy!r}"
            assert "already answering" in busy[0]["error"].lower(), busy[0]

            release.set()
            rest, _ = _read_n(ws, 2, timeout=5.0)
    finally:
        release.set()

    assert run_prompts == ["first"], f"the rejected send spawned a process: {run_prompts!r}"
    assert any(f.get("type") == "text" and f.get("text") == "first-reply" for f in rest), rest


# --------------------------------------------------------------------------
# 4. a reply larger than 1 MB
# --------------------------------------------------------------------------

def test_reply_larger_than_1mb_persisted_and_delivered_without_truncation(temp_db, monkeypatch):
    """>1 MB of streamed text must arrive whole and be stored whole."""
    chunks = ["c" * 32768 for _ in range(40)]  # 1_310_720 chars > 1 MiB
    expected = "".join(chunks)

    async def fake_run(**kwargs):
        for chunk in chunks:
            yield {"type": "text", "text": chunk}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "go"})
        frames, alive = _collect_until(
            ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=15.0, max_frames=200
        )
        # Persistence runs after `done` (chat_socket.py:438 vs :450-462); wait for
        # it inside the context so closing the socket cannot cancel the write.
        stored = _wait_until(
            lambda: bool(_assistants(conv_id)) and _assistants(conv_id)[0].stopped is False,
            timeout=10.0,
        )

    delivered = "".join(f["text"] for f in frames if f.get("type") == "text")
    assert not alive, "the turn never finished"
    assert len(expected) > 1024 * 1024
    assert delivered == expected, (
        f"delivered {len(delivered)} chars of {len(expected)} -- truncated over the wire"
    )
    assert stored, "the assistant row was never committed"
    rows = _assistants(conv_id)
    assert len(rows) == 1
    assert rows[0].content == expected, (
        f"stored {len(rows[0].content)} chars of {len(expected)} -- truncated in the DB"
    )


# --------------------------------------------------------------------------
# 5. socket closed while an approval_needed is pending
# --------------------------------------------------------------------------

def test_socket_closed_during_pending_approval_terminates_the_subprocess(temp_db, monkeypatch):
    """Closing the socket with an approval pending must not leave the CLI running.

    The turn cannot proceed without an answer the client can no longer give, so
    the subprocess must be terminated. This asserts the process registered via
    on_process_started receives kill() -- i.e. chat_socket actually stops it.
    """
    procs: list[_FakeProc] = []

    async def fake_run(**kwargs):
        on_started = kwargs.get("on_process_started")
        proc = _FakeProc()
        procs.append(proc)
        if on_started:
            on_started(proc)
        yield {"type": "approval_needed", "tool": "Bash", "action": "rm -rf ."}
        await asyncio.sleep(60)  # blocked on stdin, as the real CLI would be
        yield {"type": "text", "text": "after"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "do something"})
        first = ws.receive_json()
        assert first["type"] == "approval_needed", first
    # socket closed here, approval still unanswered

    assert _wait_until(lambda: bool(procs)), "the CLI process was never started"
    assert _wait_until(lambda: procs[0].killed, timeout=2.0), (
        "the CLI subprocess was NOT terminated after the socket closed; it is left "
        "blocked on stdin until the approval timeout (and a task cancellation does "
        "not reach the generator suspended at its yield, so the real subprocess "
        "would be orphaned)"
    )


# --------------------------------------------------------------------------
# 6. malformed client frames
# --------------------------------------------------------------------------

def test_malformed_invalid_json_does_not_kill_the_connection(temp_db, monkeypatch):
    """A non-JSON text frame must be ignored or answered, never fatal.

    Starlette's receive_json() calls json.loads() and raises json.JSONDecodeError
    (a ValueError). chat_socket's receive loop (chat_socket.py:74) only catches
    WebSocketDisconnect around the loop, so that exception escapes
    handle_chat_socket and the socket dies. Correct behaviour: the connection
    survives and a following valid send is still served.
    """

    async def fake_run(**kwargs):
        yield {"type": "text", "text": "after-bad-json"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_text("}{ this is not valid JSON")
        ws.send_json({"type": "send", "message": "after bad json"})
        frames, _ = _collect_until(
            ws,
            lambda fs: any(f.get("type") == "text" for f in fs),
            timeout=3.0,
            max_frames=10,
        )

    assert any(f.get("type") == "text" and f.get("text") == "after-bad-json" for f in frames), (
        f"a malformed frame killed the connection instead of being ignored/reported: {frames!r}"
    )


def test_malformed_unknown_type_gets_defined_error_and_connection_survives(temp_db, monkeypatch):
    """An unknown `type` gets an explicit error frame; the socket stays usable."""

    async def fake_run(**kwargs):
        yield {"type": "text", "text": "still-alive"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "totally-made-up"})
        first, _ = _read_n(ws, 1, timeout=3.0)
        assert first and first[0].get("type") == "error", first
        assert "didn't understand" in first[0]["error"].lower(), first[0]

        ws.send_json({"type": "send", "message": "still alive?"})
        rest, _ = _collect_until(
            ws, lambda fs: any(f.get("type") == "text" for f in fs), timeout=3.0
        )

    assert any(f.get("type") == "text" and f.get("text") == "still-alive" for f in rest), (
        f"the socket did not survive an unknown type: {rest!r}"
    )


def test_malformed_missing_required_fields_get_defined_error_and_connection_survives(temp_db, monkeypatch):
    """A `send` with no message and a frame with no `type` each get an error frame,
    and the connection is still usable afterwards."""

    async def fake_run(**kwargs):
        yield {"type": "text", "text": "still-alive"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send"})  # required field "message" absent
        no_message, _ = _read_n(ws, 2, timeout=3.0)
        assert [f.get("type") for f in no_message] == ["error", "done"], no_message
        assert "no text" in no_message[0]["error"].lower(), no_message[0]

        ws.send_json({"not_a_type": True})  # required field "type" absent
        no_type, _ = _read_n(ws, 1, timeout=3.0)
        assert no_type and no_type[0].get("type") == "error", no_type
        assert "didn't understand" in no_type[0]["error"].lower(), no_type[0]

        ws.send_json({"type": "send", "message": "still alive?"})
        rest, _ = _collect_until(
            ws, lambda fs: any(f.get("type") == "text" for f in fs), timeout=3.0
        )

    assert any(f.get("type") == "text" and f.get("text") == "still-alive" for f in rest), (
        f"the socket did not survive the malformed frames: {rest!r}"
    )


def test_malformed_very_large_payload_is_accepted_without_killing_connection(temp_db, monkeypatch):
    """A ~2 MB send is a valid frame: the whole prompt reaches the CLI, the turn
    answers, and the connection is not killed by the size."""
    big_prompt = "y" * 2_000_000
    seen_prompts: list[str] = []

    async def fake_run(**kwargs):
        seen_prompts.append(kwargs["prompt"])
        yield {"type": "text", "text": "got-it"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": big_prompt})
        frames, _ = _collect_until(
            ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=15.0
        )

    assert seen_prompts == [big_prompt], (
        f"the large prompt was truncated or dropped: {[len(p) for p in seen_prompts]!r}"
    )
    assert any(f.get("type") == "text" and f.get("text") == "got-it" for f in frames), frames


# --------------------------------------------------------------------------
# 7. a conversation_id that does not exist
# --------------------------------------------------------------------------

def test_nonexistent_conversation_gets_clean_rejection(temp_db, monkeypatch):
    """Connect accepts (chat_socket.py:65 accepts before it can know), then a
    `send` is rejected with a defined error frame plus `done` -- no hang, no
    silent nothing, and the socket is not dropped."""
    missing_id = "00000000-0000-4000-8000-does-not-exist"

    async def fake_run(**kwargs):  # pragma: no cover -- must never be reached
        yield {"type": "text", "text": ""}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)

    with client.websocket_connect(f"/ws/chat/{missing_id}") as ws:
        ws.send_json({"type": "send", "message": "hello"})
        frames, alive = _read_n(ws, 2, timeout=3.0)

    assert not alive, f"expected exactly two frames, got {frames!r}"
    assert [f.get("type") for f in frames] == ["error", "done"], frames
    assert "conversation no longer exists" in frames[0]["error"].lower(), frames[0]
    assert "start a new chat" in frames[0]["error"].lower(), frames[0]


# --------------------------------------------------------------------------
# 8. two sockets on the same conversation at once
# --------------------------------------------------------------------------

def test_two_sockets_same_conversation_share_busy_state(temp_db, monkeypatch):
    """CHARACTERIZATION -- asserts the behaviour the code actually has.

    Both sockets are accepted (there is no connect-time exclusivity). The process
    registry is keyed by conversation_id (process_registry.py:32-47), so while
    socket A has a turn in flight, a `send` on socket B is rejected with the busy
    error, and stream frames are delivered only to the socket that owns the turn
    -- B receives no events from A's stream.
    """
    started = threading.Event()
    release = threading.Event()
    b_frames: list = []

    async def fake_run(**kwargs):
        started.set()
        yield {"type": "text", "text": "socket-a-reply"}
        await asyncio.to_thread(release.wait, 5.0)

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    conv_id = _conv(temp_db)

    try:
        with client.websocket_connect(f"/ws/chat/{conv_id}") as wa, \
                client.websocket_connect(f"/ws/chat/{conv_id}") as wb:
            wa.send_json({"type": "send", "message": "from-a"})
            assert started.wait(2.0), "A's turn never reached the CLI"

            a_first, _ = _read_n(wa, 1, timeout=3.0)
            assert a_first and a_first[0].get("text") == "socket-a-reply", a_first

            wb.send_json({"type": "send", "message": "from-b"})
            b_frames, _ = _read_n(wb, 1, timeout=3.0)
            assert b_frames and b_frames[0].get("type") == "error", b_frames
            assert "already answering" in b_frames[0]["error"].lower(), b_frames[0]

            release.set()
            a_last, _ = _read_n(wa, 1, timeout=5.0)
            assert a_last and a_last[0].get("type") == "done", a_last
    finally:
        release.set()

    # B's only frame was the busy error: it never saw A's stream event.
    assert [f.get("type") for f in b_frames] == ["error"], (
        f"the second socket received frames from the first socket's turn: {b_frames!r}"
    )
