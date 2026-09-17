import sqlite3
from dataclasses import dataclass


@dataclass
class Conversation:
    id: str
    project_id: str | None
    name: str
    session_id: str | None
    model: str
    permission_mode: str | None
    system_prompt: str | None
    thinking_budget: int | None
    max_tokens: int | None
    status: str
    source: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Conversation":
        keys = row.keys()
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            session_id=row["session_id"],
            model=row["model"],
            permission_mode=row["permission_mode"],
            system_prompt=row["system_prompt"],
            thinking_budget=row["thinking_budget"] if "thinking_budget" in keys else None,
            max_tokens=row["max_tokens"] if "max_tokens" in keys else None,
            status=row["status"],
            source=row["source"] if "source" in keys else "web",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"] if "started_at" in keys else None,
            completed_at=row["completed_at"] if "completed_at" in keys else None,
        )
