"""QA §4 CLI-integration tests.

These tests exercise the real `claude_cli.run()` subprocess plumbing,
`chat_socket.handle_chat_socket()` persistence/registry teardown, the process
registry, and the watchdog loop.  Every "claude" subprocess is the fake script
``backend/tests/fake_claude.py`` launched through a monkeypatched spawner; the
real CLI and the network are never used.

Test 4 (non-JSON stdout) is intentionally not duplicated here: it is already
pinned by ``test_non_json_notice.py`` and ``test_stream_parser.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli
from app import startup_check
from app import watchdog as app_watchdog
from app.config import REQUIRED_AUTH_ENV_VARS
from app.main import app
from app.services import conversations_service as convs
from app.services.process_registry import registry
from app.ws import chat_socket

client = TestClient(app)
FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _new_conversation() -> str:
    response = client.post("/api/conversations", json={})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _proc_dead(proc) -> bool:
    """True when the child is gone. proc.returncode alone is not enough: asyncio sets it from the owning event loop, which the TestClient
    tears down on exit, so under load it can stay None although registry.stop() already killed the process (the flaky qa4_7 failure)."""
    if proc.returncode is not None:
        return True
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, proc.pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return True
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value != 259  # STILL_ACTIVE
    try:
        os.kill(proc.pid, 0)
    except OSError:
        return True
    return False


def _collect_until(ws, predicate, timeout: float = 5.0, max_frames: int = 200):
    """Read WS frames on a daemon thread until `predicate(frames)` or timeout.

    Returns (frames, still_reading).  `frames` may contain a synthetic
    {"type": "_exception", ...} marker if the socket died first.
    """
    frames: list = []

    def _reader() -> None:
        try:
            while len(frames) < max_frames:
                frame = ws.receive_json()
                frames.append(frame)
                if predicate(frames):
                    return
        except BaseException as exc:  # noqa: BLE001 -- surfaced in `frames`
            frames.append({"type": "_exception", "exc": repr(exc)})

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout)
    return frames, thread.is_alive()


def _assistant_rows(conv_id: str) -> list:
    return [m for m in convs.list_messages(conv_id) if m.role == "assistant"]


def _assistant_content(conv_id: str) -> str:
    rows = _assistant_rows(conv_id)
    return rows[0].content if rows else ""


def _status(conv_id: str) -> str:
    conv = convs.get_conversation(conv_id)
    return conv.status if conv else ""


@pytest.fixture
def fake_spawn(monkeypatch):
    """Replace `claude` with the fake Python CLI while preserving run()'s real I/O.

    `claude_cli.shutil.which` is made to succeed for the bare name, then the
    actual `asyncio.create_subprocess_exec` call is redirected to
    ``[sys.executable, fake_claude.py, <original claude args>]``.  The rest of
    claude_cli.run (stdout parser, stderr drain, timeout, cancellation) is real.
    """
    spawned: list = []
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd, **kwargs):
        assert cmd and cmd[0] == "claude", f"unexpected executable: {cmd!r}"
        fake_cmd = [sys.executable, str(FAKE_CLAUDE), *cmd[1:]]
        proc = await real_exec(*fake_cmd, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(claude_cli.shutil, "which", lambda _name: "claude")
    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)
    try:
        yield spawned
    finally:
        # Best-effort cleanup if a test failed before killing its child.
        for proc in spawned:
            if proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.kill()


@pytest.fixture
def no_claude_spawn(monkeypatch):
    """Simulate `claude` being absent: no real process can be created."""
    attempts: list = []

    async def missing_exec(*cmd, **kwargs):
        attempts.append(cmd)
        raise FileNotFoundError("claude")

    monkeypatch.setattr(claude_cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", missing_exec)
    return attempts


# ---------------------------------------------------------------------------
# 1. claude binary absent
# ---------------------------------------------------------------------------

def test_qa4_1_claude_absent_friendly_error_no_orphan(temp_db, no_claude_spawn):
    conv_id = _new_conversation()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "hello"})
        frames, still_reading = _collect_until(
            ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=5.0
        )

    assert not still_reading, f"the turn never terminated: {frames!r}"
    assert no_claude_spawn, "run() did not even attempt to spawn claude"
    errors = [f for f in frames if f.get("type") == "error"]
    assert errors, f"no error frame reached the client: {frames!r}"
    assert errors[0].get("code") == "claude_not_found", errors
    assert "not found" in errors[0].get("error", "").lower(), errors
    assert frames[-1].get("type") == "done", frames

    assert _wait_until(lambda: not registry.is_busy(conv_id)), "registry still busy"
    assert conv_id not in registry._procs, "an orphan process was registered"
    assert _wait_until(lambda: _status(conv_id) != "busy"), f"stuck status: {_status(conv_id)}"


# ---------------------------------------------------------------------------
# 2. auth env vars: missing / empty / malformed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["missing", "empty"])
def test_qa4_2_auth_env_missing_or_empty_reports_clear_error(monkeypatch, mode):
    for name in REQUIRED_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    if mode == "empty":
        monkeypatch.setenv(REQUIRED_AUTH_ENV_VARS[0], "")

    with pytest.raises(startup_check.StartupCheckError) as exc:
        startup_check._check_auth_env_vars()

    message = str(exc.value)
    assert "Missing required environment variable" in message
    for name in REQUIRED_AUTH_ENV_VARS:
        assert name in message, f"message does not name {name}: {message}"
    assert "persistent" in message.lower(), message


def test_qa4_2_auth_env_malformed_reports_distinct_comprehensible_error(monkeypatch):
    # A token with whitespace and a syntactically invalid base URL are malformed
    # by any reasonable definition, but startup_check._check_auth_env_vars()
    # only tests truthiness.
    monkeypatch.setenv(REQUIRED_AUTH_ENV_VARS[0], "token with spaces and no sk- prefix")
    monkeypatch.setenv(REQUIRED_AUTH_ENV_VARS[1], "http://[::1")

    with pytest.raises(startup_check.StartupCheckError) as exc:
        startup_check._check_auth_env_vars()

    message = str(exc.value).lower()
    assert "malformed" in message or "invalid" in message or "valid url" in message, message


def test_qa4_2_auth_env_malformed_error_does_not_include_token_value(monkeypatch):
    token_value = "sk-LIVE-SECRET-TOKEN-0123456789"
    monkeypatch.setenv(REQUIRED_AUTH_ENV_VARS[0], token_value + "\n")
    monkeypatch.setenv(REQUIRED_AUTH_ENV_VARS[1], "https://example.invalid")

    with pytest.raises(startup_check.StartupCheckError) as exc:
        startup_check._check_auth_env_vars()

    message = str(exc.value)
    assert REQUIRED_AUTH_ENV_VARS[0] in message, message
    assert token_value not in message, f"token value leaked into: {message!r}"


# ---------------------------------------------------------------------------
# 3. response timeout
# ---------------------------------------------------------------------------

def test_qa4_3_response_timeout_kills_subprocess_and_surfaces_timeout(
    temp_db, fake_spawn, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "timeout_hang")
    monkeypatch.setattr(claude_cli, "RESPONSE_TIMEOUT_SECONDS", 0.3)
    conv_id = _new_conversation()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "hang please"})
        frames, still_reading = _collect_until(
            ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=5.0
        )
        assert not still_reading, f"turn never terminated: {frames!r}"
        assert any(f.get("type") == "timeout" for f in frames), frames

    assert _wait_until(lambda: bool(fake_spawn), timeout=15.0), "claude was never spawned"
    proc = fake_spawn[0]
    assert _wait_until(lambda: _proc_dead(proc), timeout=15.0), (
        "timed-out claude process was not reaped (returncode still None)"
    )
    assert _wait_until(lambda: not registry.is_busy(conv_id)), "registry still busy"
    assert _wait_until(lambda: _status(conv_id) != "busy"), _status(conv_id)


# ---------------------------------------------------------------------------
# 5. stderr interleaved with stdout is never assistant content
# ---------------------------------------------------------------------------

def test_qa4_5_interleaved_stderr_is_not_persisted_as_assistant_content(
    temp_db, fake_spawn, monkeypatch, caplog
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "interleaved_stderr")
    conv_id = _new_conversation()

    with caplog.at_level(logging.WARNING):
        with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
            ws.send_json({"type": "send", "message": "say something"})
            frames, still_reading = _collect_until(
                ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=5.0
            )
            assert not still_reading, frames
            stored = _wait_until(
                lambda: bool(_assistant_rows(conv_id))
                and _assistant_rows(conv_id)[0].stopped is False,
                timeout=5.0,
            )

    assert stored, "assistant row was never committed"
    assert _assistant_rows(conv_id)[0].content == "before-stderr" + "after-stderr", (
        "unexpected reply content: " + repr(_assistant_rows(conv_id)[0].content)
    )
    assert "Error: real failure" not in _assistant_rows(conv_id)[0].content
    assert "Error: real failure" in caplog.text, "real stderr did not reach the log"


# ---------------------------------------------------------------------------
# 6. benign `no stdin data received` vs genuine stderr
# ---------------------------------------------------------------------------

def test_qa4_6_benign_no_stdin_dropped_genuine_stderr_surfaces(
    temp_db, fake_spawn, monkeypatch, caplog
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "stderr_filter")
    conv_id = _new_conversation()

    with caplog.at_level(logging.WARNING):
        with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
            ws.send_json({"type": "send", "message": "trigger stderr"})
            frames, still_reading = _collect_until(
                ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=5.0
            )
        assert not still_reading, frames

    errors = [f for f in frames if f.get("type") == "error"]
    assert errors, f"genuine stderr did not surface an error: {frames!r}"
    assert errors[0].get("code") == "auth_failed", errors
    assert "invalid key provided" in caplog.text, "genuine stderr was not logged"
    assert "no stdin data received" not in caplog.text, "benign stderr was logged"
    assert "no stdin data received" not in "".join(
        f.get("text", "") for f in frames
    ), "benign stderr reached the client"


# ---------------------------------------------------------------------------
# 7. approval flow
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "client_frame,expected_content",
    [("approve", "approved"), ("deny", "denied")],
)
def test_qa4_7_approve_and_deny_flow(
    temp_db, fake_spawn, monkeypatch, client_frame, expected_content
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "approval_echo")
    monkeypatch.setattr(chat_socket, "APPROVAL_TIMEOUT_SECONDS", 5.0)
    conv_id = _new_conversation()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "run a tool"})
        first = ws.receive_json()
        assert first.get("type") == "approval_needed", first
        assert first.get("tool") == "Bash", first

        ws.send_json({"type": client_frame})
        frames, still_reading = _collect_until(
            ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=5.0
        )
        assert not still_reading, frames
        stored = _wait_until(
            lambda: _assistant_content(conv_id) == expected_content, timeout=5.0
        )

    assert stored, f"expected {expected_content!r}, got {_assistant_content(conv_id)!r}"
    assert _wait_until(lambda: not registry.is_busy(conv_id))


def test_qa4_7_approval_timeout_is_deny_and_notice_is_shown(temp_db, fake_spawn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "approval_echo")
    monkeypatch.setattr(chat_socket, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    conv_id = _new_conversation()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "run a tool"})
        first = ws.receive_json()
        assert first.get("type") == "approval_needed", first

        frames, still_reading = _collect_until(
            ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=6.0
        )
        assert not still_reading, f"approval timeout got stuck: {frames!r}"
        stored = _wait_until(lambda: "denied" in _assistant_content(conv_id), timeout=5.0)

    content = _assistant_content(conv_id)
    assert stored, f"the fake CLI did not receive a deny on timeout; content={content!r}"
    assert "approved" not in content, f"timeout auto-approved: {content!r}"
    assert "not** run" in content, f"user was not told the tool was skipped: {content!r}"
    assert any(f.get("type") == "text" and "not** run" in f.get("text", "") for f in frames)


def test_qa4_7_disconnect_while_approval_pending_stops_cli(temp_db, fake_spawn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "approval_echo")
    monkeypatch.setattr(chat_socket, "APPROVAL_TIMEOUT_SECONDS", 30.0)
    # the fake would exit by itself after its approval wait; keep it alive far longer than the reap wait
    monkeypatch.setenv("FAKE_CLAUDE_APPROVAL_WAIT", "60")
    conv_id = _new_conversation()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "run a tool"})
        first = ws.receive_json()
        assert first.get("type") == "approval_needed", first
    # socket closed with the approval still pending

    assert _wait_until(lambda: bool(fake_spawn), timeout=15.0), "claude was never spawned"
    proc = fake_spawn[0]
    assert _wait_until(lambda: _proc_dead(proc), timeout=15.0), (
        "pending-approval CLI process was not stopped after disconnect"
    )
    assert _wait_until(lambda: not registry.is_busy(conv_id)), "registry still busy after disconnect"


# ---------------------------------------------------------------------------
# 8. stop mid-stream
# ---------------------------------------------------------------------------

def test_qa4_8_stop_mid_stream_partial_saved_process_dead_registry_cleared(
    temp_db, fake_spawn, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "hang_after_text")
    conv_id = _new_conversation()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "start and hang"})
        before_stop, still_reading = _collect_until(
            ws,
            lambda fs: any(f.get("type") == "text" and f.get("text") == "partial-" for f in fs),
            timeout=5.0,
        )
        assert not still_reading, f"fake CLI never streamed partial text: {before_stop!r}"

        ws.send_json({"type": "stop"})
        after_stop, still_reading = _collect_until(
            ws,
            lambda fs: any(f.get("type") == "stopped" for f in fs)
            and any(f.get("type") == "done" for f in fs),
            timeout=5.0,
        )
        assert not still_reading, f"stop did not terminate the turn: {after_stop!r}"

        stored = _wait_until(
            lambda: bool(_assistant_rows(conv_id))
            and _assistant_rows(conv_id)[0].stopped is True,
            timeout=5.0,
        )
        assert stored, f"partial reply was not persisted: {_assistant_rows(conv_id)!r}"

    rows = _assistant_rows(conv_id)
    assert rows[0].content == "partial-", rows[0].content
    assert _wait_until(lambda: not registry.is_busy(conv_id)), "registry not cleared after stop"
    assert _wait_until(lambda: bool(fake_spawn) and _proc_dead(fake_spawn[0]), 15.0), (
        "stopped CLI process is still running"
    )


# ---------------------------------------------------------------------------
# 9. subprocess killed externally
# ---------------------------------------------------------------------------

def test_qa4_9_external_kill_recovers_and_conversation_is_not_stuck_busy(
    temp_db, fake_spawn, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "external_kill")
    conv_id = _new_conversation()

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "kill me"})
        first, still_reading = _collect_until(
            ws,
            lambda fs: any(f.get("type") == "text" and f.get("text") == "partial-external" for f in fs),
            timeout=5.0,
        )
        assert not still_reading, f"fake CLI never started streaming: {first!r}"

        assert _wait_until(lambda: bool(fake_spawn), timeout=15.0), "claude was never spawned"
        fake_spawn[0].kill()

        rest, still_reading = _collect_until(
            ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=5.0
        )
        assert not still_reading, f"app never sent done after external kill: {rest!r}"
        assert any(f.get("type") == "error" for f in rest), rest

        recovered = _wait_until(lambda: not registry.is_busy(conv_id), timeout=5.0)
        assert recovered, "conversation is still busy after its child was killed"

        # A second turn must be accepted (the app recovered; it is not wedged).
        monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "ok_text")
        ws.send_json({"type": "send", "message": "after crash"})
        second, still_reading = _collect_until(
            ws,
            lambda fs: any(f.get("type") == "text" and f.get("text") == "ok:after crash" for f in fs),
            timeout=5.0,
        )
        assert not still_reading, f"second turn was rejected: {second!r}"
        third, still_reading = _collect_until(
            ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=5.0
        )
        assert not still_reading, f"second turn did not finish: {third!r}"


# ---------------------------------------------------------------------------
# 10. three concurrent conversations
# ---------------------------------------------------------------------------

def test_qa4_10_three_concurrent_conversations_route_to_one_process_each(
    temp_db, fake_spawn, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "concurrent_echo")
    conv_messages = [
        (_new_conversation(), "alpha"),
        (_new_conversation(), "beta"),
        (_new_conversation(), "gamma"),
    ]

    with client.websocket_connect(f"/ws/chat/{conv_messages[0][0]}") as ws0, \
            client.websocket_connect(f"/ws/chat/{conv_messages[1][0]}") as ws1, \
            client.websocket_connect(f"/ws/chat/{conv_messages[2][0]}") as ws2:
        sockets = [ws0, ws1, ws2]
        # Send all three before reading any reply, so the turns overlap.
        for (_, message), ws in zip(conv_messages, sockets):
            ws.send_json({"type": "send", "message": message})

        started = _wait_until(lambda: len(fake_spawn) == 3, timeout=5.0)
        assert started, f"expected one subprocess per conversation, got {len(fake_spawn)}"
        pids = {proc.pid for proc in fake_spawn}
        assert len(pids) == 3, f"conversations shared a process: {pids!r}"

        seen: list[list] = []
        for ws in sockets:
            frames, still_reading = _collect_until(
                ws, lambda fs: any(f.get("type") == "done" for f in fs), timeout=10.0
            )
            assert not still_reading, f"conversation turn did not finish: {frames!r}"
            seen.append(frames)

    for (conv_id, message), frames in zip(conv_messages, seen):
        text = "".join(f.get("text", "") for f in frames if f.get("type") == "text")
        assert text == f"reply:{message}", f"cross-talk for {message!r}: {text!r}"
        assert _wait_until(
            lambda cid=conv_id: bool(_assistant_rows(cid))
            and _assistant_rows(cid)[0].content == f"reply:{message}",
            timeout=5.0,
        ), f"wrong assistant row for {message!r}"

    assert _wait_until(lambda: all(not registry.is_busy(cid) for cid, _ in conv_messages)), (
        "registry did not clear all three conversations"
    )


# ---------------------------------------------------------------------------
# 11. watchdog restart cap
# ---------------------------------------------------------------------------

def test_qa4_11_watchdog_stops_after_more_than_max_restarts_in_window(monkeypatch):
    calls: list = []
    fake_now = [1000.0]

    class FakeCompletedProcess:
        returncode = 1

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) > 10:
            raise AssertionError("watchdog kept restarting without bound")
        return FakeCompletedProcess()

    monkeypatch.setattr(app_watchdog, "setup_logging", lambda: None)
    monkeypatch.setattr(app_watchdog.subprocess, "run", fake_run)
    monkeypatch.setattr(app_watchdog.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(app_watchdog.time, "sleep", lambda seconds: fake_now.__setitem__(0, fake_now[0] + seconds))

    rc = app_watchdog.main()

    assert rc == 1, f"watchdog should stop with a failure after too many crashes, got {rc}"
    assert len(calls) == app_watchdog.MAX_RESTARTS + 1, (
        f"expected {app_watchdog.MAX_RESTARTS + 1} starts (the cap plus the first), got {len(calls)}"
    )





