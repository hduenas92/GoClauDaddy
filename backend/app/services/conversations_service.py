"""CRUD + message persistence for conversations. All queries parameterized — never
string-format user input into SQL.
"""

import datetime
import json
import re
import shutil
import uuid

from app.config import ATTACHMENTS_DIR, DEFAULT_MODEL, MODELS
from app.db.connection import get_connection
from app.logging_setup import get_logger
from app.models.conversation import Conversation
from app.models.message import Message

log = get_logger("conversations")


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def create_conversation(
    name: str | None = None, project_id: str | None = None, model: str = DEFAULT_MODEL
) -> Conversation:
    conv_id = str(uuid.uuid4())
    now = _now()
    name = name or f"Chat {datetime.datetime.now().strftime('%b %d %H:%M')}"
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO conversations
               (id, project_id, name, session_id, model, permission_mode, status, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, NULL, 'idle', ?, ?)""",
            (conv_id, project_id, name, model, now, now),
        )
    return get_conversation(conv_id)  # type: ignore[return-value]


def get_conversation(conversation_id: str) -> Conversation | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return Conversation.from_row(row) if row else None


_VALID_MODEL_IDS = {m["id"] for m in MODELS}


def get_conversation_healed(conversation_id: str) -> tuple[Conversation | None, str | None]:
    """Like get_conversation, but self-heals a stale/invalid stored model to the
    default and persists the fix. Returns (conversation, invalid_model_replaced)
    where the second element is None when no correction was needed (including
    when model is NULL — that's a legitimate "use the default" state, not a defect).
    """
    conv = get_conversation(conversation_id)
    if conv is None:
        return None, None
    if conv.model is not None and conv.model not in _VALID_MODEL_IDS:
        invalid_model = conv.model
        update_conversation_settings(conversation_id, model=DEFAULT_MODEL)
        return get_conversation(conversation_id), invalid_model
    return conv, None


def list_conversations(project_id: str | None = None) -> list[Conversation]:
    with get_connection() as conn:
        if project_id is not None:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    return [Conversation.from_row(r) for r in rows]


def rename_conversation(conversation_id: str, name: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversations SET name = ?, updated_at = ? WHERE id = ?",
            (name, _now(), conversation_id),
        )


def update_conversation_settings(
    conversation_id: str,
    *,
    model: str | None = None,
    permission_mode: str | None = None,
    system_prompt: str | None = None,
    thinking_budget: int | None = None,
    max_tokens: int | None = None,
    clear_system_prompt: bool = False,
    clear_thinking_budget: bool = False,
    clear_max_tokens: bool = False,
) -> None:
    fields, params = [], []
    if model is not None:
        fields.append("model = ?")
        params.append(model)
    if permission_mode is not None:
        fields.append("permission_mode = ?")
        params.append(permission_mode)
    if system_prompt is not None:
        fields.append("system_prompt = ?")
        params.append(system_prompt)
    elif clear_system_prompt:
        fields.append("system_prompt = NULL")
    if thinking_budget is not None:
        fields.append("thinking_budget = ?")
        params.append(thinking_budget)
    elif clear_thinking_budget:
        fields.append("thinking_budget = NULL")
    if max_tokens is not None:
        fields.append("max_tokens = ?")
        params.append(max_tokens)
    elif clear_max_tokens:
        fields.append("max_tokens = NULL")
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(conversation_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?", params)


def set_session_id(conversation_id: str, session_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversations SET session_id = ?, updated_at = ? WHERE id = ?",
            (session_id, _now(), conversation_id),
        )


def set_status(conversation_id: str, status: str) -> None:
    now = _now()
    with get_connection() as conn:
        if status == "busy":
            conn.execute(
                "UPDATE conversations SET status = ?, updated_at = ?, started_at = COALESCE(started_at, ?) WHERE id = ?",
                (status, now, now, conversation_id),
            )
        elif status in ("idle", "error"):
            conn.execute(
                "UPDATE conversations SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                (status, now, now, conversation_id),
            )
        else:
            conn.execute(
                "UPDATE conversations SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, conversation_id),
            )


def delete_conversation(conversation_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    # DB cascade removes the attachment rows but not the files on disk.
    conv_attachments_dir = ATTACHMENTS_DIR / conversation_id
    if conv_attachments_dir.exists():
        try:
            shutil.rmtree(conv_attachments_dir)
        except OSError as exc:
            log.warning("Could not remove attachments dir for %s: %s", conversation_id, exc)


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    thinking: str | None = None,
    tool_calls: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    model: str | None = None,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> Message:
    msg_id = str(uuid.uuid4())
    now = _now()
    with get_connection() as conn:
        next_seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, thinking, tool_calls, input_tokens, output_tokens,
                model, cache_read_tokens, cache_creation_tokens, seq, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, conversation_id, role, content, thinking, tool_calls, input_tokens, output_tokens,
             model, cache_read_tokens, cache_creation_tokens, next_seq, now),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    return Message(
        id=msg_id,
        conversation_id=conversation_id,
        role=role,
        content=content,
        thinking=thinking,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        seq=next_seq,
        created_at=now,
    )


_AUTO_NAME_RE = re.compile(r"^Chat \w+ \d+ \d+:\d+$")


def derive_title(text: str, max_words: int = 7, max_chars: int = 55) -> str:
    cleaned = re.sub(r"```[\s\S]*?```", " ", text)
    cleaned = re.sub(r"`[^`]+`", " ", cleaned)
    cleaned = re.sub(r"[#*_~>`\[\]!]", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    words = cleaned.split()[:max_words]
    title = " ".join(words)
    if len(title) > max_chars:
        title = title[:max_chars].rsplit(" ", 1)[0]
    t = title.strip()
    return (t[:1].upper() + t[1:]) or "New Conversation"


def auto_title_conversation(conversation_id: str) -> None:
    conv = get_conversation(conversation_id)
    if not conv or not _AUTO_NAME_RE.match(conv.name):
        return
    msgs = list_messages(conversation_id)
    user_msgs = [m for m in msgs if m.role == "user"]
    if not user_msgs:
        return
    rename_conversation(conversation_id, derive_title(user_msgs[0].content))


# Duplicated from app.ws.chat_socket.STOPPED_MARKER rather than imported: the
# service layer must not depend on the websocket layer. Matched by substring
# since it's always appended, never re-anchored to a specific position.
_STOPPED_MARKER = "\n\n<!-- claudioui:stopped -->"

_TOOL_INPUT_KEYS = ("file_path", "path", "command", "pattern", "query", "url")
_TOOL_OUTPUT_LIMIT = 800


def _strip_stopped(content: str) -> tuple[str, bool]:
    if _STOPPED_MARKER in content:
        return content.replace(_STOPPED_MARKER, ""), True
    return content, False


def _fence_for(text: str) -> str:
    """Backtick fence longer than any backtick run already in text (CommonMark-safe)."""
    max_run = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        max_run = max(max_run, run)
    return "`" * max(max_run + 1, 3)


def _fenced(text: str) -> str:
    fence = _fence_for(text)
    return f"{fence}\n{text}\n{fence}"


def _tool_input_summary(input_obj) -> str:
    if not isinstance(input_obj, dict) or not input_obj:
        return ""
    for key in _TOOL_INPUT_KEYS:
        if input_obj.get(key) is not None:
            return str(input_obj[key])
    s = json.dumps(input_obj, separators=(",", ":"))
    return s if len(s) <= 120 else s[:120] + "..."


def _tool_calls_block(tool_calls_json: str | None) -> str:
    if not tool_calls_json:
        return ""
    try:
        tcs = json.loads(tool_calls_json)
    except (ValueError, TypeError):
        return ""
    if not isinstance(tcs, list) or not tcs:
        return ""
    items = []
    for tc in tcs:
        if not isinstance(tc, dict):
            continue
        name = tc.get("name") or "tool"
        summary = _tool_input_summary(tc.get("input"))
        header = f"**{name}**" + (f" `{summary}`" if summary else "")
        if "output" not in tc:
            body = "_(pending)_"
        else:
            out = _truncate_output(str(tc.get("output") or ""))
            body = _fenced(out)
            if tc.get("is_error"):
                body = "**Error:**\n" + body
        items.append(f"{header}\n{body}")
    if not items:
        return ""
    n = len(items)
    label = "tool call" if n == 1 else "tool calls"
    return f"<details><summary>{n} {label}</summary>\n\n" + "\n\n".join(items) + "\n\n</details>\n"


def _truncate_output(out: str) -> str:
    if len(out) <= _TOOL_OUTPUT_LIMIT:
        return out
    return out[:_TOOL_OUTPUT_LIMIT] + "\n... (truncated)"


def _thinking_block(thinking: str | None) -> str:
    if not thinking:
        return ""
    return f"<details><summary>Thinking</summary>\n\n{_fenced(thinking)}\n\n</details>\n"


def export_as_markdown(conversation_id: str) -> str:
    conv = get_conversation(conversation_id)
    if not conv:
        return ""
    msgs = list_messages(conversation_id)
    lines = [f"# {conv.name}\n"]
    for m in msgs:
        if m.role == "user":
            content, stopped = _strip_stopped(m.content or "")
            lines.append(f"**You:** {content}\n")
            if stopped:
                lines.append("_(stopped)_\n")
            continue
        lines.append("**Claude:**\n")
        thinking_block = _thinking_block(m.thinking)
        if thinking_block:
            lines.append(thinking_block)
        content, stopped = _strip_stopped(m.content or "")
        if content:
            lines.append(f"{content}\n")
        tool_block = _tool_calls_block(m.tool_calls)
        if tool_block:
            lines.append(tool_block)
        if stopped:
            lines.append("_(stopped)_\n")
    return "\n".join(lines)


def delete_last_message(conversation_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM messages WHERE conversation_id = ? AND role = 'assistant' AND superseded_by IS NULL ORDER BY seq DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if not row:
            return False
        # ponytail: soft delete — mark superseded instead of hard delete, history reversible
        conn.execute("UPDATE messages SET superseded_by = ? WHERE id = ?", (f"{row['id']}:regen", row["id"]))
    return True


def list_messages(conversation_id: str) -> list[Message]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC", (conversation_id,)
        ).fetchall()
    return [Message.from_row(r) for r in rows]
