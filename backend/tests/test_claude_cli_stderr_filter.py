"""stderr filtering in claude_cli.

The `claude` CLI emits `Warning: no stdin data received in 3s` on every piped
run. It is a stdin probe, not an app error, and it used to clutter the console
panel on every turn.

NOTE ON AN EARLIER VERSION OF THIS FILE: it re-implemented the filter condition
inline inside each test and asserted on its own copy. `drain_stderr` was never
called, so deleting the filter from production left every test green — false
confidence, which is worse than no test. `is_benign_stderr` and `drain_stderr`
were lifted to module level precisely so these tests can call the real thing.
Every test below exercises production code. Keep it that way.
"""

import pytest

from app.services.claude_cli import drain_stderr, is_benign_stderr


async def _fake_stream(*lines):
    """Minimal stand-in for `proc.stderr` — async-iterable, yields bytes."""
    for line in lines:
        yield (line + "\n").encode("utf-8")


# --- the predicate ---------------------------------------------------------

@pytest.mark.parametrize("line", [
    "Warning: no stdin data received in 3s",
    "no stdin data received",
    "  prefixed and no stdin data received trailing  ",
])
def test_benign_lines_are_recognised(line):
    assert is_benign_stderr(line) is True


@pytest.mark.parametrize("line", [
    "Error: something actually broke",
    "Fatal: database connection lost",
    "not logged in",
    "stdin",                      # substring of the needle, must NOT match
    "no stdin data",              # partial needle, must NOT match
    "",
])
def test_real_lines_are_not_treated_as_benign(line):
    assert is_benign_stderr(line) is False


# --- drain_stderr, the actual consumer ------------------------------------

@pytest.mark.asyncio
async def test_benign_line_is_not_collected_or_logged(caplog):
    sink: list[str] = []
    with caplog.at_level("WARNING"):
        await drain_stderr(_fake_stream("Warning: no stdin data received in 3s"), sink)
    assert sink == []
    assert "no stdin data received" not in caplog.text


@pytest.mark.asyncio
async def test_genuine_error_is_collected_and_logged(caplog):
    sink: list[str] = []
    with caplog.at_level("WARNING"):
        await drain_stderr(_fake_stream("Error: something actually broke"), sink)
    assert sink == ["Error: something actually broke"]
    assert "Error: something actually broke" in caplog.text


@pytest.mark.asyncio
async def test_mixed_stream_keeps_only_real_lines(caplog):
    sink: list[str] = []
    with caplog.at_level("WARNING"):
        await drain_stderr(_fake_stream(
            "Warning: no stdin data received in 3s",
            "Error: real failure",
            "another no stdin data received here",
            "Fatal: database connection lost",
        ), sink)
    assert sink == ["Error: real failure", "Fatal: database connection lost"]
    assert "no stdin data received" not in caplog.text


@pytest.mark.asyncio
async def test_blank_lines_are_dropped():
    sink: list[str] = []
    await drain_stderr(_fake_stream("", "   ", "Error: kept"), sink)
    assert sink == ["Error: kept"]


@pytest.mark.asyncio
async def test_none_stream_is_a_no_op():
    """proc.stderr can be None when the subprocess was started without a pipe."""
    sink: list[str] = []
    await drain_stderr(None, sink)
    assert sink == []


@pytest.mark.asyncio
async def test_undecodable_bytes_do_not_raise():
    """Decoding uses errors='replace', so a bad byte must not kill the drain."""
    async def bad_stream():
        yield b"\xff\xfe invalid utf8\n"
        yield b"Error: still reading\n"

    sink: list[str] = []
    await drain_stderr(bad_stream(), sink)
    assert any("still reading" in line for line in sink)
    assert len(sink) == 2


# --- the auth-detection path downstream depends on this sink -------------

@pytest.mark.asyncio
async def test_auth_keywords_survive_the_filter():
    """run() scans the collected lines for auth failures — the filter must not
    swallow them, or `auth_failed` would never be reported to the user."""
    sink: list[str] = []
    await drain_stderr(_fake_stream(
        "Warning: no stdin data received in 3s",
        "Error: not logged in",
    ), sink)
    assert any("not logged in" in line for line in sink)
