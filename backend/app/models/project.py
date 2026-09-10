import sqlite3
from dataclasses import dataclass


@dataclass
class Project:
    id: str
    name: str
    working_dir: str
    system_prompt: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Project":
        return cls(
            id=row["id"],
            name=row["name"],
            working_dir=row["working_dir"],
            system_prompt=row["system_prompt"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
