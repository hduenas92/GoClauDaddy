import sqlite3
from dataclasses import dataclass


@dataclass
class Attachment:
    id: str
    message_id: str | None
    conversation_id: str
    original_name: str
    stored_path: str
    mime_type: str | None
    size_bytes: int
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Attachment":
        return cls(
            id=row["id"],
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            original_name=row["original_name"],
            stored_path=row["stored_path"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
        )
