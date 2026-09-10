from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.config import PERMISSION_MODES
from app.services import conversations_service as svc

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

    @field_validator("permission_mode")
    @classmethod
    def _validate_permission_mode(cls, v):
        if v is not None and v not in PERMISSION_MODES:
            raise ValueError(f"permission_mode must be one of {PERMISSION_MODES}")
        return v


@router.get("")
def list_conversations(project_id: str | None = None):
    return svc.list_conversations(project_id=project_id)


@router.post("")
def create_conversation(body: CreateConversationRequest):
    kwargs = {"name": body.name, "project_id": body.project_id}
    if body.model:
        kwargs["model"] = body.model
    return svc.create_conversation(**kwargs)


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str):
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    messages = svc.list_messages(conversation_id)
    return {"conversation": conv, "messages": messages}


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
    svc.update_conversation_settings(conversation_id, model=body.model, permission_mode=body.permission_mode)
    return svc.get_conversation(conversation_id)


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str):
    if not svc.get_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found")
    svc.delete_conversation(conversation_id)
    return {"ok": True}
