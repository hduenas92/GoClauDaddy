"""The approval gate must fail CLOSED.

WHY THIS FILE EXISTS. `chat_socket.py` used to answer an unanswered approval
prompt with `approved = True` — comment: "timeout = auto-approve, keep stream
alive" — and emit nothing about it. A user who stepped away, or whose socket had
already closed (the prompt send is wrapped in `suppress(RuntimeError)`, so it
may never have rendered), got the tool run on their behalf with no record.

`test_ws_chat_socket.py` is the largest test file in the repo at ~27KB and had
no case for this path at all, because reaching it meant waiting 60 seconds. The
timeout is now `chat_socket.APPROVAL_TIMEOUT_SECONDS`, which these tests patch.
That is the whole reason the constant exists.

Mutation-verified: restoring `approved = True` makes
test_timeout_denies_and_says_so fail on the stdin byte.
"""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli_mod
from app.main import app
from app.ws import chat_socket

client = TestClient(app)


class _FakeStdin:
    """Records what the handler writes back to the subprocess."""

    def __init__(self):
        self.writes: list[bytes] = []

    def is_closing(self):
        return False

    def write(self, data: bytes):
        self.writes.append(data)

    async def drain(self):
        return None


class _FakeProc:
    def __init__(self, stdin):
        self.stdin = stdin


def _conv():
    return client.post("/api/conversations", json={}).json()["id"]


def _run_with_approval(monkeypatch, stdin, released: threading.Event):
    """A fake CLI turn that asks for approval, then finishes."""

    async def fake_run(**kwargs):
        on_started = kwargs.get("on_process_started")
        if on_started:
            on_started(_FakeProc(stdin))
        yield {"type": "approval_needed", "tool": "Bash", "action": "rm -rf ."}
        # The handler writes y/n before the generator is resumed.
        released.set()
        yield {"type": "text", "text": "after"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)


def test_timeout_denies_and_says_so(temp_db, monkeypatch):
    """No answer -> 'n' on stdin, and the user is told in the transcript."""
    monkeypatch.setattr(chat_socket, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    stdin = _FakeStdin()
    released = threading.Event()
    _run_with_approval(monkeypatch, stdin, released)
    conv_id = _conv()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "do something"})
        first = ws.receive_json()
        assert first["type"] == "approval_needed", first
        # Deliberately answer nothing.
        assert released.wait(5.0), "the turn never resumed after the timeout"
        frames = [first]
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f["type"] == "done":
                break

    # Non-empty guard first: an empty write list satisfies "never wrote y".
    assert stdin.writes, "the handler wrote nothing to the subprocess at all"
    assert stdin.writes[0] == b"n\n", (
        f"expected a DENY on timeout, got {stdin.writes[0]!r} — the gate failed open"
    )
    said_so = [f for f in frames if f["type"] == "text" and "not** run" in f.get("text", "")]
    assert said_so, f"nothing told the user the tool was skipped; frames={[f['type'] for f in frames]}"


def test_explicit_approve_still_runs_the_tool(temp_db, monkeypatch):
    """The control. If this fails too, the test above proves nothing about
    timeouts — it would just mean approval is broken in general."""
    monkeypatch.setattr(chat_socket, "APPROVAL_TIMEOUT_SECONDS", 10)
    stdin = _FakeStdin()
    released = threading.Event()
    _run_with_approval(monkeypatch, stdin, released)
    conv_id = _conv()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "do something"})
        assert ws.receive_json()["type"] == "approval_needed"
        ws.send_json({"type": "approve"})
        assert released.wait(5.0), "the turn never resumed after approval"
        while ws.receive_json()["type"] != "done":
            pass

    assert stdin.writes, "the handler wrote nothing to the subprocess at all"
    assert stdin.writes[0] == b"y\n", f"expected APPROVE, got {stdin.writes[0]!r}"


def test_explicit_deny_denies(temp_db, monkeypatch):
    monkeypatch.setattr(chat_socket, "APPROVAL_TIMEOUT_SECONDS", 10)
    stdin = _FakeStdin()
    released = threading.Event()
    _run_with_approval(monkeypatch, stdin, released)
    conv_id = _conv()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "do something"})
        assert ws.receive_json()["type"] == "approval_needed"
        ws.send_json({"type": "deny"})
        assert released.wait(5.0), "the turn never resumed after denial"
        while ws.receive_json()["type"] != "done":
            pass

    assert stdin.writes[0] == b"n\n", f"expected DENY, got {stdin.writes[0]!r}"


class _FakeClock:
    """Controllable stand-in for chat_socket._monotonic_now.

    The server owns the deadline, so the extension test must move TIME, not just
    wait. `asyncio.wait_for` still uses the event loop's real clock for its
    timeout, but the loop only reaches wait_for when `deadline - now > 0`; the
    fake clock decides that branch, which is the branch this test is about.
    """

    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_extend_pushes_the_deadline_and_approve_still_runs(temp_db, monkeypatch):
    """approval_extend moves the server deadline; approve after the ORIGINAL
    deadline (but before the extended one) still runs the tool.

    Red on the pre-H1 code: with the deadline fixed at t0+0.2, the advance past
    t0+0.2 makes the waiter time out and write b"n\\n" before the approve lands.
    """
    monkeypatch.setattr(chat_socket, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    clock = _FakeClock()
    monkeypatch.setattr(chat_socket, "_monotonic_now", clock)
    stdin = _FakeStdin()
    released = threading.Event()
    _run_with_approval(monkeypatch, stdin, released)
    conv_id = _conv()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "do something"})
        first = ws.receive_json()
        assert first["type"] == "approval_needed", first
        assert first["timeout"] == 0.2, first
        assert first["remaining"] == 0.2, first

        # Ask for more time 0.1s in: original deadline is t0+0.2, so the
        # extension must move it to (t0+0.1)+0.2 = t0+0.3.
        clock.advance(0.1)
        ws.send_json({"type": "approval_extend"})
        ext = ws.receive_json()
        assert ext["type"] == "approval_extended", ext
        assert ext["timeout"] == 0.2, ext
        assert ext["remaining"] == pytest.approx(0.2), ext

        # Now stand 0.25s after t0 — PAST the original deadline, before the
        # extended one. Approve must still be honoured.
        clock.advance(0.15)
        ws.send_json({"type": "approve"})
        assert released.wait(5.0), "the turn never resumed after approval"
        while ws.receive_json()["type"] != "done":
            pass

    assert stdin.writes, "the handler wrote nothing to the subprocess at all"
    assert stdin.writes[0] == b"y\n", f"expected APPROVE after extend, got {stdin.writes[0]!r}"
