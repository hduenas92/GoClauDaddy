"""Builds and runs the `claude` CLI subprocess, streaming normalized events out.

Security note: every piece of user input that reaches argv goes through this
module. permission_mode is checked against a whitelist before it's ever placed
on the command line — never pass a raw client string straight to argv.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from typing import Any

from app.config import PERMISSION_MODES, RESPONSE_TIMEOUT_SECONDS
from app.logging_setup import get_logger
from app.services.stream_parser import parse_line

log = get_logger("claude_cli")

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def build_command(
    *,
    prompt: str,
    model: str,
    permission_mode: str | None = None,
    system_prompt: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    # --verbose is required by the CLI when combining --print with
    # --output-format stream-json (it refuses to start otherwise).
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", model]

    if permission_mode and permission_mode in PERMISSION_MODES:
        cmd += ["--permission-mode", permission_mode]

    if system_prompt:
        cmd += ["--system-prompt", system_prompt]

    if session_id:
        cmd += ["--resume", session_id]  # never --continue — bleeds into unrelated sessions

    cmd.append(prompt)
    return cmd


class ClaudeCliTimeout(Exception):
    pass


async def run(
    *,
    prompt: str,
    model: str,
    cwd: str,
    permission_mode: str | None = None,
    system_prompt: str | None = None,
    session_id: str | None = None,
    on_process_started: Any = None,
) -> AsyncIterator[dict]:
    """Runs the CLI and yields normalized events as they arrive.

    Always yields a final {"type": "done"} event, or {"type": "error", ...} /
    {"type": "timeout"} on failure — callers can rely on always getting a
    terminal event.
    """
    cmd = build_command(
        prompt=prompt,
        model=model,
        permission_mode=permission_mode,
        system_prompt=system_prompt,
        session_id=session_id,
    )
    log.info(
        "Launching claude cwd=%s model=%s permission_mode=%s resume=%s",
        cwd,
        model,
        permission_mode,
        bool(session_id),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        log.error("`claude` CLI not found on PATH")
        yield {"type": "error", "error": "The claude CLI was not found on PATH."}
        yield {"type": "done"}
        return
    except OSError as exc:
        log.error("Failed to start claude: %s", exc)
        yield {"type": "error", "error": str(exc)}
        yield {"type": "done"}
        return

    if on_process_started:
        on_process_started(proc)

    async def _read_lines():
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            for event in parse_line(line):
                yield event

    try:
        async with asyncio.timeout(RESPONSE_TIMEOUT_SECONDS):
            async for event in _read_lines():
                yield event
            await proc.wait()
    except TimeoutError:
        log.warning("claude subprocess exceeded %ds timeout — killing", RESPONSE_TIMEOUT_SECONDS)
        proc.kill()
        await proc.wait()
        yield {"type": "timeout"}
    except asyncio.CancelledError:
        # Caller (e.g. a /stop request) cancelled us — kill the subprocess and re-raise.
        proc.kill()
        raise
    except Exception as exc:  # noqa: BLE001 — genuinely must not crash the socket loop
        log.exception("Error while streaming claude output")
        yield {"type": "error", "error": str(exc)}
    else:
        rc = proc.returncode
        log.info("claude exited rc=%s", rc)
        if rc not in (0, None):
            yield {"type": "error", "error": f"claude exited with code {rc}"}

    yield {"type": "done"}
