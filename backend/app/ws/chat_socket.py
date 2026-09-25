"""WebSocket handler: one connection per conversation, duplex (send + stop over the same socket).

Session continuity and message history are backed by SQLite (conversations_service)
rather than an in-memory dict â€” a conversation must already exist (created via
POST /api/conversations) before a WS connection to it will accept a send.

IMPORTANT: the main receive loop below must never block on a send's full
completion. `stop` has to be receivable *while* a send is in flight, or it
just sits unread until the turn ends on its own â€” a real bug this comment
exists because of, found by actually sending stop mid-stream and watching it
do nothing. `_handle_send` runs as its own fire-and-forget task; the loop goes
straight back to `receive_json()` after starting it.
"""

import asyncio
import contextlib
import json

from fastapi import WebSocket, WebSocketDisconnect

from app.config import DEFAULT_WORKING_DIR
from app.logging_setup import get_logger
from app.services import attachments_service as attachments
from app.services import claude_cli
from app.services import conversations_service as convs
from app.services import projects_service as projects
from app.services.process_registry import registry

log = get_logger("chat_socket")

# RETIRED in Phase 3 (4-P1).
#
# The comment that stood here justified the marker with "No schema change:
# avoids a v15 migration for one boolean." v15 shipped anyway, added the column,
# and then nothing wrote or read it for four migrations while this marker kept
# doing the job badly. Left on the record because the reasoning failed in a
# specific and repeatable way: it optimised against a migration, got the
# migration regardless, and ended with two representations of one fact.
#
# NOTHING writes this any more; the name is kept only so the string stays
# greppable and its history sits where someone would look for it. Do not
# reintroduce it: v18 indexes `content` verbatim into messages_fts, so the marker
# became part of the full-text index and a search for "claudioui" returned
# precisely the stopped turns. Pass `stopped=` to add_message() instead.
_RETIRED_STOPPED_MARKER = "\n\n<!-- claudioui:stopped -->"

# How long to wait for the user's approve/deny before DENYING. A module-level
# constant rather than a literal so the timeout path is reachable in a test
# without that test taking a minute — which is why it had no coverage at all.
APPROVAL_TIMEOUT_SECONDS: float = 60


async def handle_chat_socket(websocket: WebSocket, conversation_id: str) -> None:
    await websocket.accept()
    log.info("WS connected for conversation %s", conversation_id)

    # Shared queue for approval responses (approve/deny WS messages from frontend).
    # maxsize=1: only one approval can be pending at a time (the subprocess blocks on stdin).
    approval_queue: asyncio.Queue[bool] = asyncio.Queue(maxsize=1)

    try:
        while True:
            payload = await websocket.receive_json()
            msg_type = payload.get("type")

            if msg_type in ("approve", "deny"):
                # Drain stale entries (safety) then signal the waiting send-task
                while not approval_queue.empty():
                    approval_queue.get_nowait()
                approval_queue.put_nowait(msg_type == "approve")
                continue

            if msg_type == "stop":
                stopped = registry.stop(conversation_id)
                await websocket.send_json({"type": "stopped", "did_stop": stopped})
                # Always send done so the frontend resets the composer regardless
                # of whether there was anything to stop.
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
            task = asyncio.ensure_future(_handle_send(websocket, conversation_id, payload, approval_queue))
            registry.register_task(conversation_id, task)

    except WebSocketDisconnect:
        log.info("WS disconnected for conversation %s", conversation_id)
        # Intentionally NOT stopping an in-flight subprocess here â€” a browser
        # refresh mid-response shouldn't lose the reply. It finishes and
        # persists headless; the registry entry clears itself on completion.


async def _handle_send(
    websocket: WebSocket,
    conversation_id: str,
    payload: dict,
    approval_queue: asyncio.Queue,
) -> None:
    """N11 guard around the real handler.

    The per-turn SETUP below (everything before the streaming `try:`) used to sit
    outside any guard. A malformed payload, or any DB error while loading the
    conversation or project, therefore escaped a fire-and-forget task: **no frame
    of any kind reached the client**, `registry.clear()` never ran so the
    conversation could stay `status="busy"` forever, and nothing was logged â€”
    because the failed task stays referenced in `registry._tasks`, so asyncio
    never GC-logs it either. The turn simply vanished.

    This is the server-side mirror of N1: the frontend restores itself on `done`,
    and here was a path on which `done` could never arrive.

    Wrapped rather than restructured on purpose. The inner function's own
    `try/finally` already clears the registry, sets status and guarantees `done`
    for every failure DURING streaming; re-indenting the setup into that block
    would risk changing teardown semantics that are already correct and tested.

    `except Exception` deliberately does not catch `asyncio.CancelledError`,
    which is a BaseException in modern Python â€” a `/stop` must keep cancelling.
    """
    try:
        await _handle_send_inner(websocket, conversation_id, payload, approval_queue)
    except Exception:
        log.exception("Unhandled error in send setup for conversation %s", conversation_id)
        # Honour the done-always contract the composer relies on
        # (see :59-65 and :231-232): error first, then done.
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"type": "error", "error": "Something went wrong starting that turn. Check the logs folder for details."}
            )
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "done"})
        # Never leave the conversation wedged as busy.
        with contextlib.suppress(Exception):
            registry.clear(conversation_id)
        with contextlib.suppress(Exception):
            convs.set_status(conversation_id, "idle")


async def _handle_send_inner(
    websocket: WebSocket,
    conversation_id: str,
    payload: dict,
    approval_queue: asyncio.Queue,
) -> None:
    regenerate = bool(payload.get("regenerate"))
    prompt = (payload.get("message") or "").strip()
    if not regenerate and not prompt:
        await websocket.send_json({"type": "error", "error": "Empty message."})
        await websocket.send_json({"type": "done"})
        return

    conv = convs.get_conversation(conversation_id)
    if not conv:
        await websocket.send_json(
            {"type": "error", "error": "Conversation not found. Create it first via POST /api/conversations."}
        )
        await websocket.send_json({"type": "done"})
        return

    project = projects.get_project(conv.project_id) if conv.project_id else None

    model = conv.model
    permission_mode = conv.permission_mode
    # Conversation-level prompt overrides project-level; payload is not a source
    # of truth for system_prompt (the settings panel PATCHes the DB directly).
    system_prompt = conv.system_prompt or (project.system_prompt if project else None)
    thinking_budget = conv.thinking_budget
    max_tokens = conv.max_tokens
    cwd = (project.working_dir if project else None) or DEFAULT_WORKING_DIR

    # Regenerate means "answer that question again", and two things make it
    # different from a send. Both are load-bearing:
    #   1. The question is read back from the DB and is NOT written again. Routing
    #      regenerate through the ordinary send path appended a second user row,
    #      so the transcript read [user][user][assistant] and the duplicate
    #      survived a reload. Measured, not assumed â€” task 2.1, execution brief.
    #   2. The previous answer is superseded inside this same turn, rather than by
    #      a separate client DELETE that a failed send would leave half-applied.
    #      Soft, never a hard delete: the money was spent and the row is the record.
    prior_user_message = None
    if regenerate:
        prior_user_message = convs.last_live_user_message(conversation_id)
        if prior_user_message is None or not (prior_user_message.content or "").strip():
            await websocket.send_json(
                {"type": "error", "error": "There is no previous question in this conversation to regenerate."}
            )
            await websocket.send_json({"type": "done"})
            return
        prompt = prior_user_message.content.strip()

    # Attachments were uploaded separately (POST /api/attachments) and are
    # referenced here by id. The CLI is text-in/text-out, so the file's path
    # (not its bytes) is what actually reaches the model.
    if regenerate:
        # The files belong to the stored question, so rebuild the refs from its row.
        atts = attachments.attachments_for_message(prior_user_message.id)
    else:
        attachment_ids = payload.get("attachment_ids") or []
        atts = attachments.get_attachments(attachment_ids)
    if atts:
        refs = "\n".join(f"[Attached file: {a.stored_path}]" for a in atts)
        prompt_for_cli = f"{prompt}\n\n{refs}"
    else:
        prompt_for_cli = prompt

    if regenerate:
        convs.supersede_last_assistant(conversation_id)
    else:
        # Crash-safe: the user's message is durable before we even start streaming,
        # so a crash mid-response never loses what was actually asked.
        user_message = convs.add_message(conversation_id, "user", prompt)
        attachments.attach_to_message(attachment_ids, user_message.id)
    convs.set_status(conversation_id, "busy")

    proc_stdin: list = [None]  # mutable container captured by closure below

    def _on_started(proc):
        registry.register_process(conversation_id, proc)
        proc_stdin[0] = proc.stdin  # capture for approval responses

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls_map: dict[str, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    had_error = False
    sent_done = False
    cancelled = False
    try:
        async for event in claude_cli.run(
            prompt=prompt_for_cli,
            model=model,
            cwd=cwd,
            permission_mode=permission_mode,
            system_prompt=system_prompt,
            thinking_budget=thinking_budget,
            max_tokens=max_tokens,
            session_id=conv.session_id,
            on_process_started=_on_started,
        ):
            ev_type = event.get("type")
            if ev_type == "approval_needed":
                # Forward to frontend; wait for approve/deny back over the same WS.
                # The subprocess is blocked on stdin at this point, so stdout is quiet
                # until we write y/n â€” no events are missed during the await.
                with contextlib.suppress(RuntimeError):
                    await websocket.send_json(event)
                try:
                    approved = await asyncio.wait_for(
                        approval_queue.get(), timeout=APPROVAL_TIMEOUT_SECONDS
                    )
                except TimeoutError:
                    # FAIL CLOSED. This used to be `approved = True` with the
                    # comment "timeout = auto-approve, keep stream alive", and
                    # nothing was emitted — so a user who stepped away, or whose
                    # socket had already closed (the send above is wrapped in
                    # suppress(RuntimeError), so the prompt may never have been
                    # rendered at all), had the tool run on their behalf with no
                    # record of it. A gate that exists to withhold consent must
                    # not grant it by default. Decided with Houston 2026-09-19.
                    approved = False
                    notice = (
                        f"\n\n_No answer within {APPROVAL_TIMEOUT_SECONDS:g}s, so this tool "
                        f"was **not** run. Send the message again to retry it._\n\n"
                    )
                    text_parts.append(notice)
                    with contextlib.suppress(RuntimeError):
                        await websocket.send_json({"type": "text", "text": notice})
                stdin = proc_stdin[0]
                if stdin and not stdin.is_closing():
                    stdin.write(b"y\n" if approved else b"n\n")
                    with contextlib.suppress(Exception):
                        await stdin.drain()
                continue  # not a content event; skip all downstream accumulation
            if ev_type == "session":
                convs.set_session_id(conversation_id, event["session_id"])
            elif ev_type == "text":
                text_parts.append(event.get("text", ""))
            elif ev_type == "thinking":
                thinking_parts.append(event.get("thinking", ""))
            elif ev_type == "tool_call":
                tc_id = event.get("id", "")
                tool_calls_map[tc_id] = {"id": tc_id, "name": event.get("name", ""), "input": event.get("input", {})}
            elif ev_type == "tool_result":
                tc_id = event.get("tool_use_id", "")
                if tc_id in tool_calls_map:
                    tool_calls_map[tc_id]["output"] = event.get("content", "")
                    tool_calls_map[tc_id]["is_error"] = event.get("is_error", False)
            elif ev_type == "result" and event.get("usage"):
                usage = event["usage"]   # authoritative final totals
            elif ev_type == "usage" and event.get("usage"):
                step = event["usage"]
                usage["output_tokens"] = usage.get("output_tokens", 0) + step.get("output_tokens", 0)
                if step.get("input_tokens"):
                    usage["input_tokens"] = step["input_tokens"]
                if step.get("cache_read_input_tokens"):
                    usage["cache_read_input_tokens"] = step["cache_read_input_tokens"]
                if step.get("cache_creation_input_tokens"):
                    usage["cache_creation_input_tokens"] = step["cache_creation_input_tokens"]
            elif ev_type in ("error", "timeout"):
                had_error = True
            elif ev_type == "notice":
                # Non-JSON CLI stdout (P2-B E1): log it, forward it to the
                # client as a visible non-fatal notice, and deliberately do NOT
                # append it to text_parts — it is not Claude's reply.
                log.warning("Non-JSON CLI stdout line ignored: %s", event.get("text", ""))

            # Socket already closed (client navigated away)? Keep draining the
            # generator so the subprocess still finishes and persists.
            if ev_type == "done":
                sent_done = True
            with contextlib.suppress(RuntimeError):
                await websocket.send_json(event)
    except asyncio.CancelledError:
        cancelled = True  # marks the persisted row below; must still propagate
        raise  # expected â€” a /stop cancelled this task, not a real failure
    except Exception:  # noqa: BLE001 â€” this task is fire-and-forget; an
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
        if not sent_done:
            with contextlib.suppress(RuntimeError):
                await websocket.send_json({"type": "done"})
        full_text = "".join(text_parts)
        full_thinking = "".join(thinking_parts) or None
        tool_calls_json = json.dumps(list(tool_calls_map.values())) if tool_calls_map else None
        if full_text or full_thinking or tool_calls_json or usage.get("output_tokens") or usage.get("input_tokens"):
            # `content` is what the model said, nothing more. Cancellation is a
            # column (v15), not an HTML comment smuggled into the text â€” see the
            # note on STOPPED_MARKER above.
            convs.add_message(
                conversation_id,
                "assistant",
                full_text,
                stopped=cancelled,
                thinking=full_thinking,
                tool_calls=tool_calls_json,
                model=model,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                cache_creation_tokens=usage.get("cache_creation_input_tokens") or 0,
            )
