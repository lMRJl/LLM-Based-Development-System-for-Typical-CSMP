import uuid
import datetime
from sqlalchemy.orm import Session

from backend.models.database import (
    Project, PipelineRun, Artifact, Message,
    ProjectStatus, PipelineStatus, ArtifactType,
)
from backend.schemas.api import ProjectCreate


def create_project(db: Session, data: ProjectCreate) -> Project:
    project = Project(
        id=str(uuid.uuid4()),
        name=data.name,
        description=data.description,
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def get_project(db: Session, project_id: str) -> Project | None:
    return db.query(Project).filter(Project.id == project_id).first()


def list_projects(db: Session) -> list[Project]:
    return db.query(Project).order_by(Project.updated_at.desc()).all()


def update_project_status(db: Session, project_id: str, status: ProjectStatus) -> Project | None:
    project = get_project(db, project_id)
    if project:
        project.status = status
        project.updated_at = datetime.datetime.utcnow()
        db.commit()
        db.refresh(project)
    return project


def update_project(db: Session, project_id: str, name: str | None = None, description: str | None = None) -> Project | None:
    project = get_project(db, project_id)
    if project:
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        project.updated_at = datetime.datetime.utcnow()
        db.commit()
        db.refresh(project)
    return project


def delete_project(db: Session, project_id: str) -> bool:
    project = get_project(db, project_id)
    if not project:
        return False
    db.delete(project)  # cascade deletes PipelineRun, Artifact, Message
    db.commit()
    return True


def create_pipeline_run(db: Session, project_id: str) -> PipelineRun:
    run = PipelineRun(
        id=str(uuid.uuid4()),
        project_id=project_id,
        status=PipelineStatus.PENDING,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_pipeline_run(
    db: Session,
    run_id: str,
    status: PipelineStatus | None = None,
    current_stage: str | None = None,
    context_json: str | None = None,
    start_stage: str | None = None,
) -> PipelineRun | None:
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if run:
        if status:
            run.status = status
        if current_stage:
            run.current_stage = current_stage
        if context_json is not None:
            run.context_json = context_json
        if start_stage is not None:
            run.start_stage = start_stage
        if status in (PipelineStatus.COMPLETED, PipelineStatus.FAILED, PipelineStatus.INTERRUPTED):
            run.completed_at = datetime.datetime.utcnow()
        db.commit()
        db.refresh(run)
    return run


def save_pipeline_context(db: Session, run_id: str, current_stage: str, ctx: dict) -> PipelineRun | None:
    """Serialize and persist pipeline context after each stage for resume support."""
    import json

    # Extract serializable subset of ctx
    serializable = {
        "pipeline_input": ctx.get("pipeline_input", ""),
        "tech_stack": ctx.get("tech_stack", {}),
        "generated_files": _serialize_generated_files(ctx.get("generated_files", {})),
        "completed_stages": list(ctx.get("_completed_stages", [])),
        "prd": ctx.get("prd", ""),
        "design": ctx.get("design", ""),
        "database": ctx.get("database", ""),
        "backend": ctx.get("backend", ""),
        "frontend": ctx.get("frontend", ""),
        "backend_api_list": ctx.get("backend_api_list", ""),
        "validations": ctx.get("validations", {}),
    }
    return update_pipeline_run(
        db, run_id,
        current_stage=current_stage,
        context_json=json.dumps(serializable, ensure_ascii=False),
    )


def _serialize_generated_files(generated_files: dict) -> dict:
    """Extract path+code from generated files to make them JSON-serializable."""
    result = {}
    for module, files in generated_files.items():
        result[module] = [
            {"path": f.get("path", ""), "code": f.get("code", "")}
            for f in files if isinstance(f, dict)
        ]
    return result


def get_pipeline_context(db: Session, run_id: str) -> dict | None:
    """Retrieve and deserialize saved pipeline context."""
    import json
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run or not run.context_json:
        return None
    try:
        return json.loads(run.context_json)
    except json.JSONDecodeError:
        return None


def get_next_stage(db: Session, run_id: str, plan_stages: list[str]) -> str | None:
    """Determine the next incomplete stage from saved context."""
    ctx = get_pipeline_context(db, run_id)
    if not ctx:
        return plan_stages[0] if plan_stages else None
    completed = set(ctx.get("completed_stages", []))
    for stage in plan_stages:
        if stage not in completed:
            return stage
    return None  # All stages completed


def cleanup_stale_runs(db: Session, stale_minutes: int = 5) -> int:
    """Mark PipelineRuns stuck in 'running' state as 'interrupted'."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=stale_minutes)
    runs = db.query(PipelineRun).filter(
        PipelineRun.status == PipelineStatus.RUNNING,
        PipelineRun.created_at < cutoff,
    ).all()
    for run in runs:
        run.status = PipelineStatus.INTERRUPTED
        run.completed_at = datetime.datetime.utcnow()
    if runs:
        db.commit()
    return len(runs)


def save_artifact(
    db: Session,
    project_id: str,
    pipeline_run_id: str,
    artifact_type: ArtifactType,
    stage: str,
    title: str,
    content: str,
    rag_context_used: str | None = None,
    file_path: str | None = None,
) -> Artifact:
    artifact = Artifact(
        id=str(uuid.uuid4()),
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        type=artifact_type,
        stage=stage,
        title=title,
        content=content,
        rag_context_used=rag_context_used,
        file_path=file_path,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def save_message(db: Session, project_id: str, role: str, content: str) -> Message:
    msg = Message(
        id=str(uuid.uuid4()),
        project_id=project_id,
        role=role,
        content=content,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_project_artifacts(db: Session, project_id: str) -> list[Artifact]:
    return (
        db.query(Artifact)
        .filter(Artifact.project_id == project_id)
        .order_by(Artifact.created_at.desc())
        .all()
    )


def get_project_messages(db: Session, project_id: str) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.project_id == project_id)
        .order_by(Message.created_at.asc())
        .all()
    )
