"""File upload handling. The `claude` CLI is text-in/text-out, so a file's path
(not its bytes) is what actually reaches the model — see chat_socket.py, which
appends a "[Attached file: <path>]" line to the prompt. Storing under a
conversation-scoped directory (not a swept OS temp dir) means attachments
survive restarts and can be re-referenced across turns of the same conversation.
"""

import contextlib
import datetime
import errno
import re
import uuid
from pathlib import Path

from app.config import ALLOWED_ATTACHMENT_EXTENSIONS, ATTACHMENTS_DIR, MAX_UPLOAD_BYTES
from app.db.connection import get_connection
from app.logging_setup import get_logger
from app.models.attachment import Attachment

log = get_logger("attachments")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class AttachmentRejected(ValueError):
    pass


class DiskFull(OSError):
    """Distinct out-of-space error — maps to HTTP 507, never a generic 500."""

    def __init__(self) -> None:
        super().__init__("The disk is full — free up some space, then try again.")


class ConversationNotFound(LookupError):
    pass


def _sanitize_filename(name: str) -> str:
    # Strip any path component (handles both / and \ separators, and any
    # leading ".." traversal attempt), then collapse everything else to a
    # safe charset. The uuid prefix added at save time guarantees uniqueness
    # even after sanitizing collapses two different names to the same string.
    base = Path(name).name
    base = _UNSAFE.sub("_", base)
    return base or "file"


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def save_attachment(
    conversation_id: str, original_name: str, data: bytes, mime_type: str | None
) -> Attachment:
    if len(data) > MAX_UPLOAD_BYTES:
        raise AttachmentRejected(f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit")

    safe_name = _sanitize_filename(original_name)
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise AttachmentRejected(f"File type '{ext}' is not allowed")

    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not exists:
        raise ConversationNotFound(conversation_id)

    conv_dir = ATTACHMENTS_DIR / conversation_id
    conv_dir.mkdir(parents=True, exist_ok=True)

    attachment_id = str(uuid.uuid4())
    stored_name = f"{attachment_id}_{safe_name}"
    stored_path = conv_dir / stored_name
    try:
        stored_path.write_bytes(data)
    except OSError as exc:
        # ENOSPC (POSIX) or ERROR_DISK_FULL (winerror 112) — a distinct,
        # specific failure the frontend can word differently from "too large".
        if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) == 112:
            log.exception("Failed to write attachment %s (disk full): %s", stored_name, exc)
            with contextlib.suppress(OSError):
                stored_path.unlink(missing_ok=True)  # don't leave a partial file
            raise DiskFull() from exc
        raise

    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO attachments
               (id, message_id, conversation_id, original_name, stored_path, mime_type, size_bytes, created_at)
               VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
            (attachment_id, conversation_id, original_name, str(stored_path), mime_type, len(data), now),
        )
    log.info("Saved attachment %s (%d bytes) for conversation %s", safe_name, len(data), conversation_id)
    return Attachment(
        id=attachment_id,
        message_id=None,
        conversation_id=conversation_id,
        original_name=original_name,
        stored_path=str(stored_path),
        mime_type=mime_type,
        size_bytes=len(data),
        created_at=now,
    )


def get_attachment(attachment_id: str) -> Attachment | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    return Attachment.from_row(row) if row else None


def get_attachments(attachment_ids: list[str]) -> list[Attachment]:
    if not attachment_ids:
        return []
    placeholders = ",".join("?" for _ in attachment_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM attachments WHERE id IN ({placeholders})", attachment_ids
        ).fetchall()
    return [Attachment.from_row(r) for r in rows]


def attach_to_message(attachment_ids: list[str], message_id: str) -> None:
    if not attachment_ids:
        return
    with get_connection() as conn:
        conn.executemany(
            "UPDATE attachments SET message_id = ? WHERE id = ?",
            [(message_id, aid) for aid in attachment_ids],
        )


def attachments_for_message(message_id: str) -> list[Attachment]:
    """Files attached to one message.

    Needed by regenerate: the question is re-asked from the stored row, so its
    file references have to be rebuilt from the DB rather than from the payload.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM attachments WHERE message_id = ? ORDER BY created_at ASC", (message_id,)
        ).fetchall()
    return [Attachment.from_row(r) for r in rows]


def delete_attachment(attachment_id: str) -> None:
    att = get_attachment(attachment_id)
    if not att:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    try:
        Path(att.stored_path).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove attachment file %s: %s", att.stored_path, exc)


def prune_orphans() -> int:
    """Removes files on disk with no matching DB row (covers crash-mid-upload cases)."""
    if not ATTACHMENTS_DIR.exists():
        return 0
    with get_connection() as conn:
        known_paths = {
            row["stored_path"] for row in conn.execute("SELECT stored_path FROM attachments").fetchall()
        }

    removed = 0
    for path in ATTACHMENTS_DIR.rglob("*"):
        if path.is_file() and str(path) not in known_paths:
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                log.warning("Could not remove orphaned attachment %s: %s", path, exc)
    if removed:
        log.info("Pruned %d orphaned attachment file(s)", removed)
    return removed
