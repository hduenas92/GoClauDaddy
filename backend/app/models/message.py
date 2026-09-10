import sqlite3
from dataclasses import dataclass


@dataclass
class Message:
    id: str
    conversation_id: str
    role: str
    content: str
    thinking: str | None
    input_tokens: int | None
    output_tokens: int | None
    seq: int
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Message":
        return cls(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            thinking=row["thinking"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            seq=row["seq"],
            created_at=row["created_at"],
        )
