"""Tests for claude_cli.build_env() and the env run() passes to the subprocess.

CLI 2.1.286 rejects `--max-tokens` and `--thinking` (unknown option, exit 1).
The documented controls are the CLAUDE_CODE_MAX_OUTPUT_TOKENS and
MAX_THINKING_TOKENS environment variables, so run() must set those overrides.
"""

import os

import pytest

from app.services import claude_cli


def test_build_env_none_none_is_empty():
    assert claude_cli.build_env(thinking_budget=None, max_tokens=None) == {}


def test_build_env_zero_max_tokens_is_omitted():
    assert claude_cli.build_env(thinking_budget=None, max_tokens=0) == {}


def test_build_env_negative_max_tokens_is_omitted():
    assert claude_cli.build_env(thinking_budget=None, max_tokens=-1) == {}


def test_build_env_positive_max_tokens():
    assert claude_cli.build_env(thinking_budget=None, max_tokens=4096) == {
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"
    }


class _EmptyStream:
    """Async byte iterator that yields no chunks (subprocess with silent stdout)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeProc:
    returncode = 0
    stdin = None
    stderr = None

    def __init__(self):
        self.stdout = _EmptyStream()

    async def wait(self):
        return 0


async def _run_capture_env(monkeypatch, **kwargs) -> dict:
    captured: dict = {}

    async def fake_exec(*args, **spawn_kwargs):
        captured["args"] = args
        captured["kwargs"] = spawn_kwargs
        return _FakeProc()

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)
    events = [ev async for ev in claude_cli.run(prompt="hi", model="m", cwd=".", **kwargs)]
    assert events[-1] == {"type": "done"}
    return captured


@pytest.mark.asyncio
async def test_run_without_overrides_leaves_env_alone(monkeypatch):
    monkeypatch.delenv("MAX_THINKING_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    captured = await _run_capture_env(monkeypatch)
    env = captured["kwargs"]["env"]
    assert "MAX_THINKING_TOKENS" not in env
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env
