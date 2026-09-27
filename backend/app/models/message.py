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
    model: str | None
    cache_read_tokens: int
    cache_creation_tokens: int
    seq: int
    created_at: str
    tool_calls: str | None = None
    # v15. Read defensively: the column has existed since v15 but this model did
    # not read it until Phase 3, and from_row is handed rows from queries that do
    # not all select *.
    stopped: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Message":
        keys = row.keys()
        return cls(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            thinking=row["thinking"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            model=row["model"],
            cache_read_tokens=row["cache_read_tokens"] or 0,
            cache_creation_tokens=row["cache_creation_tokens"] or 0,
            seq=row["seq"],
            created_at=row["created_at"],
            tool_calls=row["tool_calls"] if "tool_calls" in keys else None,
            stopped=bool(row["stopped"]) if "stopped" in keys else False,
        )
