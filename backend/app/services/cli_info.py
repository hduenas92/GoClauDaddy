"""In-memory snapshot of the latest `claude` CLI system/init stream-json event.

Deliberately file-free and tiny: the right column only needs what the most
recent init reported, so one module-level dict is the whole store.
"""

from datetime import datetime, timezone
from typing import Any


def _empty_snapshot() -> dict[str, Any]:
    return {
        "captured_at": None,
        "model": None,
        "claude_code_version": None,
        "tools": [],
        "skills": [],
        "slash_commands": [],
        "agents": [],
        "plugins": [],
        "mcp_servers": [],
    }


_snapshot: dict[str, Any] = _empty_snapshot()


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _plugin_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def _mcp_servers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    servers: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        servers.append({"name": name, "status": item.get("status")})
    return servers


def record_init(ev: dict[str, Any]) -> None:
    """Replace the snapshot with the latest init event's data."""
    global _snapshot
    _snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "model": _string_or_none(ev.get("model")),
        "claude_code_version": _string_or_none(ev.get("claude_code_version")),
        "tools": _string_list(ev.get("tools")),
        "skills": _string_list(ev.get("skills")),
        "slash_commands": _string_list(ev.get("slash_commands")),
        "agents": _string_list(ev.get("agents")),
        "plugins": _plugin_names(ev.get("plugins")),
        "mcp_servers": _mcp_servers(ev.get("mcp_servers")),
    }


def get_snapshot() -> dict[str, Any]:
    return _snapshot
