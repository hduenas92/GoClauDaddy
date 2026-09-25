"""P2-A2 — Survival matrix against a LIVE server (WRITE job).

Each test starts a real `uvicorn` subprocess (never port 8765) with a temp
USERPROFILE so `Path.home() / ".goclaudaddy"` lands in the test's tmp dir, and
with a fake `claude` CLI first on PATH so `shutil.which("claude")` resolves to
`tests/fixtures/fake_claude_cli.py`. The server can be stopped gracefully
(SIGBREAK), hard-killed (taskkill /F /T), and restarted on the same temp data
dir.

Cell matrix: 4 events x 5 data types = 20 cells.
"""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from websockets.sync.client import connect as ws_connect

from tests.fixtures import fake_claude_cli as fake

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
FAKE_CLI_SCRIPT = BACKEND_DIR / "tests" / "fixtures" / "fake_claude_cli.py"

USER_MESSAGE_1 = "Survival user message one - please read the attached file."
USER_MESSAGE_2 = "Survival user message two - this is the crashing turn."
ATTACHMENT_BYTES_1 = b"SURVIVAL_ATTACHMENT_ONE_BYTES_12345"
ATTACHMENT_BYTES_2 = b"SURVIVAL_ATTACHMENT_TWO_BYTES_67890"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """A real uvicorn subprocess on a free port with a temp data dir + fake CLI."""

    def __init__(self, tmp_path: Path, extra_env: dict[str, str] | None = None) -> None:
        self.tmp_path = tmp_path
        self.extra_env = extra_env or {}
        self.data_home = tmp_path / "home"
        self.data_home.mkdir(parents=True, exist_ok=True)
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        shim = self.bin_dir / "claude.cmd"
        shim.write_text(
            f'@echo off\r\n"{VENV_PYTHON}" "{FAKE_CLI_SCRIPT}" %*\r\n',
            encoding="ascii",
        )
        self.port = _free_port()
        self.log_path = tmp_path / "server.log"
        self.log_fh = None
        self.proc: subprocess.Popen | None = None
        self.db_path = self.data_home / ".goclaudaddy" / "goclaudaddy.db"
        self.attachments_dir = self.data_home / ".goclaudaddy" / "attachments"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Path.home() on this Windows Python honours USERPROFILE (verified);
        # set HOME too so both agree no matter which wins.
        env["USERPROFILE"] = str(self.data_home)
        env["HOME"] = str(self.data_home)
        env["PATH"] = str(self.bin_dir) + os.pathsep + env.get("PATH", "")
        env.update(self.extra_env)
        return env

    def start(self, timeout: float = 20.0) -> None:
        assert self.proc is None or self.proc.poll() is not None, "already running"
        cmd = [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log-level",
            "warning",
        ]
        self.log_fh = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(BACKEND_DIR),
            env=self._env(),
            stdout=self.log_fh,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        _wait_for_health(self.base_url, timeout=timeout)

    def stop_graceful(self, timeout: float = 15.0) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            self.proc.send_signal(signal.CTRL_BREAK_EVENT)
        except (ValueError, OSError):
            pass
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill_hard()
        finally:
            self._close_log()

    def kill_hard(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
            capture_output=True,
            timeout=20,
        )
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        finally:
            self._close_log()

    def _close_log(self) -> None:
        if self.log_fh is not None:
            try:
                self.log_fh.close()
            except OSError:
                pass
            self.log_fh = None

    def ensure_dead(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.kill_hard()
        elif self.proc is not None:
            self._close_log()


def _wait_for_health(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                if client.get(f"{base_url}/api/health").status_code == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - retry until healthy
            last_exc = exc
        time.sleep(0.1)
    raise RuntimeError(f"server did not become healthy in {timeout}s (last error: {last_exc})")


@pytest.fixture()
def live_server(tmp_path):
    server = LiveServer(
        tmp_path,
        extra_env={fake.HOLD_FILE_ENV: str(tmp_path / "hold_after_tool_use")},
    )
    server.start()
    try:
        yield server
    finally:
        server.ensure_dead()


# ---------------------------------------------------------------------------
# HTTP / WS / DB helpers
# ---------------------------------------------------------------------------


def _create_conversation(server: LiveServer) -> str:
    with httpx.Client(timeout=10) as client:
        r = client.post(f"{server.base_url}/api/conversations", json={})
        r.raise_for_status()
        return r.json()["id"]


def _upload(server: LiveServer, conv_id: str, filename: str, data: bytes) -> dict:
    with httpx.Client(timeout=10) as client:
        r = client.post(
            f"{server.base_url}/api/attachments",
            params={"conversation_id": conv_id},
            files={"file": (filename, data, "text/plain")},
        )
        r.raise_for_status()
        return r.json()


def _get_conversation(server: LiveServer, conv_id: str) -> dict:
    with httpx.Client(timeout=10) as client:
        r = client.get(f"{server.base_url}/api/conversations/{conv_id}")
        r.raise_for_status()
        return r.json()


def _send_payload(message: str, attachment_ids: list[str]) -> dict:
    return {"type": "send", "message": message, "attachment_ids": attachment_ids}


def _ws_send_and_recv_until(server: LiveServer, conv_id: str, payload: dict, stop_on: str) -> list[dict]:
    """Open a WS, send, and receive until `stop_on` or `done`; closes the WS."""
    events: list[dict] = []
    with ws_connect(f"{server.ws_url}/ws/chat/{conv_id}", open_timeout=10) as ws:
        ws.send(json.dumps(payload))
        while True:
            ev = json.loads(ws.recv(timeout=20))
            events.append(ev)
            if ev.get("type") in (stop_on, "done"):
                return events


def _recv_until_type(ws, stop_on: str, timeout: float = 20.0) -> list[dict]:
    """Receive on an already-open WS until `stop_on` or `done`; leaves WS open."""
    events: list[dict] = []
    while True:
        ev = json.loads(ws.recv(timeout=timeout))
        events.append(ev)
        if ev.get("type") in (stop_on, "done"):
            return events


def _wait_for_assistant(server: LiveServer, conv_id: str, text: str, timeout: float = 20.0) -> dict:
    """Poll the conversation API until the assistant row with `text` is committed and status is not busy."""
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        last = _get_conversation(server, conv_id)
        msgs = last.get("messages", [])
        if any(m.get("role") == "assistant" and text in (m.get("content") or "") for m in msgs):
            if last["conversation"].get("status") != "busy":
                return last
        time.sleep(0.15)
    raise AssertionError(f"assistant message with {text!r} never committed; last={last}")


def _db_view(server: LiveServer) -> dict:
    conn = sqlite3.connect(server.db_path)
    conn.row_factory = sqlite3.Row
    try:
        messages = [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY seq ASC").fetchall()]
        attachments = [dict(r) for r in conn.execute("SELECT * FROM attachments ORDER BY created_at ASC").fetchall()]
        conversations = [dict(r) for r in conn.execute("SELECT * FROM conversations").fetchall()]
    finally:
        conn.close()
    return {"messages": messages, "attachments": attachments, "conversations": conversations}


def _users(msgs: list[dict]) -> list[dict]:
    return [m for m in msgs if m.get("role") == "user"]


def _assistants(msgs: list[dict]) -> list[dict]:
    return [m for m in msgs if m.get("role") == "assistant"]


def _assert_tool_calls_json(tool_calls_json: str | None) -> list[dict]:
    assert tool_calls_json, "assistant message has no tool_calls JSON"
    tcs = json.loads(tool_calls_json)
    assert isinstance(tcs, list) and len(tcs) == 1, f"expected exactly 1 tool call, got {tcs!r}"
    tc = tcs[0]
    assert tc["name"] == fake.TOOL_NAME
    assert tc["input"] == fake.TOOL_INPUT
    assert tc["output"] == fake.TOOL_RESULT
    return tcs


def _assert_tokens(m: dict) -> None:
    assert m.get("input_tokens") == fake.TOKENS["input_tokens"]
    assert m.get("output_tokens") == fake.TOKENS["output_tokens"]
    assert m.get("cache_read_tokens") == fake.TOKENS["cache_read_input_tokens"]
    assert m.get("cache_creation_tokens") == fake.TOKENS["cache_creation_input_tokens"]


def _assert_completed_turn_survived(api: dict, db: dict, user_text: str, attachment_bytes: bytes) -> None:
    """All 5 data types + attachment + status survive for one completed turn."""
    api_msgs = api["messages"]
    db_msgs = db["messages"]

    # 1. user message text (API + SQLite)
    assert any(m["role"] == "user" and m["content"] == user_text for m in api_msgs)
    assert any(m["role"] == "user" and m["content"] == user_text for m in db_msgs)

    # 2. assistant text (API + SQLite)
    assert any(m["role"] == "assistant" and m["content"] == fake.TEXT for m in api_msgs)
    assert any(m["role"] == "assistant" and m["content"] == fake.TEXT for m in db_msgs)

    # 3. thinking (API + SQLite)
    assert any(m["role"] == "assistant" and m["thinking"] == fake.THINKING for m in api_msgs)
    assert any(m["role"] == "assistant" and m["thinking"] == fake.THINKING for m in db_msgs)

    # 4. tool_calls JSON: name/input/output (API + SQLite)
    api_assistants = _assistants(api_msgs)
    db_assistants = _assistants(db_msgs)
    assert api_assistants, "no assistant message via API"
    assert db_assistants, "no assistant message in SQLite"
    _assert_tool_calls_json(api_assistants[0]["tool_calls"])
    _assert_tool_calls_json(db_assistants[0]["tool_calls"])

    # 5. the 4 token columns (API + SQLite)
    _assert_tokens(api_assistants[0])
    _assert_tokens(db_assistants[0])

    # attachment row linked to the user message + file bytes identical
    user_rows = [m for m in db_msgs if m["role"] == "user" and m["content"] == user_text]
    assert len(user_rows) == 1, f"expected 1 user row for {user_text!r}, got {len(user_rows)}"
    atts = [a for a in db["attachments"] if a["message_id"] == user_rows[0]["id"]]
    assert len(atts) == 1, f"expected 1 attachment linked to user message, got {len(atts)}"
    stored = Path(atts[0]["stored_path"])
    assert stored.exists(), f"attachment file missing on disk: {stored}"
    assert stored.read_bytes() == attachment_bytes

    # conversation status is not busy
    assert api["conversation"]["status"] != "busy"
    assert db["conversations"][0]["status"] != "busy"


# ---------------------------------------------------------------------------
# The 4 events x 5 data types = 20 cells
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_crash_hard_kill_mid_stream(live_server):
    """crash: one complete turn, then hard-kill mid-stream; restart; read back."""
    server = live_server
    conv_id = _create_conversation(server)

    # Turn 1: complete and fully persisted before the crash.
    att1 = _upload(server, conv_id, "note1.txt", ATTACHMENT_BYTES_1)
    events1 = _ws_send_and_recv_until(
        server, conv_id, _send_payload(USER_MESSAGE_1, [att1["id"]]), "done"
    )
    assert events1 and events1[-1]["type"] == "done"
    _wait_for_assistant(server, conv_id, fake.TEXT)

    # Turn 2: crash it. Create the hold marker so the fake CLI parks after its
    # tool_use line — the kill is then guaranteed to land mid-stream.
    Path(server.tmp_path / "hold_after_tool_use").touch()
    att2 = _upload(server, conv_id, "note2.txt", ATTACHMENT_BYTES_2)
    ws = ws_connect(f"{server.ws_url}/ws/chat/{conv_id}", open_timeout=10, legacy=True)
    ws.send(json.dumps(_send_payload(USER_MESSAGE_2, [att2["id"]])))
    try:
        events2 = _recv_until_type(ws, "tool_call")
        assert events2[-1]["type"] == "tool_call", f"expected tool_call, got {events2[-1]!r}"
    finally:
        server.kill_hard()
        with _suppress(Exception):
            ws.close()

    server.start()  # restart on the same temp data dir

    api = _get_conversation(server, conv_id)
    db = _db_view(server)
    api_msgs = api["messages"]
    db_msgs = db["messages"]

    # Everything that existed BEFORE the kill must be strictly intact.
    _assert_completed_turn_survived(api, db, USER_MESSAGE_1, ATTACHMENT_BYTES_1)
    assert any(m["role"] == "user" and m["content"] == USER_MESSAGE_2 for m in api_msgs)
    assert any(m["role"] == "user" and m["content"] == USER_MESSAGE_2 for m in db_msgs)
    atts_linked = [a for a in db["attachments"] if a["message_id"] is not None]
    assert len(atts_linked) == 2, f"expected both attachments linked, got {atts_linked!r}"
    for att in db["attachments"]:
        stored = Path(att["stored_path"])
        assert stored.exists(), f"attachment file missing on disk: {stored}"
        assert stored.read_bytes() in (ATTACHMENT_BYTES_1, ATTACHMENT_BYTES_2)

    # In-flight assistant reply: CURRENT BEHAVIOUR, pending decision — a hard
    # kill persists NOTHING of the crashing turn's reply (no text, no thinking,
    # no tool_calls, no tokens): there is no assistant row for turn 2 at all.
    assert len(_assistants(db_msgs)) == 1  # CURRENT BEHAVIOUR, pending decision
    assert len(_assistants(api_msgs)) == 1  # CURRENT BEHAVIOUR, pending decision


@pytest.mark.live
def test_restart_graceful_stop_survives(live_server):
    """restart: let the turn complete, graceful stop, restart on the same dir."""
    server = live_server
    conv_id = _create_conversation(server)
    att = _upload(server, conv_id, "note.txt", ATTACHMENT_BYTES_1)
    events = _ws_send_and_recv_until(
        server, conv_id, _send_payload(USER_MESSAGE_1, [att["id"]]), "done"
    )
    assert events and events[-1]["type"] == "done"
    _wait_for_assistant(server, conv_id, fake.TEXT)

    server.stop_graceful()
    server.start()

    api = _get_conversation(server, conv_id)
    db = _db_view(server)
    _assert_completed_turn_survived(api, db, USER_MESSAGE_1, ATTACHMENT_BYTES_1)


@pytest.mark.live
def test_disconnect_ws_close_survives(live_server):
    """disconnect: close the WS right after the first thinking event, keep the server up."""
    server = live_server
    conv_id = _create_conversation(server)
    att = _upload(server, conv_id, "note.txt", ATTACHMENT_BYTES_1)
    ws = ws_connect(f"{server.ws_url}/ws/chat/{conv_id}", open_timeout=10, legacy=True)
    ws.send(json.dumps(_send_payload(USER_MESSAGE_1, [att["id"]])))
    try:
        events = _recv_until_type(ws, "thinking")
        assert events[-1]["type"] == "thinking", f"expected thinking, got {events[-1]!r}"
    finally:
        ws.close()

    # Server is still up and finishes the turn headless (max 20s).
    _wait_for_assistant(server, conv_id, fake.TEXT, timeout=20.0)

    api = _get_conversation(server, conv_id)
    db = _db_view(server)
    _assert_completed_turn_survived(api, db, USER_MESSAGE_1, ATTACHMENT_BYTES_1)


@pytest.mark.live
def test_reload_new_clients_read_back(live_server):
    """reload: turn completes, then a brand-new HTTP client + WS connection read it back."""
    server = live_server
    conv_id = _create_conversation(server)
    att = _upload(server, conv_id, "note.txt", ATTACHMENT_BYTES_1)
    events = _ws_send_and_recv_until(
        server, conv_id, _send_payload(USER_MESSAGE_1, [att["id"]]), "done"
    )
    assert events and events[-1]["type"] == "done"
    _wait_for_assistant(server, conv_id, fake.TEXT)

    # What a page reload does: brand-new HTTP client and brand-new WS connection.
    with httpx.Client(timeout=10) as fresh_http:
        r = fresh_http.get(f"{server.base_url}/api/conversations/{conv_id}")
        r.raise_for_status()
        api = r.json()
    with ws_connect(f"{server.ws_url}/ws/chat/{conv_id}", open_timeout=10) as fresh_ws:
        pass  # a reload opens a fresh socket; nothing is sent

    db = _db_view(server)
    _assert_completed_turn_survived(api, db, USER_MESSAGE_1, ATTACHMENT_BYTES_1)


class _suppress:
    """Minimal context-manager alias so test teardown never masks the kill."""

    def __init__(self, *exceptions):
        self.exceptions = exceptions

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, self.exceptions)
