import dataclasses

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.config import PERMISSION_MODES
from app.db.connection import get_connection
from app.services import conversations_service as svc
from app.services.cost import compute_cost_usd

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    name: str | None = None
    project_id: str | None = None
    model: str | None = None


class RenameConversationRequest(BaseModel):
    name: str


class UpdateConversationSettingsRequest(BaseModel):
    model: str | None = None
    permission_mode: str | None = None
    system_prompt: str | None = None
    thinking_budget: int | None = None
    max_tokens: int | None = None
    clear_system_prompt: bool = False
    clear_thinking_budget: bool = False
    clear_max_tokens: bool = False

    @field_validator("permission_mode")
    @classmethod
    def _validate_permission_mode(cls, v):
        if v is not None and v not in PERMISSION_MODES:
            raise ValueError(f"permission_mode must be one of {PERMISSION_MODES}")
        return v


@router.get("")
def list_conversations(project_id: str | None = None):
    convs = svc.list_conversations(project_id=project_id)
    if not convs:
        return []
    ids = [c.id for c in convs]
    with get_connection() as conn:
        rows = conn.execute(
            # Grouped by (conversation, MESSAGE model), not by conversation
            # alone. Pricing a whole conversation at `c.model` reprices every
            # historical message whenever the user switches model mid-thread —
            # Haiku to Opus moved a $6.00 total to $30.00 with no tokens spent.
            # COALESCE keeps rows written before migration 4 added
            # messages.model priced at the conversation's model.
            f"""SELECT m.conversation_id,
                COALESCE(m.model, c.model) AS model,
                COALESCE(SUM(m.input_tokens), 0) AS ti,
                COALESCE(SUM(m.output_tokens), 0) AS tot_out,
                COALESCE(SUM(m.cache_read_tokens), 0) AS tcr,
                COALESCE(SUM(m.cache_creation_tokens), 0) AS tcc
                FROM messages m JOIN conversations c ON c.id = m.conversation_id
                WHERE m.role = 'assistant'
                  AND m.conversation_id IN ({','.join('?' * len(ids))})
                GROUP BY m.conversation_id, COALESCE(m.model, c.model)""",
            ids,
        ).fetchall()
    cost_map: dict[str, float] = {}
    for r in rows:
        cost_map[r["conversation_id"]] = cost_map.get(r["conversation_id"], 0.0) + compute_cost_usd(
            r["model"], r["ti"], r["tot_out"], r["tcr"], r["tcc"]
        )
    return [
        {**dataclasses.asdict(c), "cost_usd": cost_map.get(c.id, 0.0)}
        for c in convs
    ]


@router.post("")
def create_conversation(body: CreateConversationRequest):
    kwargs = {"name": body.name, "project_id": body.project_id}
    if body.model:
        kwargs["model"] = body.model
    return svc.create_conversation(**kwargs)


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str):
    conv, invalid_model = svc.get_conversation_healed(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    messages = svc.list_messages(conversation_id)
    result = {"conversation": conv, "messages": messages, "model_correction": None}
    if invalid_model is not None:
        result["model_correction"] = {"invalid_model": invalid_model, "corrected_to": conv.model}
    return result


@router.patch("/{conversation_id}/rename")
def rename_conversation(conversation_id: str, body: RenameConversationRequest):
    if not svc.get_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found")
    svc.rename_conversation(conversation_id, body.name)
    return svc.get_conversation(conversation_id)


@router.patch("/{conversation_id}/settings")
def update_settings(conversation_id: str, body: UpdateConversationSettingsRequest):
    if not svc.get_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found")
    svc.update_conversation_settings(
        conversation_id,
        model=body.model,
        permission_mode=body.permission_mode,
        system_prompt=body.system_prompt,
        thinking_budget=body.thinking_budget,
        max_tokens=body.max_tokens,
        clear_system_prompt=body.clear_system_prompt,
        clear_thinking_budget=body.clear_thinking_budget,
        clear_max_tokens=body.clear_max_tokens,
    )
    return svc.get_conversation(conversation_id)


@router.post("/{conversation_id}/auto-title")
def auto_title(conversation_id: str):
    if not svc.get_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found")
    svc.auto_title_conversation(conversation_id)
    return svc.get_conversation(conversation_id)


@router.get("/{conversation_id}/export")
def export_conversation(conversation_id: str):
    if not svc.get_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found")
    return {"markdown": svc.export_as_markdown(conversation_id)}


@router.delete("/{conversation_id}/messages/last")
def delete_last_message(conversation_id: str):
    if not svc.get_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found")
    deleted = svc.delete_last_message(conversation_id)
    return {"ok": deleted}


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str):
    if not svc.get_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found")
    svc.delete_conversation(conversation_id)
    return {"ok": True}
