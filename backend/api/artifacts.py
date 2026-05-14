from fastapi import APIRouter, Request
from backend.models.database import get_session
from backend.services import project_service

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


def get_db(request: Request):
    return get_session(request.app.state.engine)


@router.get("/project/{project_id}")
def get_project_artifacts(project_id: str, request: Request):
    db = get_db(request)
    return project_service.get_project_artifacts(db, project_id)


@router.get("/project/{project_id}/messages")
def get_project_messages(project_id: str, request: Request):
    db = get_db(request)
    return project_service.get_project_messages(db, project_id)
