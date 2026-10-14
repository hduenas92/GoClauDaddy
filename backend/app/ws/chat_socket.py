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
import json
import time

from fastapi import WebSocket, WebSocketDisconnect

from app.config import DEFAULT_WORKING_DIR, PORT, expected_permission_echo
from app.logging_setup import get_logger
from app.services import attachments_service as attachments
from app.services import claude_cli
from app.services import conversations_service as convs
from app.services import permission_broker
from app.services import projects_service as projects
from app.services.process_registry import registry
from app.services.redact import redact_secrets

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

# How often a text/thinking/usage checkpoint may write while streaming (P2-E).
# Module-level so the throttle rule is testable without sleeping 2 real seconds
# in a unit test.
CHECKPOINT_INTERVAL_SECONDS: float = 2.0

# One copy of the "gone" text, shared by the pre-send guard and the mid-reply
# guard: a conversation deleted from another tab must read the same as one that
# never existed.
CONVERSATION_GONE_ERROR = "This conversation no longer exists. Start a new chat."


def _monotonic_now() -> float:
    """One-line indirection so checkpoint-throttle tests can install a fake clock."""
    return time.monotonic()


async def handle_chat_socket(websocket: WebSocket, conversation_id: str) -> None:
    await websocket.accept()
    log.info("WS connected for conversation %s", conversation_id)

    try:
        while True:
            payload = await _receive_json_or_sentinel(websocket)
            msg_type = payload.get("type")

            if msg_type in ("approve", "approve_always", "deny", "approval_extend"):
                # Approval decisions go straight to the broker, which wakes the
                # CLI's pending MCP tools/call request. An unknown request_id is
                # harmless: the broker returns False and the CLI's own timeout
                # still denies (fail closed).
                request_id = payload.get("request_id")
                if msg_type == "approval_extend":
                    permission_broker.broker.extend(conversation_id, request_id)
                else:
                    decision = {
                        "approve": "allow_once",
                        "approve_always": "allow_always",
                        "deny": "deny",
                    }[msg_type]
                    permission_broker.broker.resolve(conversation_id, request_id, decision)
                continue

            if msg_type == "stop":
                stopped = registry.stop(conversation_id)
                await websocket.send_json({"type": "stopped", "did_stop": stopped})
                # Always send done so the frontend resets the composer regardless
                # of whether there was anything to stop.
                await websocket.send_json({"type": "done"})
                continue

            if msg_type != "send":
                await websocket.send_json({"type": "error", "error": "The app sent something the server didn't understand. Refresh the page and try again."})
                continue

            if registry.is_busy(conversation_id):
                await websocket.send_json(
                    {"type": "error", "error": "This conversation is already answering a message. Wait for it to finish or press Stop."}
                )
                continue

            # Fire-and-forget: registered immediately (no await between the
            # is_busy check above and this) so a second rapid `send` can't
            # slip past registry.is_busy() before this one is tracked.
            #
            # Record the owner at the same instant as the task: the disconnect
            # path below may stop this turn for THIS socket only.
            _turn_owner[conversation_id] = websocket
            task = asyncio.ensure_future(_handle_send(websocket, conversation_id, payload))
            registry.register_task(conversation_id, task)

    except WebSocketDisconnect:
        log.info("WS disconnected for conversation %s", conversation_id)
        # Only the socket that started the in-flight turn may stop it by
        # disconnecting, and only while a permission prompt is pending (a turn
        # that is streaming can finish headless). Two browser tabs share a
        # conversation_id but are separate sockets, so closing the second tab
        # must not kill the first tab's pending prompt. An explicit `stop`
        # frame from ANY socket still stops it -- that is the branch above and
        # is unchanged.
        if _turn_owner.get(conversation_id) is websocket and permission_broker.broker.has_pending(
            conversation_id
        ):
            registry.stop(conversation_id)


async def _handle_send(
    websocket: WebSocket,
    conversation_id: str,
    payload: dict,
) -> None:
    """N11 guard around the real handler.

    The per-turn SETUP below (everything before the streaming `try:`) used to sit
    outside any guard. A malformed payload, or any DB error while loading the
    conversation or project, therefore escaped a fire-and-forget task: **no frame
    of any kind reached the client**, `registry.clear()` never ran so the
    conversation could stay `status="busy"` forever, and nothing was logged —
    because the failed task stays referenced in `registry._tasks`, so asyncio
    never GC-logs it either. The turn simply vanished.

    This is the server-side mirror of N1: the frontend restores itself on `done`,
    and here was a path on which `done` could never arrive.

    Wrapped rather than restructured on purpose. The inner function's own
    `try/finally` already clears the registry, sets status and guarantees `done`
    for every failure DURING streaming; re-indenting the setup into that block
    would risk changing teardown semantics that are already correct and tested.

    `except Exception` deliberately does not catch `asyncio.CancelledError`,
    which is a BaseException in modern Python — a `/stop` must keep cancelling.
    """
    try:
        await _handle_send_inner(websocket, conversation_id, payload)
    except Exception:
        log.exception("Unhandled error in send setup for conversation %s", conversation_id)
        # Honour the done-always contract the composer relies on
        # (see :59-65 and :231-232): error first, then done.
        try:
            await websocket.send_json(
                {"type": "error", "error": "Something went wrong starting that turn. Try again; if it keeps failing, open the LOG panel."}
            )
        except (RuntimeError, WebSocketDisconnect):
            pass
        except Exception:
            log.warning(
                "Failed to send error frame to websocket for conversation %s",
                conversation_id,
                exc_info=True,
            )
        try:
            await websocket.send_json({"type": "done"})
        except (RuntimeError, WebSocketDisconnect):
            pass
        except Exception:
            log.warning(
                "Failed to send done frame to websocket for conversation %s",
                conversation_id,
                exc_info=True,
            )
        # Never leave the conversation wedged as busy.
        _release_owner(conversation_id, websocket)
        try:
            registry.clear(conversation_id)
        except Exception:
            log.warning(
                "Failed to clear process registry for conversation %s",
                conversation_id,
                exc_info=True,
            )
        try: convs.set_status(conversation_id, "idle")  # P2-R R3: was suppress — log the secondary failure instead of hiding it
        except Exception: log.warning("Failed to set status idle for conversation %s", conversation_id, exc_info=True)


async def _handle_send_inner(
    websocket: WebSocket,
    conversation_id: str,
    payload: dict,
) -> None:
    regenerate = bool(payload.get("regenerate"))
    prompt = (payload.get("message") or "").strip()
    attachment_ids = payload.get("attachment_ids") or []
    # Resolve attachments before the empty-prompt guard: an attachment-only send
    # is valid, but an empty send with no existing attachment must still fail
    # before anything is persisted.
    atts = attachments.get_attachments(attachment_ids) if not regenerate else []
    if not regenerate and not prompt and not atts:
        await websocket.send_json({"type": "error", "error": "Your message has no text. Add a sentence and send again."})
        await websocket.send_json({"type": "done"})
        return

    conv = convs.get_conversation(conversation_id)
    if not conv:
        await websocket.send_json(
            {"type": "error", "error": CONVERSATION_GONE_ERROR}
        )
        await websocket.send_json({"type": "done"})
        return

    project = projects.get_project(conv.project_id) if conv.project_id else None

    model = conv.model
    # A conversation with no stored mode is a manual-approval chat (ruling Q2):
    # the CLI must receive an explicit --permission-mode so its echo can be
    # checked against the requested mode.
    permission_mode = conv.permission_mode or "manual"
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
    #      survived a reload. Measured, not assumed — task 2.1, execution brief.
    #   2. The previous answer is superseded inside this same turn, rather than by
    #      a separate client DELETE that a failed send would leave half-applied.
    #      Soft, never a hard delete: the money was spent and the row is the record.
    prior_user_message = None
    if regenerate:
        prior_user_message = convs.last_live_user_message(conversation_id)
        if prior_user_message is None:
            await websocket.send_json(
                {"type": "error", "error": "There's no previous question here to regenerate. Send a message first."}
            )
            await websocket.send_json({"type": "done"})
            return
        prompt = (prior_user_message.content or "").strip()
        # The files belong to the stored question, so rebuild the refs from its row.
        atts = attachments.attachments_for_message(prior_user_message.id)
        if not prompt and not atts:
            await websocket.send_json(
                {"type": "error", "error": "There's no previous question here to regenerate. Send a message first."}
            )
            await websocket.send_json({"type": "done"})
            return

    # Attachments were uploaded separately (POST /api/attachments) and are
    # referenced here by id. The CLI is text-in/text-out, so the file's path
    # (not its bytes) is what actually reaches the model.
    if atts:
        refs = "\n".join(f"[Attached file: {a.stored_path}]" for a in atts)
        prompt_for_cli = f"{prompt}\n\n{refs}" if prompt else refs
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

    async def _notify_permission_frame(frame: dict) -> None:
        """Deliver one broker frame to the UI that owns this turn."""
        await websocket.send_json(frame)

    permission_token = permission_broker.broker.open_turn(
        conversation_id, cwd, _notify_permission_frame
    )
    # The CLI reaches the in-app MCP server on the same socket/port that
    # accepted this websocket. Fall back to config.PORT for direct handler
    # tests whose fake websocket has no ASGI scope.
    scope = getattr(websocket, "scope", None) or {}
    server = scope.get("server") or (None, None)
    port = server[1] if isinstance(server, (list, tuple)) and len(server) > 1 and server[1] else PORT
    prompt_tool_url = f"http://127.0.0.1:{port}/mcp/permission/{permission_token}"

    def _on_started(proc):
        registry.register_process(conversation_id, proc)

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls_map: dict[str, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    had_error = False
    sent_done = False
    cancelled = False
    conversation_deleted = False  # set when a checkpoint finds the row is gone
    assistant_message_id: str | None = None
    last_checkpoint_at: float | None = None

    def _checkpoint(now: float) -> None:
        """INSERT once, UPDATE after: persist what has streamed so far.

        While streaming the row is marked `stopped=1` (incomplete) regardless of
        how the turn will end; the final persistence below sets the true value.
        """
        nonlocal assistant_message_id, last_checkpoint_at, conversation_deleted
        # The conversation can be deleted from another tab while this one streams
        # (B101_N5). Writing anyway is a FOREIGN KEY failure for the first INSERT
        # and a 0-row no-op after, so stop the turn instead of writing anything.
        if convs.get_conversation(conversation_id) is None:
            conversation_deleted = True
            return
        tool_calls_json = json.dumps(list(tool_calls_map.values())) if tool_calls_map else None
        content = "".join(text_parts)
        thinking = "".join(thinking_parts) or None
        if assistant_message_id is None:
            msg = convs.add_message(
                conversation_id,
                "assistant",
                content,
                stopped=True,  # interim incomplete marker (P2-E)
                thinking=thinking,
                tool_calls=tool_calls_json,
                model=model,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                cache_creation_tokens=usage.get("cache_creation_input_tokens") or 0,
            )
            assistant_message_id = msg.id
        else:
            convs.update_message_content(
                assistant_message_id,
                content=content,
                thinking=thinking,
                tool_calls=tool_calls_json,
                model=model,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                cache_creation_tokens=usage.get("cache_creation_input_tokens") or 0,
                stopped=True,  # still incomplete while streaming
            )
        last_checkpoint_at = now

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
            prompt_tool_url=prompt_tool_url,
        ):
            ev_type = event.get("type")
            if ev_type == "permission_mode_echo":
                actual = event.get("mode")
                ok = expected_permission_echo(permission_mode) == actual
                if not ok:
                    log.warning(
                        "Permission mode mismatch for conversation %s: requested %r, CLI echoed %r",
                        conversation_id,
                        permission_mode,
                        actual,
                    )
                with contextlib.suppress(RuntimeError):
                    await websocket.send_json({
                        "type": "permission_mode",
                        "requested": permission_mode,
                        "actual": actual,
                        "ok": ok,
                    })
                continue  # mode handshake is not content; never stored or forwarded
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
                # Non-JSON CLI stdout (P2-B E1): log it (redacted — the raw
                # line may contain credentials), forward fixed copy to the
                # client as a visible non-fatal notice, and deliberately do NOT
                # append it to text_parts — it is not Claude's reply.
                cleaned = redact_secrets(event.get("text", ""))
                log.warning("Non-JSON CLI stdout line ignored: %s", cleaned)
                event = {"type": "notice", "text": NON_JSON_NOTICE_TEXT}

            # P2-E checkpoint rules, applied BEFORE the event is forwarded so a
            # hard kill that lands right after the client saw the frame still
            # has the frame's content on disk. tool_call/tool_result write
            # immediately (the side effect can exist before the result arrives);
            # text/thinking/usage share the 2 s throttle.
            now = _monotonic_now()
            if ev_type in ("tool_call", "tool_result"):
                _checkpoint(now)
            elif ev_type in ("text", "thinking", "usage"):
                if last_checkpoint_at is None or now - last_checkpoint_at >= CHECKPOINT_INTERVAL_SECONDS:
                    _checkpoint(now)
            if conversation_deleted:
                # The row vanished mid-reply: stop the turn here rather than
                # forwarding more of a reply the conversation cannot hold.
                break

            # Socket already closed (client navigated away)? Keep draining the
            # generator so the subprocess still finishes and persists.
            if ev_type == "done":
                sent_done = True
            with contextlib.suppress(RuntimeError):
                await websocket.send_json(event)
    except asyncio.CancelledError:
        cancelled = True  # marks the persisted row below; must still propagate
        raise  # expected — a /stop cancelled this task, not a real failure
    except Exception:  # noqa: BLE001 — this task is fire-and-forget; an
        # uncaught exception here would otherwise vanish into asyncio's default
        # "Task exception was never retrieved" logging instead of our own log
        # or a WS event the client can actually show the user.
        had_error = True
        log.exception("Unexpected error handling send for conversation %s", conversation_id)
        with contextlib.suppress(RuntimeError):
            await websocket.send_json(
                {"type": "error", "error": "Something went wrong during the reply. Try again; if it keeps failing, open the LOG panel."}
            )
    finally:
        # The turn is over for ANY reason (done, error, stop, owner window
        # closed): close the permission turn so its token dies and any still
        # pending prompt is denied (fail closed).
        permission_broker.broker.close_turn(permission_token)
        registry.clear(conversation_id)
        # The turn is over; it no longer has an owner that may stop it.
        _release_owner(conversation_id, websocket)
        convs.set_status(conversation_id, "error" if had_error else "idle")
        # B101_N5: the conversation was deleted mid-reply. Tell the client before
        # the closing `done`, so it gets exactly one error frame then the usual one.
        if conversation_deleted:
            with contextlib.suppress(RuntimeError):
                await websocket.send_json({"type": "error", "error": CONVERSATION_GONE_ERROR})
        if not sent_done:
            with contextlib.suppress(RuntimeError):
                await websocket.send_json({"type": "done"})
        full_text = "".join(text_parts)
        full_thinking = "".join(thinking_parts) or None
        tool_calls_json = json.dumps(list(tool_calls_map.values())) if tool_calls_map else None
        has_content = bool(
            full_text or full_thinking or tool_calls_json
            or usage.get("output_tokens") or usage.get("input_tokens")
        )
        # P2-E: if a checkpoint already INSERTed the row, the final persistence
        # UPDATEs it in place - never a second assistant row for one turn. If no
        # checkpoint ever fired (e.g. a turn that produced nothing until the
        # very end), the old INSERT path still applies.
        # B101_N5: when the conversation was deleted mid-reply there is nowhere to
        # write (the INSERT would fail its foreign key, the UPDATE would hit zero
        # rows), so neither path runs.
        if assistant_message_id is not None and not conversation_deleted:
            convs.update_message_content(
                assistant_message_id,
                content=full_text,
                thinking=full_thinking,
                tool_calls=tool_calls_json,
                model=model,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                cache_creation_tokens=usage.get("cache_creation_input_tokens") or 0,
                stopped=cancelled,
            )
        elif has_content and not conversation_deleted:
            # `content` is what the model said, nothing more. Cancellation is a
            # column (v15), not an HTML comment smuggled into the text — see the
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


# Fixed copy for the non-JSON CLI stdout notice frame (P2-B E1). The raw
# stdout line may contain credentials, so the forwarded frame carries no part
# of it; only the server log sees the redacted text.
NON_JSON_NOTICE_TEXT = "GoClaudaddy ignored an unexpected line from the Claude CLI."

# Which websocket started the in-flight turn for each conversation. Only that
# socket's disconnect may stop the turn (see handle_chat_socket's
# WebSocketDisconnect handler); a second socket disconnecting must not. Set
# when a `send` starts a turn and cleared when the turn ends. An explicit
# `stop` frame deliberately ignores this.
_turn_owner: dict[str, WebSocket] = {}


def _release_owner(conversation_id: str, websocket: WebSocket) -> None:
    # Only if still ours: the outer guard runs after awaits, by which time another tab may own a new turn.
    if _turn_owner.get(conversation_id) is websocket:
        del _turn_owner[conversation_id]


async def _receive_json_or_sentinel(websocket: WebSocket) -> dict:
    """Receive a client frame, treating malformed JSON as an unknown type.

    Returning an unrecognized `type` lets the main loop's existing
    unknown-message branch answer with a defined error frame and keep reading.
    Valid JSON that is not an object (a list, string, number, bool or null)
    is treated the same way: `receive_json()` hands it back as-is, and the
    loop's `payload.get(...)` would raise AttributeError out of
    handle_chat_socket and kill the socket.
    """
    try:
        payload = await websocket.receive_json()
    except json.JSONDecodeError:
        return {"type": "__invalid_json__"}
    if not isinstance(payload, dict):
        return {"type": "__invalid_json__"}
    return payload
