import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, constr

from app.services import dir_picker
from app.services import projects_service as svc

router = APIRouter(prefix="/api/projects", tags=["projects"])


_NonEmptyName = constr(strip_whitespace=True, min_length=1)


class CreateProjectRequest(BaseModel):
    name: _NonEmptyName
    working_dir: str | None = None
    system_prompt: str | None = None


class UpdateProjectRequest(BaseModel):
    name: _NonEmptyName | None = None
    working_dir: str | None = None
    system_prompt: str | None = None


@router.get("")
def list_projects():
    return svc.list_projects()


@router.post("")
def create_project(body: CreateProjectRequest):
    try:
        return svc.create_project(body.name, body.working_dir, body.system_prompt)
    except svc.InvalidWorkingDirError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{project_id}")
def update_project(project_id: str, body: UpdateProjectRequest):
    if not svc.get_project(project_id):
        raise HTTPException(404, "Project not found")
    try:
        svc.update_project(
            project_id, name=body.name, working_dir=body.working_dir, system_prompt=body.system_prompt
        )
    except svc.InvalidWorkingDirError as exc:
        raise HTTPException(400, str(exc)) from exc
    return svc.get_project(project_id)


@router.delete("/{project_id}")
def delete_project(project_id: str):
    if not svc.get_project(project_id):
        raise HTTPException(404, "Project not found")
    svc.delete_project(project_id)
    return {"ok": True}


@router.post("/browse-directory")
async def browse_directory(initial_dir: str | None = None):
    # 503, not 500: a machine with no display has nothing wrong with it. The
    # generic 500 handler's "check the logs folder" is actively misleading here.
    try:
        path = await asyncio.to_thread(dir_picker.pick_directory, initial_dir)
    except dir_picker.DirPickerUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    # "" means the user cancelled the dialog. It is NOT an error and NOT a
    # request to clear the field; the client is responsible for leaving the
    # existing value alone when it sees one.
    return {"path": path}
