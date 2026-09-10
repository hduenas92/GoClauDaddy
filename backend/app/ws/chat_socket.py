"""WebSocket handler: one connection per conversation, duplex (send + stop over the same socket).

Session continuity and message history are backed by SQLite (conversations_service)
rather than an in-memory dict — a conversation must already exist (created via
POST /api/conversations) before a WS connection to it will accept a send.

IMPORTANT: the main receive loop below must never block on a send's full
completion. `stop` has to be receivable *while* a send is in flight, or it
just sits unread until the turn ends on its own — a real bug this comment
exists because of, found by actually sending stop mid-stream and watching it
do nothing. `_handle_send` runs as its own fire-and-forget task; the loop goes
straight back to `receive_json()` after starting it.
"""

import asyncio
import contextlib

from fastapi import WebSocket, WebSocketDisconnect

from app.config import DEFAULT_WORKING_DIR
from app.logging_setup import get_logger
from app.services import attachments_service as attachments
from app.services import claude_cli
from app.services import conversations_service as convs
from app.services import projects_service as projects
from app.services.process_registry import registry

log = get_logger("chat_socket")


async def handle_chat_socket(websocket: WebSocket, conversation_id: str) -> None:
    await websocket.accept()
    log.info("WS connected for conversation %s", conversation_id)

    try:
        while True:
            payload = await websocket.receive_json()
            msg_type = payload.get("type")

            if msg_type == "stop":
                stopped = registry.stop(conversation_id)
                await websocket.send_json({"type": "stopped", "did_stop": stopped})
                if stopped:
                    # claude_cli.run() skips its own trailing `done` event on
                    # cancellation (it re-raises instead) - send it here so the
                    # frontend's existing done-keyed UI reset still fires.
                    await websocket.send_json({"type": "done"})
                continue

            if msg_type != "send":
                await websocket.send_json({"type": "error", "error": f"Unknown message type: {msg_type}"})
                continue

            if registry.is_busy(conversation_id):
                await websocket.send_json(
                    {"type": "error", "error": "This conversation is already processing a message."}
                )
                continue

            # Fire-and-forget: registered immediately (no await between the
            # is_busy check above and this) so a second rapid `send` can't
            # slip past registry.is_busy() before this one is tracked.
            task = asyncio.ensure_future(_handle_send(websocket, conversation_id, payload))
            registry.register_task(conversation_id, task)

    except WebSocketDisconnect:
        log.info("WS disconnected for conversation %s", conversation_id)
        # Intentionally NOT stopping an in-flight subprocess here — a browser
        # refresh mid-response shouldn't lose the reply. It finishes and
        # persists headless; the registry entry clears itself on completion.


async def _handle_send(websocket: WebSocket, conversation_id: str, payload: dict) -> None:
    prompt = (payload.get("message") or "").strip()
    if not prompt:
        await websocket.send_json({"type": "error", "error": "Empty message."})
        return

    conv = convs.get_conversation(conversation_id)
    if not conv:
        await websocket.send_json(
            {"type": "error", "error": "Conversation not found. Create it first via POST /api/conversations."}
        )
        return

    project = projects.get_project(conv.project_id) if conv.project_id else None

    model = payload.get("model") or conv.model
    permission_mode = payload.get("permission_mode") or conv.permission_mode
    system_prompt = payload.get("system_prompt") or (project.system_prompt if project else None)
    cwd = (project.working_dir if project else None) or DEFAULT_WORKING_DIR

    # Attachments were uploaded separately (POST /api/attachments) and are
    # referenced here by id. The CLI is text-in/text-out, so the file's path
    # (not its bytes) is what actually reaches the model.
    attachment_ids = payload.get("attachment_ids") or []
    atts = attachments.get_attachments(attachment_ids)
    if atts:
        refs = "\n".join(f"[Attached file: {a.stored_path}]" for a in atts)
        prompt_for_cli = f"{prompt}\n\n{refs}"
    else:
        prompt_for_cli = prompt

    # Crash-safe: the user's message is durable before we even start streaming,
    # so a crash mid-response never loses what was actually asked.
    user_message = convs.add_message(conversation_id, "user", prompt)
    attachments.attach_to_message(attachment_ids, user_message.id)
    convs.set_status(conversation_id, "busy")

    def _on_started(proc):
        registry.register_process(conversation_id, proc)

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    had_error = False
    try:
        async for event in claude_cli.run(
            prompt=prompt_for_cli,
            model=model,
            cwd=cwd,
            permission_mode=permission_mode,
            system_prompt=system_prompt,
            session_id=conv.session_id,
            on_process_started=_on_started,
        ):
            ev_type = event.get("type")
            if ev_type == "session":
                convs.set_session_id(conversation_id, event["session_id"])
            elif ev_type == "text":
                text_parts.append(event.get("text", ""))
            elif ev_type == "thinking":
                thinking_parts.append(event.get("thinking", ""))
            elif ev_type in ("usage", "result") and event.get("usage"):
                usage = event["usage"]
            elif ev_type in ("error", "timeout"):
                had_error = True

            # Socket already closed (client navigated away)? Keep draining the
            # generator so the subprocess still finishes and persists.
            with contextlib.suppress(RuntimeError):
                await websocket.send_json(event)
    except asyncio.CancelledError:
        raise  # expected — a /stop cancelled this task, not a real failure
    except Exception:  # noqa: BLE001 — this task is fire-and-forget; an
        # uncaught exception here would otherwise vanish into asyncio's default
        # "Task exception was never retrieved" logging instead of our own log
        # or a WS event the client can actually show the user.
        had_error = True
        log.exception("Unexpected error handling send for conversation %s", conversation_id)
        with contextlib.suppress(RuntimeError):
            await websocket.send_json(
                {"type": "error", "error": "Something went wrong. Check the logs folder for details."}
            )
    finally:
        registry.clear(conversation_id)
        convs.set_status(conversation_id, "error" if had_error else "idle")
        full_text = "".join(text_parts)
        full_thinking = "".join(thinking_parts) or None
        if full_text or full_thinking:
            convs.add_message(
                conversation_id,
                "assistant",
                full_text,
                thinking=full_thinking,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )
