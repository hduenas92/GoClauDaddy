"""Regression coverage for a real coworker failure: the server started fine
(the startup check passed), but every chat request failed with "The claude
CLI was not found on PATH." Root cause, confirmed by reproducing it exactly:
shutil.which() (used by the startup check) applies PATHEXT and finds a
claude.cmd/.ps1 shim (e.g. from an npm-style install), but
asyncio.create_subprocess_exec() on Windows does not - given only a bare
"claude" with no real .exe reachable anywhere on PATH, it fails with
FileNotFoundError even though shutil.which found the shim fine. run() must
resolve the executable the same way the startup check does before spawning.
"""

import sys

import pytest

from app.services.claude_cli import run

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT/.cmd resolution is Windows-specific")


@pytest.fixture
def cmd_shim_only_on_path(tmp_path, monkeypatch):
    """Puts a claude.cmd shim on PATH with no real claude.exe reachable at all -
    the exact scenario that broke on a real machine."""
    shim = tmp_path / "claude.cmd"
    shim.write_text('@echo off\necho {"type":"result","subtype":"success"}\n', encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))
    return shim


@pytest.mark.asyncio
async def test_run_finds_cmd_shim_not_just_bare_exe(cmd_shim_only_on_path, tmp_path):
    events = [ev async for ev in run(prompt="hi", model="m", cwd=str(tmp_path))]
    assert not any(ev.get("type") == "error" and "not found on PATH" in ev.get("error", "") for ev in events)
    assert events[-1] == {"type": "done"}
