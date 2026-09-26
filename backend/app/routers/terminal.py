"""Embedded terminal: spawn a shell subprocess, bridge it over a WebSocket.

Each POST /api/terminal creates a fresh session. The WebSocket for that
session bridges raw bytes in both directions. The process is killed when
the WebSocket closes.
"""

import asyncio
import sys
import uuid

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.logging_setup import get_logger

router = APIRouter(tags=["terminal"])
log = get_logger("terminal")

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_SHELL = ["cmd.exe"] if sys.platform == "win32" else ["/bin/bash", "--login"]

# session_id → Process; entries removed on WS close
_sessions: dict[str, asyncio.subprocess.Process] = {}


@router.post("/api/terminal", status_code=201)
async def create_terminal():
    tid = str(uuid.uuid4())
    try:
        proc = await asyncio.create_subprocess_exec(
            *_SHELL,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as exc:
        log.exception("Failed to spawn shell: %s", exc)
        raise HTTPException(500, f"Could not start terminal: {exc}")

    _sessions[tid] = proc
    log.info("Terminal %s started pid=%s", tid[:8], proc.pid)
    return {"id": tid}


@router.websocket("/ws/terminal/{terminal_id}")
async def terminal_ws(websocket: WebSocket, terminal_id: str):
    proc = _sessions.get(terminal_id)
    if not proc:
        await websocket.close(code=4004)
        return

    await websocket.accept()

    async def _pump_stdout():
        """Forward subprocess stdout → WebSocket until EOF."""
        try:
            assert proc.stdout
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                await websocket.send_bytes(chunk)
        except Exception:
            pass
        # Process exited on its own — notify the client and close
        _sessions.pop(terminal_id, None)
        with _swallow():
            await websocket.send_text("\r\n\x1b[1;31m[process exited]\x1b[0m\r\n")
            await websocket.close()

    reader = asyncio.create_task(_pump_stdout())

    try:
        while True:
            try:
                msg = await websocket.receive()
            except WebSocketDisconnect:
                break
            if msg["type"] == "websocket.disconnect":
                break
            data: bytes = msg.get("bytes") or (msg.get("text") or "").encode("utf-8")
            if data and proc.stdin and not proc.stdin.is_closing():
                proc.stdin.write(data)
                await proc.stdin.drain()
    finally:
        reader.cancel()
        _sessions.pop(terminal_id, None)
        with _swallow():
            proc.kill()
            await proc.wait()
        log.info("Terminal %s closed", terminal_id[:8])


class _swallow:
    """Context manager that silently ignores exceptions — for cleanup paths."""
    def __enter__(self): return self
    def __exit__(self, *_): return True
