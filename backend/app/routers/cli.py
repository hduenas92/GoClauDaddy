"""Right-column v2: latest CLI init snapshot and in-flight agent turns."""

from fastapi import APIRouter

from app.services import cli_info
from app.services import conversations_service
from app.services.process_registry import registry

router = APIRouter(prefix="/api", tags=["cli"])


@router.get("/cli/info")
def get_cli_info():
    return cli_info.get_snapshot()


@router.get("/agents/status")
async def get_agents_status():
    turns = []
    for turn in registry.list_turns():
        conversation = conversations_service.get_conversation(turn["conversation_id"])
        turns.append({
            "conversation_id": turn["conversation_id"],
            "name": conversation.name if conversation else "",
            "elapsed_s": turn["elapsed_s"],
            "status": "running",
        })
    return turns
