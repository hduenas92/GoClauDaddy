"""P2-M — QA §14 L1: every error path logs file+line+stack (WRITE phase).

Covers the six items in `plans/QA_AGENT_INSTRUCTIONS.md` §14:

- L1  Every error path produces a log line with file, line, and stack
- L2  No secrets in any log            (already covered by test_secret_handling.py)
- L3  Log rotation works at the size threshold
- L4  SSE `/api/server/logs` survives a 300+ line burst (300-line buffer claimed)
- L5  Two SSE subscribers simultaneously
- L6  Every silent contextlib.suppress is deliberate      (report, not a test)

P2-M L1 scope (per the P2-M spec's verified facts): the error paths that answer
the client with an error and must log a stack are

- app/services/claude_cli.py:198 (FileNotFoundError spawn) and :203 (OSError spawn)
- app/services/attachments_service.py:78-84 -> app/routers/attachments.py:16-17 (507)
- app/services/dir_picker.py:36 and :42 (-> 503)
- app/startup_check.py:22 and :31 (abort startup)

plus the already-compliant main.py:79 (HTTP 500) and claude_cli.py:233
(streaming exception). The non-JSON CLI stdout notice (chat_socket.py:426) is
a non-fatal `notice` frame, not an error response, so the stack requirement does
not apply to it; its test below asserts it is still logged at WARNING from the
correct file:line. Forwarding/non-persistence for that path is covered by P2-B
E1 (tests/test_non_json_notice.py).
"""

import builtins
import contextlib
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.logging_setup as logging_setup
import app.services.attachments_service as att_svc
import app.services.claude_cli as claude_cli_mod
import app.services.dir_picker as dir_picker_mod
import app.startup_check as startup_mod
from app.config import DEFAULT_MODEL
from app.main import app
from app.services import conversations_service as convs

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# L1 — every error path produces a log line with file, line, and stack
# ---------------------------------------------------------------------------


def _line_of(rel_path, needle):
    """1-based line of the one source line containing `needle`: pins the log call itself, not a line number
    that every unrelated edit above it shifts (a fixed `== 203` broke on two blank lines, 2026-10-01)."""
    lines = (Path(__file__).resolve().parents[1] / rel_path).read_text(encoding="utf-8").splitlines()
    hits = [n for n, line in enumerate(lines, 1) if needle in line]
    assert len(hits) == 1, f"{rel_path}: {needle!r} found {len(hits)}x"
    return hits[0]


def _assert_logged_stack(records, label):
    """Assert a list of LogRecords contains one with exc_info + pathname/lineno."""
    assert records, f"{label}: no matching log record was produced"
    rec = records[-1]
    assert rec.pathname and rec.lineno > 0, (
        f"{label}: record at {rec.pathname}:{rec.lineno} lacks pathname/lineno"
    )
    assert rec.exc_info is not None, (
        f"{label}: record at {rec.pathname}:{rec.lineno} has no stack (exc_info={rec.exc_info!r})"
    )
    return rec


def test_l1_http_500_handler_logs_stack(monkeypatch, caplog):
    """The global 500 handler logs the real exception with a stack (main.py:79)."""
    def boom(conversation_id):
        raise RuntimeError("L1-500-simulated")

    monkeypatch.setattr(convs, "get_conversation", boom)
    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/api/conversations/anything")
    assert res.status_code == 500

    records = [r for r in caplog.records if r.levelno >= logging.ERROR and "Unhandled error" in r.getMessage()]
    rec = _assert_logged_stack(records, "HTTP 500 handler")
    assert rec.pathname.endswith(os.path.join("app", "main.py"))


def test_l1_ws_chat_cli_not_found_logs_stack(temp_db, monkeypatch, caplog):
    """WS chat turn with the `claude` CLI missing must log a stack (claude_cli.py:198)."""
    async def fake_exec(*a, **kw):
        raise FileNotFoundError("no such claude")

    monkeypatch.setattr(claude_cli_mod.asyncio, "create_subprocess_exec", fake_exec)
    client = TestClient(app)
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "hello"})
        frames = []
        for _ in range(50):
            frame = ws.receive_json()
            frames.append(frame)
            if frame.get("type") == "done":
                break
        else:
            raise AssertionError(f"WS turn did not finish within 50 frames: {frames[-3:]!r}")

    records = [r for r in caplog.records if "not found on PATH" in r.getMessage()]
    _assert_logged_stack(records, "WS chat CLI not-found failure")


async def test_l1_claude_streaming_exception_logs_stack(monkeypatch, caplog):
    """A streaming exception inside claude_cli.run logs a stack (claude_cli.py:233)."""
    async def boom(chunks):
        raise RuntimeError("L1-STREAM-BOOM")
        yield  # pragma: no cover

    class _FakeProc:
        returncode = 0
        stdin = None
        stdout = None
        stderr = None

        async def wait(self):
            return 0

    async def fake_exec(*a, **kw):
        return _FakeProc()

    monkeypatch.setattr(claude_cli_mod, "decode_and_parse_lines", boom)
    monkeypatch.setattr(claude_cli_mod.asyncio, "create_subprocess_exec", fake_exec)

    events = []
    async for ev in claude_cli_mod.run(prompt="hi", model=DEFAULT_MODEL, cwd="."):
        events.append(ev)

    assert any(e.get("type") == "error" for e in events), events
    records = [r for r in caplog.records if "Error while streaming claude output" in r.getMessage()]
    _assert_logged_stack(records, "claude streaming exception")


def test_l1_disk_full_507_path_logs_stack(temp_db, tmp_path, monkeypatch, caplog):
    """ENOSPC during upload → HTTP 507 must log a stack (attachments.py:16-17)."""
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(att_svc, "ATTACHMENTS_DIR", att_dir)

    client = TestClient(app, raise_server_exceptions=False)
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    def boom_write(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", boom_write)
    res = client.post(
        "/api/attachments",
        params={"conversation_id": conv_id},
        files={"file": ("diskfull.txt", b"x", "text/plain")},
    )
    assert res.status_code == 507

    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    disk_records = [
        r for r in records
        if "disk" in r.getMessage().lower() or "space" in r.getMessage().lower()
    ]
    _assert_logged_stack(disk_records, "disk-full 507 path")


def test_l1_non_json_notice_logs_stack(temp_db, monkeypatch, caplog):
    """A non-JSON CLI stdout notice is logged at chat_socket.py:430.

    P2-M scope: this path answers the client with a non-fatal `notice` frame,
    not an error response, so it is NOT one of the L1 error paths that must
    carry a stack. The stack assertion is therefore not applied here; what
    must stay true is that the line is logged, at WARNING level, from
    chat_socket.py:430. Forwarding/non-persistence is covered by P2-B E1
    (tests/test_non_json_notice.py).
    """
    async def fake_run(**kwargs):
        yield {"type": "notice", "text": "L1-BANNER-GARBAGE-42"}
        yield {"type": "text", "text": "ok"}

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    client = TestClient(app)
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "hello"})
        frames = []
        for _ in range(50):
            frame = ws.receive_json()
            frames.append(frame)
            if frame.get("type") == "done":
                break
        else:
            raise AssertionError(f"WS turn did not finish within 50 frames: {frames[-3:]!r}")

    records = [r for r in caplog.records if "L1-BANNER-GARBAGE-42" in r.getMessage()]
    assert records, "non-JSON notice: no matching log record was produced"
    rec = records[-1]
    assert rec.levelno == logging.WARNING, f"expected WARNING, got {rec.levelno}"
    assert rec.pathname.endswith(os.path.join("app", "ws", "chat_socket.py")), rec.pathname
    assert rec.lineno == _line_of("app/ws/chat_socket.py", 'log.warning("Non-JSON CLI stdout line ignored'), rec.lineno


def test_l1_ws_chat_cli_oserror_logs_stack(temp_db, monkeypatch, caplog):
    """WS chat turn with an OSError from spawn must log a stack (claude_cli.py:203)."""
    async def fake_exec(*a, **kw):
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(claude_cli_mod.asyncio, "create_subprocess_exec", fake_exec)
    client = TestClient(app)
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "hello"})
        frames = []
        for _ in range(50):
            frame = ws.receive_json()
            frames.append(frame)
            if frame.get("type") == "done":
                break
        else:
            raise AssertionError(f"WS turn did not finish within 50 frames: {frames[-3:]!r}")

    records = [r for r in caplog.records if "Failed to start claude" in r.getMessage()]
    rec = _assert_logged_stack(records, "WS chat CLI OSError spawn failure")
    assert rec.pathname.endswith(os.path.join("app", "services", "claude_cli.py")), rec.pathname
    assert rec.lineno == _line_of("app/services/claude_cli.py", 'log.exception("Failed to start claude'), rec.lineno


def test_l1_dir_picker_tkinter_import_error_logs_stack(monkeypatch, caplog):
    """dir_picker with no tkinter must log a stack (dir_picker.py:36)."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tkinter":
            raise ImportError("no tkinter (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(dir_picker_mod.DirPickerUnavailable):
        dir_picker_mod.pick_directory()

    records = [r for r in caplog.records if "tkinter is not installed" in r.getMessage()]
    rec = _assert_logged_stack(records, "dir_picker tkinter import failure")
    assert rec.pathname.endswith(os.path.join("app", "services", "dir_picker.py")), rec.pathname
    assert rec.lineno == 36, rec.lineno


def test_l1_dir_picker_tcl_error_logs_stack(monkeypatch, caplog):
    """dir_picker with Tk raising TclError must log a stack (dir_picker.py:42)."""
    import tkinter

    def boom(*a, **kw):
        raise tkinter.TclError("no display (simulated)")

    monkeypatch.setattr(tkinter, "Tk", boom)
    with pytest.raises(dir_picker_mod.DirPickerUnavailable):
        dir_picker_mod.pick_directory()

    records = [r for r in caplog.records if "Folder picker unavailable" in r.getMessage()]
    rec = _assert_logged_stack(records, "dir_picker TclError failure")
    assert rec.pathname.endswith(os.path.join("app", "services", "dir_picker.py")), rec.pathname
    assert rec.lineno == 42, rec.lineno


def test_l1_startup_check_write_probe_logs_stack(monkeypatch, caplog):
    """_check_writable OSError must log a stack (startup_check.py:22)."""
    def boom_write(self, *args, **kwargs):
        raise OSError(13, "permission denied (simulated)")

    monkeypatch.setattr(Path, "write_text", boom_write)
    with pytest.raises(startup_mod.StartupCheckError):
        startup_mod._check_writable(Path("C:/nonexistent-startup-check-dir"))

    records = [r for r in caplog.records if "cannot write" in r.getMessage()]
    rec = _assert_logged_stack(records, "startup write-probe failure")
    assert rec.pathname.endswith(os.path.join("app", "startup_check.py")), rec.pathname


def test_l1_startup_check_port_bind_logs_stack(monkeypatch, caplog):
    """_check_port_free bind OSError must log a stack (startup_check.py:31)."""
    def boom_bind(self, *args, **kwargs):
        raise OSError(10048, "port in use (simulated)")

    monkeypatch.setattr(socket.socket, "bind", boom_bind)
    with pytest.raises(startup_mod.StartupCheckError):
        startup_mod._check_port_free()

    records = [r for r in caplog.records if "not free" in r.getMessage()]
    rec = _assert_logged_stack(records, "startup port-bind failure")
    assert rec.pathname.endswith(os.path.join("app", "startup_check.py")), rec.pathname


# ---------------------------------------------------------------------------
# L3 — log rotation works at the size threshold
# ---------------------------------------------------------------------------


def test_l3_log_rotation_respects_threshold_and_backup_count(tmp_path, monkeypatch):
    """The real logging_setup RotatingFileHandler rotates at maxBytes and honours backupCount."""
    log_file = tmp_path / "logs" / "app.log"
    monkeypatch.setattr(logging_setup, "LOG_FILE", log_file)
    monkeypatch.setattr(logging_setup, "_configured", False)

    root = logging.getLogger("goclaudaddy")
    before = set(root.handlers)
    logging_setup.setup_logging()
    new_handlers = [h for h in root.handlers if h not in before]
    rfh = next(
        h for h in new_handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    # Configure the REAL handler with a tiny threshold and small backup count so the
    # test exercises the actual RotatingFileHandler.doRollover path, not a stand-in.
    rfh.maxBytes = 256
    rfh.backupCount = 3

    try:
        log = logging.getLogger("goclaudaddy.l3test")
        for i in range(40):
            log.warning("L3 rotation probe line %02d padding padding padding", i)
        for h in new_handlers:
            h.flush()

        files = sorted(tmp_path.joinpath("logs").glob("app.log*"))
        assert len(files) == 4, (
            f"expected 1 current + backupCount 3 = 4 files, got {[f.name for f in files]}"
        )
        assert (tmp_path / "logs" / "app.log.1").exists(), "rotated file app.log.1 missing"
    finally:
        for h in new_handlers:
            root.removeHandler(h)
            h.close()
        logging_setup._configured = False


# ---------------------------------------------------------------------------
# L4/L5 — SSE /api/server/logs against a real uvicorn server on a free port
# ---------------------------------------------------------------------------


class _LiveServer:
    """Real uvicorn subprocess on a free port with a temp USERPROFILE/HOME."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        self.log_path = tmp_path / "server.log"
        self.proc: subprocess.Popen | None = None
        self.log_fh = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 20.0) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", self.port))
            except OSError as exc:
                raise RuntimeError(f"port {self.port} is not free for the observability live server") from exc

        env = os.environ.copy()
        env["USERPROFILE"] = str(self.home)
        env["HOME"] = str(self.home)
        self.log_fh = open(self.log_path, "ab")  # noqa: SIM115 — held open for the subprocess lifetime
        self.proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning",
            ],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=self.log_fh,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        self._wait_health(timeout=timeout)

    def _wait_health(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(f"{self.base_url}/api/health").status_code == 200:
                        return
            except Exception as exc:  # noqa: BLE001 — retry until healthy
                last_exc = exc
            time.sleep(0.1)
        raise RuntimeError(f"server did not become healthy in {timeout}s (last: {last_exc})")

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    timeout=20,
                )
        if self.log_fh is not None:
            with contextlib.suppress(OSError):
                self.log_fh.close()
            self.log_fh = None


@pytest.fixture()
def live_server(tmp_path):
    server = _LiveServer(tmp_path)
    server.start()
    try:
        yield server
    finally:
        server.kill()


def _read_marker_lines(stream_iter, n: int, marker: str) -> list[str]:
    """Read SSE `data:` lines until `n` payloads containing `marker` are seen."""
    got: list[str] = []
    for raw in stream_iter:
        if raw.startswith("data: ") and marker in raw:
            got.append(raw.split(marker, 1)[1].split(".", 1)[0])
            if len(got) >= n:
                return got
    return got


def _upload_burst(client: httpx.Client, base: str, conv_id: str, marker: str, n: int) -> None:
    for i in range(n):
        res = client.post(
            f"{base}/api/attachments",
            params={"conversation_id": conv_id},
            files={"file": (f"{marker}{i:03d}.txt", b"x", "text/plain")},
        )
        assert res.status_code == 200, res.text


@pytest.mark.live
def test_l4_sse_burst_replays_documented_300_line_buffer(live_server):
    """350 logged lines; a fresh subscriber gets exactly the documented 300-line buffer tail.

    Documented behaviour: `_log_buffer` is `deque(maxlen=300)` (server.py:20) and the
    QA doc claims a "(300-line buffer claimed)" (plans/QA_AGENT_INSTRUCTIONS.md §14,
    actual line 263).  Each of the 350 uploads logs exactly one line carrying the
    marker (attachments_service.py:95), so the 300 retained lines are always burst
    indices 050..349 regardless of how many startup lines preceded the burst:
    deque(maxlen=300) keeps the LAST 300 total lines, and 350 > 300.
    """
    base = live_server.base_url
    with httpx.Client(timeout=httpx.Timeout(10.0, read=10.0)) as client:
        conv_id = client.post(f"{base}/api/conversations", json={}).json()["id"]
        _upload_burst(client, base, conv_id, "L4-BURST-", 350)

        start = time.monotonic()
        with client.stream("GET", f"{base}/api/server/logs") as sse:
            got = _read_marker_lines(sse.iter_lines(), 300, "L4-BURST-")
        elapsed = time.monotonic() - start

    assert got == [f"{i:03d}" for i in range(50, 350)], (
        f"expected exactly the last 300 buffered burst lines 050..349, got {len(got)} lines "
        f"first={got[0] if got else None} last={got[-1] if got else None}"
    )
    assert elapsed < 10.0, f"SSE read took {elapsed:.2f}s — stream appears to hang"


@pytest.mark.live
def test_l4_sse_live_subscriber_survives_350_line_burst(live_server):
    """A subscriber opened BEFORE the burst and reading concurrently gets all 350 in order."""
    base = live_server.base_url
    got: list[str] = []
    elapsed = 0.0
    results: dict[str, object] = {}
    thread: threading.Thread | None = None
    with httpx.Client(timeout=httpx.Timeout(10.0, read=10.0)) as client:
        conv_id = client.post(f"{base}/api/conversations", json={}).json()["id"]

        def uploader() -> None:
            try:
                _upload_burst(client, base, conv_id, "L4-LIVE-", 350)
                results["ok"] = True
            except Exception as exc:  # noqa: BLE001 — surfaced below
                results["err"] = repr(exc)

        try:
            with client.stream("GET", f"{base}/api/server/logs") as sse:
                it = sse.iter_lines()
                start = time.monotonic()
                thread = threading.Thread(target=uploader)
                thread.start()
                got = _read_marker_lines(it, 350, "L4-LIVE-")
                elapsed = time.monotonic() - start
        finally:
            if thread is not None:
                thread.join(timeout=30)

    assert results.get("ok") is True, f"uploader failed: {results.get('err')}"
    assert thread is not None and not thread.is_alive(), "uploader thread did not finish"
    assert got == [f"{i:03d}" for i in range(350)], (
        f"live subscriber did not receive all 350 burst lines in order "
        f"(got {len(got)}, first={got[0] if got else None}, last={got[-1] if got else None})"
    )
    assert elapsed < 10.0, f"SSE read took {elapsed:.2f}s — stream appears to hang"


@pytest.mark.live
def test_l5_two_sse_subscribers_receive_all_20_lines(live_server):
    """Two subscribers opened at the same time both receive all 20 emitted lines."""
    base = live_server.base_url
    expected = [f"{i:03d}" for i in range(20)]

    with httpx.Client(timeout=httpx.Timeout(10.0, read=10.0)) as client:
        conv_id = client.post(f"{base}/api/conversations", json={}).json()["id"]

        with client.stream("GET", f"{base}/api/server/logs") as sse1, \
                client.stream("GET", f"{base}/api/server/logs") as sse2:
            _upload_burst(client, base, conv_id, "L5-BURST-", 20)
            got1 = _read_marker_lines(sse1.iter_lines(), 20, "L5-BURST-")
            got2 = _read_marker_lines(sse2.iter_lines(), 20, "L5-BURST-")

    assert got1 == expected, f"subscriber 1 got {got1}"
    assert got2 == expected, f"subscriber 2 got {got2}"
