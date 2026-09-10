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
    status: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Conversation":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            session_id=row["session_id"],
            model=row["model"],
            permission_mode=row["permission_mode"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
