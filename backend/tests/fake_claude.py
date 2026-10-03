#!/usr/bin/env python
"""A tiny fake `claude` CLI used only by backend/tests/test_qa_cli_integration.py.

It never talks to the network. The test's monkeypatched spawner launches this
script with `sys.executable`; the app still runs its real subprocess handling
(stdout parsing, stderr drain, timeout kill, registry) against it.

The scenario is selected by FAKE_CLAUDE_SCENARIO. Every stdout line is a JSON
stream-json event the production stream_parser already knows how to parse,
except the approval prompt line, which mimics the real CLI's non-JSON prompt
and is parsed into an approval_needed event by stream_parser.
"""

import json
import os
import queue
import sys
import threading
import time


def _emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _text(text):
    _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


def _result():
    _emit({
        "type": "result",
        "subtype": "success",
        "usage": {
            "input_tokens": 3,
            "output_tokens": 4,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    })


def _stderr(line):
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _read_stdin_line(timeout):
    """Read one line from stdin, returning None after `timeout` seconds.

    A daemon reader thread is used because select() does not work with Windows
    pipes. The daemon thread is abandoned if the process is killed.
    """
    q = queue.Queue()

    def _reader():
        try:
            q.put(sys.stdin.readline())
        except Exception:
            q.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def _prompt():
    # build_command() appends the prompt as the final argv element.
    return sys.argv[-1] if len(sys.argv) > 1 else ""


def main():
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "ok_text").strip()
    prompt = _prompt()

    if scenario == "ok_text":
        _text(f"ok:{prompt}")
        _result()
        return 0

    if scenario == "interleaved_stderr":
        _text("before-stderr")
        _stderr("Error: real failure")
        _text("after-stderr")
        _result()
        return 0

    if scenario == "stderr_filter":
        _stderr("Warning: no stdin data received in 3s")
        _stderr("Error: invalid key provided")
        return 1

    if scenario == "timeout_hang":
        # Silence, then outlive RESPONSE_TIMEOUT_SECONDS.
        while True:
            time.sleep(0.2)

    if scenario == "hang_after_text":
        _text("partial-")
        while True:
            time.sleep(0.2)

    if scenario == "external_kill":
        _text("partial-external")
        while True:
            time.sleep(0.2)

    if scenario == "approval_echo":
        sys.stdout.write("Allow Bash? [y/n]\n")
        sys.stdout.flush()
        answer = _read_stdin_line(timeout=float(os.environ.get("FAKE_CLAUDE_APPROVAL_WAIT", "5.0")))
        if answer is None:
            _stderr("FAKE_CLAUDE_NO_APPROVAL_RESPONSE")
            return 3
        if answer.strip().lower().startswith("y"):
            _text("approved")
        else:
            _text("denied")
        _result()
        return 0

    if scenario == "concurrent_echo":
        _text(f"reply:{prompt}")
        # Keep every conversation's process alive long enough that all three
        # are demonstrably in flight at the same time.
        time.sleep(0.5)
        _result()
        return 0

    _stderr(f"unknown FAKE_CLAUDE_SCENARIO: {scenario}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
