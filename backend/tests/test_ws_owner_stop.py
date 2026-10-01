"""Owner-scoped stop on WebSocket disconnect (app/ws/chat_socket.py).

Regression: the disconnect path used to call registry.stop() for ANY socket
connected to a conversation with an approval pending:

    if conversation_id in _approval_pending:  # only an approval-blocked turn can't finish headless
        registry.stop(conversation_id)

Two browser tabs share a conversation_id but are separate sockets, so closing
the second tab killed the first tab's pending turn. The fix records the
websocket that STARTED the turn (`_turn_owner[conversation_id]`) and lets only
that socket stop the turn by disconnecting. An explicit `stop` frame from any
socket is deliberately unchanged and still stops the turn.

Driven through fastapi.testclient.TestClient -- the same pattern as
test_ws_edge_cases.py -- with claude_cli.run monkeypatched by an async
generator, so no real CLI or network is used.
"""

import asyncio
import threading
import time

from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli_mod
from app.main import app

client = TestClient(app)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _conv(temp_db) -> str:
    """Create a conversation in the throwaway test DB."""
    return client.post("/api/conversations", json={}).json()["id"]


def _wait_until(pred, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def _read_n(ws, n: int, timeout: float = 5.0):
    """Read exactly `n` frames on a daemon thread; never block the suite forever.

    Copied from test_ws_edge_cases.py: Starlette's receive_json() takes no
    timeout and a read for a frame the server never sends would wedge pytest.
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


class _FakeStdin:
    def is_closing(self) -> bool:
        return False

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        return None


class _FakeProc:
    """Records kill() so a test can prove the subprocess was (not) terminated."""

    def __init__(self) -> None:
        self.killed = False
        self.stdin = _FakeStdin()

    def kill(self) -> None:
        self.killed = True


def _patch_pending_approval(monkeypatch, procs: list) -> None:
    """A fake CLI turn that asks for approval and stays blocked on it.

    The handler adds the conversation to `_approval_pending`, sends the
    `approval_needed` frame and awaits the approval queue; the async generator
    is suspended at its yield. That is exactly the in-flight shape the
    disconnect path is about.
    """

    async def fake_run(**kwargs):
        on_started = kwargs.get("on_process_started")
        proc = _FakeProc()
        procs.append(proc)
        if on_started:
            on_started(proc)
        yield {"type": "approval_needed", "tool": "Bash", "action": "rm -rf ."}
        # Reached only after approve/deny/timeout, or never if the turn is
        # stopped. Suspended well past the test's lifetime.
        await asyncio.sleep(60)
        yield {"type": "text", "text": "after"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)


# --------------------------------------------------------------------------
# 1. the non-owning socket's disconnect must NOT stop the turn; the owner's must
# --------------------------------------------------------------------------

def test_non_owner_disconnect_does_not_stop_then_owner_disconnect_does(temp_db, monkeypatch):
    procs: list = []
    _patch_pending_approval(monkeypatch, procs)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as owner:
        owner.send_json({"type": "send", "message": "do something"})
        first, _ = _read_n(owner, 1, timeout=5.0)
        assert first and first[0].get("type") == "approval_needed", first
        assert _wait_until(lambda: bool(procs)), "the CLI process was never started"

        # A second browser tab connects to the same conversation, then closes
        # without sending a `send`. It owns no turn.
        with client.websocket_connect(f"/ws/chat/{conv_id}") as second:
            pass

        # Its disconnect handler has had time to run. It must not have stopped
        # the owner's approval-blocked turn.
        time.sleep(0.3)
        assert procs[0].killed is False, (
            "closing the non-owning socket killed the owner's approval-blocked "
            "CLI process"
        )

    # Leaving the owner's context disconnects the owning socket: now the turn
    # cannot finish headless and the CLI must be stopped.
    assert _wait_until(lambda: procs[0].killed, timeout=3.0), (
        "the owning socket's disconnect did NOT stop the pending CLI process"
    )


# --------------------------------------------------------------------------
# 2. an explicit `stop` from a non-owning socket still stops the turn
# --------------------------------------------------------------------------

def test_stop_from_second_socket_still_stops_the_turn(temp_db, monkeypatch):
    procs: list = []
    _patch_pending_approval(monkeypatch, procs)
    conv_id = _conv(temp_db)

    with client.websocket_connect(f"/ws/chat/{conv_id}") as owner:
        owner.send_json({"type": "send", "message": "do something"})
        first, _ = _read_n(owner, 1, timeout=5.0)
        assert first and first[0].get("type") == "approval_needed", first
        assert _wait_until(lambda: bool(procs)), "the CLI process was never started"

        with client.websocket_connect(f"/ws/chat/{conv_id}") as second:
            second.send_json({"type": "stop"})
            frames, alive = _read_n(second, 2, timeout=5.0)
            assert not alive, f"the stop got no complete response: {frames!r}"
            assert [f.get("type") for f in frames] == ["stopped", "done"], frames
            assert frames[0]["did_stop"] is True, frames

        assert _wait_until(lambda: procs[0].killed, timeout=3.0), (
            "an explicit stop from the non-owning socket did not kill the CLI "
            "process"
        )
