"""Builds and runs the `claude` CLI subprocess, streaming normalized events out.

Security note: every piece of user input that reaches argv goes through this
module. permission_mode is checked against a whitelist before it's ever placed
on the command line — never pass a raw client string straight to argv.
"""

import asyncio
import codecs
import shutil
import sys
from collections.abc import AsyncIterator
from typing import Any

from app.config import PERMISSION_MODES, RESPONSE_TIMEOUT_SECONDS
from app.logging_setup import get_logger
from app.services.stream_parser import parse_line

log = get_logger("claude_cli")

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Substrings that identify CLI chatter which is not an application error and must
# not reach the console panel. Keep this list short and specific — a broad filter
# here would hide real failures.
_BENIGN_STDERR = (
    "no stdin data received",  # Claude CLI stdin probe in piped (--print) mode
)


def is_benign_stderr(line: str) -> bool:
    """True if this stderr line is known CLI noise rather than a real failure."""
    return any(needle in line for needle in _BENIGN_STDERR)


async def drain_stderr(stream, sink: list[str]) -> None:
    """Read the subprocess's stderr, drop benign noise, log and collect the rest.

    Module-level rather than a closure inside `run()` specifically so it is
    directly testable: a closure forced an earlier test to re-implement this
    logic inline, which meant the test passed even with the filter removed.
    """
    if not stream:
        return
    async for line_bytes in stream:
        line = line_bytes.decode("utf-8", errors="replace").rstrip()
        if line and not is_benign_stderr(line):
            log.warning("claude stderr: %s", line)
            sink.append(line)


def build_command(
    *,
    prompt: str,
    model: str,
    permission_mode: str | None = None,
    system_prompt: str | None = None,
    session_id: str | None = None,
    thinking_budget: int | None = None,
    max_tokens: int | None = None,
) -> list[str]:
    # --verbose is required by the CLI when combining --print with
    # --output-format stream-json (it refuses to start otherwise).
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", model]

    if permission_mode and permission_mode in PERMISSION_MODES:
        cmd += ["--permission-mode", permission_mode]

    if system_prompt:
        cmd += ["--system-prompt", system_prompt]

    if thinking_budget and thinking_budget > 0:
        cmd += ["--thinking", "enabled"]

    if max_tokens and max_tokens > 0:
        cmd += ["--max-tokens", str(max_tokens)]

    if session_id:
        cmd += ["--resume", session_id]  # never --continue — bleeds into unrelated sessions

    cmd.append(prompt)
    return cmd


class ClaudeCliTimeout(Exception):
    pass


async def decode_and_parse_lines(chunks: AsyncIterator[bytes]) -> AsyncIterator[dict]:
    """Decodes an async stream of raw byte chunks into normalized events.

    Uses a PERSISTENT incremental UTF-8 decoder across the whole stream rather
    than decoding each chunk independently. Chunks here are byte-level lines
    (split on \\n, which can never fall inside a valid UTF-8 multi-byte
    sequence) — but an incremental decoder still protects against any chunk
    that isn't actually newline-terminated (e.g. an unusually long line
    hitting asyncio's internal buffer limit), which would otherwise leave a
    multi-byte character split across two reads. That split was a real, if
    intermittent (~1 in 5-10 longer responses), observed cause of corrupted
    characters before this was a persistent decoder — a fresh decode() per
    chunk has no way to recover from a split; a persistent one carries the
    incomplete bytes forward to combine with the next chunk.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""
    async for raw in chunks:
        decoded = decoder.decode(raw)
        if "�" in decoded:
            log.warning("Non-UTF-8 byte(s) in claude output — replaced (data loss of 1 char)")
        buffer += decoded
        while "\n" in buffer:
            text_line, buffer = buffer.split("\n", 1)
            for event in parse_line(text_line):
                yield event
    tail = decoder.decode(b"", final=True)
    buffer += tail
    if buffer.strip():
        for event in parse_line(buffer):
            yield event


async def run(
    *,
    prompt: str,
    model: str,
    cwd: str,
    permission_mode: str | None = None,
    system_prompt: str | None = None,
    session_id: str | None = None,
    thinking_budget: int | None = None,
    max_tokens: int | None = None,
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
        thinking_budget=thinking_budget,
        max_tokens=max_tokens,
    )

    # Resolve to a full path rather than spawning the bare "claude" name.
    # shutil.which() applies PATHEXT (finds a claude.cmd/.ps1 shim, e.g. from
    # an npm-style install), but asyncio.create_subprocess_exec() on Windows
    # does not - given only a bare name with no shim reachable as a real
    # .exe, it fails with FileNotFoundError even though shutil.which (used by
    # the startup check) found it fine. Resolving first makes both paths
    # agree. Confirmed by reproducing the exact mismatch: a claude.cmd-only
    # PATH made shutil.which succeed and create_subprocess_exec fail with the
    # exact error a real coworker hit; passing the resolved path fixed it.
    resolved = shutil.which(cmd[0])
    if resolved:
        cmd[0] = resolved

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
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        log.error("`claude` CLI not found on PATH")
        yield {"type": "error", "code": "claude_not_found", "error": "The claude CLI was not found on PATH."}
        yield {"type": "done"}
        return
    except OSError as exc:
        log.error("Failed to start claude: %s", exc)
        yield {"type": "error", "error": str(exc)}
        yield {"type": "done"}
        return

    if on_process_started:
        on_process_started(proc)

    stderr_lines: list[str] = []

    stderr_task = asyncio.create_task(drain_stderr(proc.stderr, stderr_lines))

    try:
        async with asyncio.timeout(RESPONSE_TIMEOUT_SECONDS):
            assert proc.stdout is not None
            async for event in decode_and_parse_lines(proc.stdout):
                yield event
            await proc.wait()
    except TimeoutError:
        log.warning("claude subprocess exceeded %ds timeout — killing", RESPONSE_TIMEOUT_SECONDS)
        proc.kill()
        await proc.wait()
        stderr_task.cancel()
        yield {"type": "timeout"}
    except asyncio.CancelledError:
        # Caller (e.g. a /stop request) cancelled us — kill the subprocess and re-raise.
        proc.kill()
        stderr_task.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 — genuinely must not crash the socket loop
        log.exception("Error while streaming claude output")
        stderr_task.cancel()
        yield {"type": "error", "error": str(exc)}
    else:
        await stderr_task
        rc = proc.returncode
        log.info("claude exited rc=%s", rc)
        if rc not in (0, None):
            stderr_text = " ".join(stderr_lines).lower()
            # BUDGET IS CHECKED BEFORE AUTH, and the order is load-bearing.
            # The auth list contains the bare substring "auth", which appears
            # inside "unauthorized" — and a 402-style spend refusal can carry
            # that word too. Checking auth first would file every exhausted
            # key under "run `claude auth`", sending the user to re-run a
            # command that is working fine.
            #
            # Matched broadly on purpose. A CaaS key with no assigned budget is
            # a likely first-run failure — the default allowance is $200 and
            # some keys have none assigned — and the exact wording the platform
            # returns is not documented anywhere we can read. A false positive
            # costs a slightly wrong (but still useful) message; a false
            # negative costs "claude exited with code 1", which tells the user
            # nothing at all. The raw stderr is already logged at WARNING by
            # drain_stderr(), so the real text is always recoverable.
            if any(kw in stderr_text for kw in (
                "budget", "quota", "credit", "insufficient", "exceeded",
                "spend limit", "spending limit", "balance", "billing",
                "payment required", "402",
            )):
                yield {
                    "type": "error",
                    "code": "budget_exceeded",
                    "error": (
                        "Your CaaS key has no remaining budget. Check your balance "
                        "and request an increase, then try again."
                    ),
                }
            elif any(kw in stderr_text for kw in ("not logged in", "unauthorized", "authentication", "api key", "invalid key", "auth")):
                yield {"type": "error", "code": "auth_failed", "error": "Claude authentication failed. Run `claude auth` in a terminal, then refresh."}
            else:
                yield {"type": "error", "error": f"claude exited with code {rc}"}

    yield {"type": "done"}
