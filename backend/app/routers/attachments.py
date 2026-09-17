from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services import attachments_service as svc

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.post("")
async def upload_attachment(conversation_id: str, file: UploadFile = File(...)):
    data = await file.read()
    try:
        att = svc.save_attachment(conversation_id, file.filename or "file", data, file.content_type)
    except svc.ConversationNotFound:
        raise HTTPException(404, "Conversation not found")
    except svc.AttachmentRejected as exc:
        raise HTTPException(400, str(exc)) from exc
    return att


@router.get("/{attachment_id}/download")
def download_attachment(attachment_id: str):
    att = svc.get_attachment(attachment_id)
    if not att:
        raise HTTPException(404, "Attachment not found")
    return FileResponse(att.stored_path, filename=att.original_name)


@router.delete("/{attachment_id}")
def delete_attachment(attachment_id: str):
    if not svc.get_attachment(attachment_id):
        raise HTTPException(404, "Attachment not found")
    svc.delete_attachment(attachment_id)
    return {"ok": True}
