from fastapi import APIRouter, Request, HTTPException
from backend.models.database import get_session
from backend.schemas.api import ProjectCreate, ProjectResponse
from backend.services import project_service

router = APIRouter(prefix="/api/projects", tags=["projects"])


def get_db(request: Request):
    return get_session(request.app.state.engine)


@router.post("", response_model=ProjectResponse)
def create_project(data: ProjectCreate, request: Request):
    db = get_db(request)
    return project_service.create_project(db, data)


@router.get("", response_model=list[ProjectResponse])
def list_projects(request: Request):
    db = get_db(request)
    return project_service.list_projects(db)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, request: Request):
    db = get_db(request)
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(project_id: str, data: ProjectCreate, request: Request):
    db = get_db(request)
    project = project_service.update_project(db, project_id, name=data.name, description=data.description)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}")
def delete_project(project_id: str, request: Request):
    db = get_db(request)
    ok = project_service.delete_project(db, project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "ok", "message": "Project and all associated data deleted"}
