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

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "original_name": self.original_name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
        }
