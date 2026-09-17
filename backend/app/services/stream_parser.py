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
        # Check for interactive permission prompt text (non-interactive --print mode may still emit these)
        if re.search(r'\[y/n\]|\(y/n\)|allow\s+\S.*\?', line, re.I):
            tool_m = re.search(r'(?:allow|run|execute)\s+(\w+)', line, re.I)
            tool = tool_m.group(1) if tool_m else "tool"
            return [{"type": "approval_needed", "tool": tool, "action": line.strip(), "id": ""}]
        # Not JSON (e.g. a stray CLI banner line) — surface it as plain text
        # rather than silently dropping it.
        return [{"type": "text", "text": line}]

    ev_type = ev.get("type", "")

    if ev_type == "system":
        subtype = ev.get("subtype", "")
        if subtype == "init":
            session_id = ev.get("session_id")
            return [{"type": "session", "session_id": session_id}] if session_id else []
        # Claude CLI emits permission requests as system events in stream-json format
        if "permission" in subtype or subtype in ("tool_permission", "approval_request", "tool_use_confirm"):
            return [{"type": "approval_needed",
                     "tool": ev.get("tool", ev.get("toolName", "")),
                     "action": str(ev.get("action", ev.get("description", ev.get("input", "")))),
                     "id": ev.get("id", "")}]
        return []

    if ev_type == "assistant":
        return _parse_assistant_blocks(ev)

    if ev_type == "result":
        if ev.get("is_error"):
            return [{"type": "error", "error": ev.get("result") or "CLI returned an error"}]
        usage = ev.get("usage", {})
        return [{"type": "result", "usage": _usage(usage) if usage else None}]

    if ev_type == "user":
        return _parse_user_blocks(ev)

    return []


def _parse_user_blocks(ev: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in ev.get("message", {}).get("content", []):
        if block.get("type") != "tool_result":
            continue
        raw = block.get("content", "")
        text = "".join(b.get("text", "") for b in raw if b.get("type") == "text") if isinstance(raw, list) else str(raw)
        events.append({
            "type": "tool_result",
            "tool_use_id": block.get("tool_use_id", ""),
            "content": text,
            "is_error": block.get("is_error", False),
        })
    return events


def _parse_assistant_blocks(ev: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    msg = ev.get("message", {})
    for block in msg.get("content", []):
        bt = block.get("type", "")
        if bt == "thinking":
            events.append({"type": "thinking", "thinking": block.get("thinking", "")})
        elif bt == "text":
            events.append({"type": "text", "text": block.get("text", "")})
        elif bt == "tool_use":
            events.append({
                "type": "tool_call",
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input": block.get("input", {}),
            })
    usage = msg.get("usage", {})
    if usage:
        events.append({"type": "usage", "usage": _usage(usage)})
    return events


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    return {
        "input_tokens":                usage.get("input_tokens", 0),
        "output_tokens":               usage.get("output_tokens", 0),
        "cache_read_input_tokens":     usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
    }
