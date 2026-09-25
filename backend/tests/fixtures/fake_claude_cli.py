"""Fake `claude` CLI for the P2-A2 live survival matrix.

The real CLI is `claude -p --output-format stream-json --verbose`. This fixture
emits one deterministic turn with exactly the stream-json shapes
`app/services/stream_parser.py` normalizes (thinking, tool_use, tool_result,
text, usage, result), sleeping ~0.3s between lines so a live test can interrupt
the turn mid-stream (first thinking, first tool_use).

It is invoked through a `claude.cmd` shim that the live-server fixture writes
into a temp dir placed first on PATH, so `shutil.which("claude")` in
`app/services/claude_cli.py:159` resolves to this fake without any product
change. Arguments are ignored; output is deterministic.
"""

import json
import os
import sys
import time
from pathlib import Path

EMIT_SLEEP_SECONDS = 0.3

# Test-only knob: the crash test points this env var at a marker file; when the
# file exists the fake CLI parks after the tool_use line, giving the test an
# arbitrarily wide window in which the server tree is hard-killed mid-stream.
HOLD_FILE_ENV = "FAKE_CLI_HOLD_FILE"
HOLD_SECONDS = 30.0

# Deterministic token numbers asserted by the survival tests.
TOKENS = {
    "input_tokens": 1111,
    "output_tokens": 2222,
    "cache_read_input_tokens": 333,
    "cache_creation_input_tokens": 444,
}

THINKING = "SURVIVAL_THINKING: deterministic thinking block for the live survival matrix."
TEXT = "SURVIVAL_TEXT: deterministic assistant reply for the live survival matrix."
TOOL_USE_ID = "toolu_01LIVE_SURVIVAL"
TOOL_NAME = "Read"
TOOL_INPUT = {"file_path": "C:\\fake\\survival.txt"}
TOOL_RESULT = "SURVIVAL_TOOL_RESULT: deterministic tool output."


def build_lines() -> list[str]:
    """The six stream-json lines of one turn, in wire order."""
    events = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": THINKING}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": TOOL_USE_ID,
                        "name": TOOL_NAME,
                        "input": TOOL_INPUT,
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": TOOL_USE_ID,
                        "content": TOOL_RESULT,
                        "is_error": False,
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": TEXT}]},
        },
        {
            "type": "assistant",
            "message": {"usage": dict(TOKENS), "content": []},
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": TEXT,
            "usage": dict(TOKENS),
        },
    ]
    return [json.dumps(ev) for ev in events]


def _sleep_after(line_index: int, sleep: float) -> None:
    """Sleep between lines; index 1 is the tool_use line (crash-test hold)."""
    if line_index == 1:
        hold_file = os.environ.get(HOLD_FILE_ENV)
        if hold_file and Path(hold_file).exists():
            time.sleep(HOLD_SECONDS)
            return
    time.sleep(sleep)


def emit(sleep: float = EMIT_SLEEP_SECONDS) -> None:
    for index, line in enumerate(build_lines()):
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        _sleep_after(index, sleep)


if __name__ == "__main__":
    emit()
