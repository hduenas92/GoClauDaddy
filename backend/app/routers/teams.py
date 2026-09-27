"""Team sessions: group conversations into a named team, track aggregate cost."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_connection
from app.services.cost import compute_cost_usd

router = APIRouter(prefix="/api/teams", tags=["teams"])


class TeamCreate(BaseModel):
    name: str
    conversation_ids: list[str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aggregate_cost(conn, team_id: str) -> float:
    rows = conn.execute(
        """SELECT m.model, m.input_tokens, m.output_tokens,
                  m.cache_read_tokens, m.cache_creation_tokens
           FROM messages m
           JOIN team_members tm ON tm.conversation_id = m.conversation_id
           WHERE tm.team_id = ?""",
        (team_id,),
    ).fetchall()
    return sum(
        compute_cost_usd(
            r["model"] or "",
            r["input_tokens"] or 0,
            r["output_tokens"] or 0,
            r["cache_read_tokens"] or 0,
            r["cache_creation_tokens"] or 0,
        )
        for r in rows
    )


def _serialize_team(conn, row) -> dict:
    members = conn.execute(
        """SELECT tm.conversation_id, tm.role, c.name AS conv_name
           FROM team_members tm
           JOIN conversations c ON c.id = tm.conversation_id
           WHERE tm.team_id = ?""",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "cost_usd": _aggregate_cost(conn, row["id"]),
        "members": [
            {"conversation_id": m["conversation_id"], "role": m["role"], "name": m["conv_name"]}
            for m in members
        ],
    }


@router.post("", status_code=201)
def create_team(body: TeamCreate):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    if not body.conversation_ids:
        raise HTTPException(400, "at least one conversation_id required")

    team_id = str(uuid.uuid4())
    now = _now()
    with get_connection() as conn:
        # Verify all conversations exist
        for cid in body.conversation_ids:
            row = conn.execute("SELECT id FROM conversations WHERE id = ?", (cid,)).fetchone()
            if not row:
                raise HTTPException(404, f"conversation {cid} not found")

        conn.execute(
            "INSERT INTO team_sessions (id, name, created_at) VALUES (?, ?, ?)",
            (team_id, body.name.strip(), now),
        )
        for cid in body.conversation_ids:
            conn.execute(
                "INSERT OR IGNORE INTO team_members (team_id, conversation_id) VALUES (?, ?)",
                (team_id, cid),
            )
        row = conn.execute("SELECT * FROM team_sessions WHERE id = ?", (team_id,)).fetchone()
        return _serialize_team(conn, row)


@router.get("")
def list_teams():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM team_sessions ORDER BY created_at DESC"
        ).fetchall()
        return [_serialize_team(conn, r) for r in rows]


@router.get("/{team_id}")
def get_team(team_id: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM team_sessions WHERE id = ?", (team_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "team not found")
        return _serialize_team(conn, row)


@router.delete("/{team_id}", status_code=204)
def delete_team(team_id: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM team_sessions WHERE id = ?", (team_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "team not found")
        conn.execute("DELETE FROM team_sessions WHERE id = ?", (team_id,))
