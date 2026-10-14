"""REST endpoints for the WP-AP rule store and decision log (J3)."""

from fastapi import APIRouter, HTTPException, Query

import app.services.permissions as permissions

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


@router.get("/rules")
def get_rules():
    return [
        {
            "id": rule.id,
            "tool_name": rule.tool_name,
            "pattern": rule.pattern,
            "cwd": rule.cwd,
            "created_at": rule.created_at,
        }
        for rule in permissions.list_rules()
    ]


@router.delete("/rules/{id}")
def delete_rule(id: str):
    if not permissions.revoke_rule(id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"revoked": True}


@router.get("/log")
def get_log(limit: int = Query(50, ge=1, le=500)):
    return permissions.list_log(limit)
