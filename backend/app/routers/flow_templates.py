import datetime
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_connection

router = APIRouter(prefix="/api/flow-templates", tags=["flow-templates"])


class TemplateBody(BaseModel):
    title: str
    description: str | None = None
    body: str
    category: str


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@router.get("")
def list_templates():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM flow_templates ORDER BY is_builtin DESC, sort_order, title"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("")
def create_template(body: TemplateBody):
    tid = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO flow_templates (id, title, description, body, category, is_builtin, sort_order, created_at)
               VALUES (?, ?, ?, ?, ?, 0, 0, ?)""",
            (tid, body.title, body.description, body.body, body.category, _now()),
        )
    return {"id": tid, "title": body.title, "description": body.description,
            "body": body.body, "category": body.category, "is_builtin": 0, "sort_order": 0}


@router.put("/{template_id}")
def update_template(template_id: str, body: TemplateBody):
    with get_connection() as conn:
        row = conn.execute("SELECT is_builtin FROM flow_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Template not found")
        if row["is_builtin"]:
            raise HTTPException(403, "Cannot modify built-in templates")
        conn.execute(
            "UPDATE flow_templates SET title = ?, description = ?, body = ?, category = ? WHERE id = ?",
            (body.title, body.description, body.body, body.category, template_id),
        )
    return {"id": template_id, **body.model_dump()}


@router.delete("/{template_id}")
def delete_template(template_id: str):
    with get_connection() as conn:
        row = conn.execute("SELECT is_builtin FROM flow_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Template not found")
        if row["is_builtin"]:
            raise HTTPException(403, "Cannot delete built-in templates")
        conn.execute("DELETE FROM flow_templates WHERE id = ?", (template_id,))
    return {"ok": True}
