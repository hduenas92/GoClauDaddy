from fastapi import APIRouter

from app.db.connection import get_connection
from app.services.process_registry import registry

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/status")
def get_agent_status():
    agents = registry.active_agents()
    if not agents:
        return []

    ids = [a["conversation_id"] for a in agents]
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, name FROM conversations WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
    names = {r["id"]: r["name"] for r in rows}

    return [
        {
            "conversation_id": a["conversation_id"],
            "name": names.get(a["conversation_id"], a["conversation_id"][:8]),
            "elapsed_s": a["elapsed_s"],
            "status": "running",
        }
        for a in agents
    ]
