"""CRUD + message persistence for conversations. All queries parameterized — never
string-format user input into SQL.
"""

import datetime
import shutil
import uuid

from app.config import ATTACHMENTS_DIR, DEFAULT_MODEL
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
    conversation_id: str, *, model: str | None = None, permission_mode: str | None = None
) -> None:
    fields, params = [], []
    if model is not None:
        fields.append("model = ?")
        params.append(model)
    if permission_mode is not None:
        fields.append("permission_mode = ?")
        params.append(permission_mode)
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
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversations SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), conversation_id),
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
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> Message:
    msg_id = str(uuid.uuid4())
    now = _now()
    with get_connection() as conn:
        next_seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, thinking, input_tokens, output_tokens, seq, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, conversation_id, role, content, thinking, input_tokens, output_tokens, next_seq, now),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    return Message(
        id=msg_id,
        conversation_id=conversation_id,
        role=role,
        content=content,
        thinking=thinking,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        seq=next_seq,
        created_at=now,
    )


def list_messages(conversation_id: str) -> list[Message]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC", (conversation_id,)
        ).fetchall()
    return [Message.from_row(r) for r in rows]
