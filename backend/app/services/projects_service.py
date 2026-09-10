"""CRUD for projects — a named container: working dir + system prompt, holding
multiple conversations that share that context.
"""

import datetime
import uuid
from pathlib import Path

from app.db.connection import get_connection
from app.models.project import Project


class InvalidWorkingDirError(ValueError):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _validate_working_dir(path_str: str) -> None:
    p = Path(path_str)
    if not p.is_absolute():
        raise InvalidWorkingDirError(f"working_dir must be an absolute path: {path_str}")
    if not p.exists():
        raise InvalidWorkingDirError(f"working_dir does not exist: {path_str}")
    if not p.is_dir():
        raise InvalidWorkingDirError(f"working_dir is not a directory: {path_str}")


def create_project(name: str, working_dir: str, system_prompt: str | None = None) -> Project:
    _validate_working_dir(working_dir)
    project_id = str(uuid.uuid4())
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO projects (id, name, working_dir, system_prompt, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, name, working_dir, system_prompt, now, now),
        )
    return get_project(project_id)  # type: ignore[return-value]


def get_project(project_id: str) -> Project | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return Project.from_row(row) if row else None


def list_projects() -> list[Project]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    return [Project.from_row(r) for r in rows]


def update_project(
    project_id: str,
    *,
    name: str | None = None,
    working_dir: str | None = None,
    system_prompt: str | None = None,
) -> None:
    if working_dir is not None:
        _validate_working_dir(working_dir)
    fields, params = [], []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if working_dir is not None:
        fields.append("working_dir = ?")
        params.append(working_dir)
    if system_prompt is not None:
        fields.append("system_prompt = ?")
        params.append(system_prompt)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(project_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", params)


def delete_project(project_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
