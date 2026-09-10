"""Parses `claude -p --output-format stream-json` output lines into normalized events.

Ported from the old claudioui_server.py's inline parsing (system/init, assistant
content blocks, result). Pure function — no I/O — so it's fully unit-testable
against captured real sample lines.
"""

import json
import re
from typing import Any

_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def parse_line(raw_line: str) -> list[dict[str, Any]]:
    """Returns zero or more normalized events for one line of CLI stdout.

    A single `assistant` event can carry multiple content blocks (e.g. thinking
    then text), so this always returns a list rather than "the first" event.
    """
    line = strip_ansi(raw_line).strip()
    if not line:
        return []

    try:
        ev = json.loads(line)
    except ValueError:
        # Not JSON (e.g. a stray CLI banner line) — surface it as plain text
        # rather than silently dropping it.
        return [{"type": "text", "text": line}]

    ev_type = ev.get("type", "")

    if ev_type == "system" and ev.get("subtype") == "init":
        session_id = ev.get("session_id")
        return [{"type": "session", "session_id": session_id}] if session_id else []

    if ev_type == "assistant":
        return _parse_assistant_blocks(ev)

    if ev_type == "result":
        usage = ev.get("usage", {})
        return [{"type": "result", "usage": _usage(usage) if usage else None}]

    return []


def _parse_assistant_blocks(ev: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    msg = ev.get("message", {})
    for block in msg.get("content", []):
        bt = block.get("type", "")
        if bt == "thinking":
            events.append({"type": "thinking", "thinking": block.get("thinking", "")})
        elif bt == "text":
            events.append({"type": "text", "text": block.get("text", "")})
    usage = msg.get("usage", {})
    if usage:
        events.append({"type": "usage", "usage": _usage(usage)})
    return events


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
