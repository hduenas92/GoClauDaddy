"""Regression tests for formerly-silent cleanup/error paths.

The task is narrow: a closed peer or an already-gone process is an expected
condition and may stay silent, but anything else must not vanish. These tests
pin the unexpected branch at one chat-socket cleanup site and at the assess
subprocess-kill site, and pin that expected exceptions remain quiet.
"""

import asyncio
import logging

import pytest
from fastapi import WebSocketDisconnect

from app.routers import assess as assess_mod
from app.ws import chat_socket


class _FakeWebSocket:
    """Fails the first send, then records later sends.

    `_handle_send` tries to send an error frame first and a done frame second.
    Raising only on the first call isolates the site under test.
    """

    def __init__(self, first_send_exc: BaseException):
        self._first_send_exc = first_send_exc
        self.calls = 0
        self.sent = []

    async def send_json(self, payload):
        self.calls += 1
        if self.calls == 1:
            raise self._first_send_exc
        self.sent.append(payload)


def _chat_socket_records(caplog, needle: str):
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "goclaudaddy.chat_socket"
        and needle in r.getMessage()
    ]


def _assess_records(caplog, needle: str):
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "goclaudaddy.assess"
        and needle in r.getMessage()
    ]


def test_unexpected_ws_error_frame_failure_is_logged_and_flow_continues(
    monkeypatch, caplog
):
    """A non-closed-socket failure while sending the error frame must be logged."""

    async def primary_failure(*args, **kwargs):
        raise RuntimeError("simulated primary setup failure")

    monkeypatch.setattr(chat_socket, "_handle_send_inner", primary_failure)
    monkeypatch.setattr(chat_socket.convs, "set_status", lambda cid, status: None)

    ws = _FakeWebSocket(ValueError("simulated unexpected send_json failure"))
    with caplog.at_level(logging.WARNING, logger="goclaudaddy.chat_socket"):
        asyncio.run(
            chat_socket._handle_send(ws, "conv-exception-test", {"message": "x"}, asyncio.Queue())
        )

    records = _chat_socket_records(caplog, "send error frame")
    assert records, f"expected WARNING for the failed error frame; records={caplog.records!r}"
    assert records[-1].exc_info is not None, "WARNING record must carry exc_info=True"
    assert ws.sent == [{"type": "done"}], "the handler must continue to the done frame"


@pytest.mark.parametrize(
    "expected_exc",
    [RuntimeError("closed websocket"), WebSocketDisconnect()],
    ids=["runtime-error", "websocket-disconnect"],
)
def test_expected_closed_ws_error_frame_failure_is_silent(
    monkeypatch, caplog, expected_exc
):
    """RuntimeError / WebSocketDisconnect from a closed peer are expected and silent."""

    async def primary_failure(*args, **kwargs):
        raise RuntimeError("simulated primary setup failure")

    monkeypatch.setattr(chat_socket, "_handle_send_inner", primary_failure)
    monkeypatch.setattr(chat_socket.convs, "set_status", lambda cid, status: None)

    ws = _FakeWebSocket(expected_exc)
    with caplog.at_level(logging.WARNING, logger="goclaudaddy.chat_socket"):
        asyncio.run(
            chat_socket._handle_send(ws, "conv-exception-test", {"message": "x"}, asyncio.Queue())
        )

    assert not _chat_socket_records(caplog, "send error frame"), (
        f"expected no WARNING for expected exception {expected_exc!r}; "
        f"records={caplog.records!r}"
    )
    assert ws.sent == [{"type": "done"}], "the handler must continue to the done frame"


class _FailingAssessProc:
    def __init__(self, kill_exc: BaseException):
        self.kill_exc = kill_exc

    async def communicate(self):
        raise RuntimeError("simulated assessment failure")

    def kill(self):
        raise self.kill_exc


def _run_assess_with_failing_kill(monkeypatch, kill_exc):
    proc = _FailingAssessProc(kill_exc)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(assess_mod.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    return asyncio.run(assess_mod.assess(assess_mod.AssessBody(message="hello")))


def test_unexpected_assess_kill_failure_is_logged(monkeypatch, caplog):
    """A process.kill() failure that is not 'process already gone' must be logged."""

    with caplog.at_level(logging.WARNING, logger="goclaudaddy.assess"):
        result = _run_assess_with_failing_kill(
            monkeypatch, ValueError("simulated unexpected kill failure")
        )

    assert result["timed_out"] is True, "assess must still return its fallback after kill failure"
    records = _assess_records(caplog, "kill subprocess")
    assert records, f"expected WARNING for the failed kill; records={caplog.records!r}"
    assert records[-1].exc_info is not None, "WARNING record must carry exc_info=True"


def test_expected_assess_process_lookup_error_is_silent(monkeypatch, caplog):
    """ProcessLookupError from proc.kill() means already gone and stays silent."""

    with caplog.at_level(logging.WARNING, logger="goclaudaddy.assess"):
        result = _run_assess_with_failing_kill(
            monkeypatch, ProcessLookupError("process already gone")
        )

    assert result["timed_out"] is True, "assess must still return its fallback"
    assert not _assess_records(caplog, "kill subprocess"), (
        f"expected no kill WARNING for an already-gone process; records={caplog.records!r}"
    )
