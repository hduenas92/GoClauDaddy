"""MCP endpoint the claude CLI calls for tool permissions (WP-AP J2).

POST /mcp/permission/{token} is a minimal JSON-RPC 2.0 server over HTTP
(MCP streamable-http as CLI 2.1.291 uses it). It exposes exactly one tool,
``approve``, whose result is the CLI decision dict returned by the broker.
"""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import app.services.permission_broker as permission_broker

router = APIRouter()

_DENY_DECISION = {"behavior": "deny", "message": "The permission check failed, so the tool was denied."}
_SERVER_NAME = "GoClaudaddy"
_SERVER_VERSION = "1.0.0"


def _rpc_ok(request_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _http_error(request_id, code: int, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_rpc_error(request_id, code, message))


def _approve_tool() -> dict:
    return {
        "name": "approve",
        "description": "Ask the user to allow or deny one Claude tool permission request.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_use_id": {"type": "string"},
            },
            "required": ["tool_name", "input"],
        },
    }


@router.post("/mcp/permission/{token}")
async def permission_mcp(token: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _http_error(None, -32700, "Parse error", 400)

    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        return _http_error(body.get("id") if isinstance(body, dict) else None, -32600, "Invalid Request", 400)

    method = body["method"]
    request_id = body.get("id")
    params = body.get("params") if isinstance(body.get("params"), dict) else {}

    if method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "initialize":
        protocol_version = params.get("protocolVersion") or "2025-06-18"
        return _rpc_ok(request_id, {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        })

    if method == "ping":
        return _rpc_ok(request_id, {})

    if method == "tools/list":
        return _rpc_ok(request_id, {"tools": [_approve_tool()]})

    if method == "tools/call":
        if params.get("name") != "approve":
            return _http_error(request_id, -32602, "Unknown tool", 200)

        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        tool_name = arguments.get("tool_name")
        tool_input = arguments.get("input")
        tool_use_id = arguments.get("tool_use_id")

        try:
            decision = await asyncio.wait_for(
                permission_broker.broker.decide(token, tool_name, tool_input, tool_use_id),
                timeout=permission_broker.APPROVAL_TIMEOUT_SECONDS
                + permission_broker.APPROVAL_MAX_SECONDS
                + 1.0,
            )
        except Exception:
            decision = _DENY_DECISION

        return _rpc_ok(request_id, {
            "content": [{"type": "text", "text": json.dumps(decision)}],
        })

    return _http_error(request_id, -32601, "Method not found", 200)
