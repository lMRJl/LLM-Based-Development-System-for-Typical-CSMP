"""Agent execution API — SSE streaming with real AgentPipeline"""

import json
import asyncio
import os
import re
import traceback
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend.models.database import get_session, PipelineStatus, ArtifactType
from backend.services import project_service
from backend.agents import AgentPipeline

router = APIRouter(prefix="/api/agents", tags=["agents"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_db(request: Request):
    return get_session(request.app.state.engine)


def _parse_files(content: str) -> list[tuple[str, str]]:
    """Parse LLM output into (path, content) pairs.

    Supports the current backend format:
        PATH:backend/path/to/file.py
        CODE:
        ...

    Also keeps compatibility with the older ===FILE: path=== format.
    """
    # Normalize line endings and strip outer whitespace
    content = content.replace("\r\n", "\n").strip()

    path_code_pattern = (
        r'^\s*PATH\s*[:：]\s*(.+?)\s*\n\s*CODE\s*[:：]\s*\n?'
        r'(.*?)(?=^\s*PATH\s*[:：]\s*.+?\s*\n\s*CODE\s*[:：]|\Z)'
    )
    path_code_matches = re.findall(path_code_pattern, content, re.DOTALL | re.MULTILINE | re.IGNORECASE)
    if path_code_matches:
        return [(path.strip(), code.strip()) for path, code in path_code_matches]

    pattern = r'===FILE:\s*(.+?)===\s*\n(.*?)===END==='
    matches = re.findall(pattern, content, re.DOTALL)
    if matches:
        return [(path.strip(), code.strip()) for path, code in matches]
    # Fallback: try to find FILE markers with flexible formatting
    pattern2 = r'===FILE:\s*(\S+)\s*===?\s*\n(.+?)(?====FILE:|$)'
    matches2 = re.findall(pattern2, content, re.DOTALL)
    if matches2:
        return [(path.strip(), code.strip()) for path, code in matches2]
    return []


def _files_from_stage_result(stage_result: dict) -> list[tuple[str, str]]:
    """Prefer structured generated files over reparsing artifact text."""
    files = stage_result.get("files")
    if not isinstance(files, list):
        return []

    parsed: list[tuple[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        code = item.get("code", "")
        if path and isinstance(code, str):
            parsed.append((path, code))
    return parsed


def _resolve_output_path(output_dir: str, rel_path: str) -> tuple[str, str]:
    """Resolve a model-provided relative path safely inside the project output dir."""
    normalized = rel_path.replace("\\", "/").lstrip("/")
    normalized = os.path.normpath(normalized)
    if normalized.startswith("..") or os.path.isabs(normalized):
        raise ValueError(f"Unsafe generated file path: {rel_path}")

    abs_path = os.path.abspath(os.path.join(output_dir, normalized))
    output_root = os.path.abspath(output_dir)
    if os.path.commonpath([output_root, abs_path]) != output_root:
        raise ValueError(f"Generated file path escapes project output dir: {rel_path}")
    return abs_path, normalized


def _project_output_dir(project_id: str) -> str:
    """Return the canonical output directory: projects/{project_id first 8 chars}."""
    safe_project_id = re.sub(r"[^A-Za-z0-9_-]", "_", project_id)[:8]
    return str(PROJECT_ROOT / "projects" / safe_project_id)


# Stage → default single-file name (used when no FILE markers found)
STAGE_DEFAULT_FILE = {
    "research": "research_report.md",
    "prd": "PRD.md",
    "design": "design.md",
    "review": "review_report.md",
    "database": "database_design.md",
    "backend": "backend.py",
    "frontend": "frontend.py",
    "deploy": "docker-compose.yml",
}

SSE_TOKEN_CHUNK_SIZE = 50


async def run_pipeline_stream(project_id: str, user_input: str, db):
    """SSE streaming — execute AgentPipeline stage by stage and save each stage immediately."""
    run = project_service.create_pipeline_run(db, project_id)
    project_service.update_project_status(db, project_id, "running")
    run_id = run.id

    stage_type_map = {
        "research": ArtifactType.OTHER,
        "prd": ArtifactType.PRD,
        "design": ArtifactType.DESIGN,
        "database": ArtifactType.DATABASE,
        "backend": ArtifactType.BACKEND_CODE,
        "frontend": ArtifactType.FRONTEND_CODE,
        "review": ArtifactType.REVIEW,
        "deploy": ArtifactType.DEPLOY,
    }

    # Create project output directory
    output_dir = _project_output_dir(project_id)
    os.makedirs(output_dir, exist_ok=True)
    stage_timings = []

    try:
        pipeline = AgentPipeline()
        tech_stack = pipeline.extract_tech_stack(user_input)
        project_service.save_message(db, project_id, "system", json.dumps({"tech_stack": tech_stack}, ensure_ascii=False))
        pipeline_input = pipeline.prepare_pipeline_input(user_input)

        # Emit plan first
        plan = pipeline.orchestrator.run_pipeline_plan(pipeline_input)
        stage_list = [s["stage"] for s in plan.get("stages", [])]
        yield f"data: {json.dumps({'type': 'plan', 'summary': plan.get('summary', ''), 'stages': stage_list}, ensure_ascii=False)}\n\n"

        # Execute and stream stage by stage. This makes generated files visible on disk
        # as soon as their stage completes, even if a later stage fails.
        for stage_result in pipeline.iter_execute(
            pipeline_input,
            input_summarized=True,
            tech_stack=tech_stack,
            plan=plan,
            output_dir=output_dir,
        ):
            stage_name = stage_result["stage"]
            duration_seconds = stage_result.get("duration_seconds")
            if duration_seconds is not None:
                stage_timings.append({
                    "stage": stage_name,
                    "status": stage_result.get("status", "unknown"),
                    "duration_seconds": duration_seconds,
                })
            project_service.update_pipeline_run(db, run_id, current_stage=stage_name)

            yield f"data: {json.dumps({'type': 'stage_start', 'stage': stage_name, 'description': f'Executing {stage_name}...'}, ensure_ascii=False)}\n\n"

            # Per-file streaming for backend/frontend stages
            if stage_result.get("type") == "file_chunk":
                file_path = stage_result.get("path", "")
                file_code = stage_result.get("code", "")
                yield f"data: {json.dumps({'type': 'file_start', 'stage': stage_name, 'path': file_path}, ensure_ascii=False)}\n\n"
                for i in range(0, len(file_code), SSE_TOKEN_CHUNK_SIZE):
                    chunk = file_code[i:i + SSE_TOKEN_CHUNK_SIZE]
                    yield f"data: {json.dumps({'type': 'token', 'stage': stage_name, 'content': chunk, 'path': file_path}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.005)
                yield f"data: {json.dumps({'type': 'file_complete', 'stage': stage_name, 'path': file_path}, ensure_ascii=False)}\n\n"
                continue

            if stage_result.get("status") == "completed":
                content = stage_result.get("content", "")
                rag_ctx = stage_result.get("rag_context", "")

                # File saving: backend writes via tools directly. Other stages need disk writes.
                files = _files_from_stage_result(stage_result) or _parse_files(content)
                saved_paths = []

                if stage_name in ("backend", "frontend"):
                    # Agent uses write_file tool — files already on disk
                    saved_paths = [f.get("path", "") for f in (stage_result.get("files") or [])]
                elif files:
                    print(f"[ArtifactSave] stage={stage_name} output_dir={output_dir} files={len(files)}")
                    for rel_path, file_content in files:
                        abs_path, normalized_path = _resolve_output_path(output_dir, rel_path)
                        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                        with open(abs_path, "w", encoding="utf-8") as f:
                            f.write(file_content)
                        saved_paths.append(normalized_path)
                        print(f"[ArtifactSave] wrote {abs_path}")
                elif stage_name in STAGE_DEFAULT_FILE:
                    # Single-file output (prd/design/database/deploy)
                    fname = STAGE_DEFAULT_FILE[stage_name]
                    abs_path = os.path.join(output_dir, fname)
                    with open(abs_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    saved_paths.append(fname)
                    print(f"[ArtifactSave] wrote {abs_path}")

                    # Database stage: also write SQL file
                    if stage_name == "database":
                        sql_content = stage_result.get("sql_content", "")
                        if sql_content:
                            sql_fname = "schema.sql"
                            sql_path = os.path.join(output_dir, sql_fname)
                            with open(sql_path, "w", encoding="utf-8") as f:
                                f.write(sql_content)
                            saved_paths.append(sql_fname)
                            print(f"[ArtifactSave] wrote {sql_path}")

                file_path = ", ".join(saved_paths) if saved_paths else None

                # Save artifact metadata/content before token streaming as well. The UI and
                # status APIs can then see stage output immediately after generation.
                artifact_type = stage_type_map.get(stage_name, ArtifactType.OTHER)
                project_service.save_artifact(
                    db, project_id, run_id,
                    artifact_type=artifact_type,
                    stage=stage_name,
                    title=stage_result.get("title", stage_name),
                    content=content,
                    rag_context_used=rag_ctx if rag_ctx else None,
                    file_path=file_path,
                )

                # Persist pipeline context for resume support
                if hasattr(pipeline, "_last_ctx"):
                    project_service.save_pipeline_context(
                        db, run_id, stage_name, pipeline._last_ctx
                    )

                yield f"data: {json.dumps({'type': 'artifact_saved', 'stage': stage_name, 'saved_paths': saved_paths, 'output_dir': output_dir, 'duration_seconds': duration_seconds}, ensure_ascii=False)}\n\n"

                # Stream content in small chunks to reduce long-lived connection pressure.
                for i in range(0, len(content), SSE_TOKEN_CHUNK_SIZE):
                    chunk = content[i:i + SSE_TOKEN_CHUNK_SIZE]
                    yield f"data: {json.dumps({'type': 'token', 'stage': stage_name, 'content': chunk}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.005)

                yield f"data: {json.dumps({'type': 'stage_complete', 'stage': stage_name, 'saved_paths': saved_paths, 'output_dir': output_dir, 'duration_seconds': duration_seconds}, ensure_ascii=False)}\n\n"
            else:
                err_msg = stage_result.get("content", "Unknown error")
                yield f"data: {json.dumps({'type': 'stage_error', 'stage': stage_name, 'error': err_msg, 'duration_seconds': duration_seconds}, ensure_ascii=False)}\n\n"

        project_service.update_pipeline_run(db, run_id, status=PipelineStatus.COMPLETED)
        project_service.update_project_status(db, project_id, "completed")
        project_service.save_message(
            db,
            project_id,
            "system",
            json.dumps({"stage_timings": stage_timings}, ensure_ascii=False),
        )
        yield f"data: {json.dumps({'type': 'pipeline_complete', 'run_id': run_id, 'stage_timings': stage_timings}, ensure_ascii=False)}\n\n"

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[Pipeline Error] {tb}")
        project_service.update_pipeline_run(db, run_id, status=PipelineStatus.FAILED)
        project_service.update_project_status(db, project_id, "failed")
        project_service.save_message(
            db,
            project_id,
            "system",
            json.dumps({"stage_timings": stage_timings, "pipeline_error": str(e)}, ensure_ascii=False),
        )
        yield f"data: {json.dumps({'type': 'pipeline_error', 'error': str(e), 'stage_timings': stage_timings}, ensure_ascii=False)}\n\n"


@router.post("/run/{project_id}")
async def run_agent_pipeline(project_id: str, request: Request):
    body = await request.json()
    user_input = body.get("user_input", "")
    db = get_db(request)
    project_service.save_message(db, project_id, "user", user_input)

    return StreamingResponse(
        run_pipeline_stream(project_id, user_input, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status/{project_id}")
def get_pipeline_status(project_id: str, request: Request):
    db = get_db(request)
    from backend.models.database import PipelineRun
    runs = db.query(PipelineRun).filter(
        PipelineRun.project_id == project_id
    ).order_by(PipelineRun.created_at.desc()).all()
    if not runs:
        return {"status": "no_runs"}
    latest = runs[0]
    # Get completed stages from context if available
    completed_stages = []
    ctx = project_service.get_pipeline_context(db, latest.id)
    if ctx:
        completed_stages = ctx.get("completed_stages", [])
    return {
        "run_id": latest.id,
        "status": latest.status.value if latest.status else "unknown",
        "current_stage": latest.current_stage.value if latest.current_stage else None,
        "completed_stages": completed_stages,
    }


@router.get("/runs/{project_id}")
def list_pipeline_runs(project_id: str, request: Request):
    """List all pipeline runs for a project."""
    db = get_db(request)
    from backend.models.database import PipelineRun
    runs = db.query(PipelineRun).filter(
        PipelineRun.project_id == project_id
    ).order_by(PipelineRun.created_at.desc()).limit(10).all()
    return [
        {
            "run_id": r.id,
            "status": r.status.value if r.status else "unknown",
            "current_stage": r.current_stage.value if r.current_stage else None,
            "created_at": str(r.created_at) if r.created_at else None,
            "completed_at": str(r.completed_at) if r.completed_at else None,
        }
        for r in runs
    ]


@router.post("/resume/{project_id}")
async def resume_pipeline(project_id: str, request: Request):
    """Resume an interrupted pipeline from the last completed stage."""
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    from_stage = body.get("from_stage") or request.query_params.get("from_stage")

    db = get_db(request)
    from backend.models.database import PipelineRun, PipelineStatus

    # Find latest interrupted run
    latest = db.query(PipelineRun).filter(
        PipelineRun.project_id == project_id
    ).order_by(PipelineRun.created_at.desc()).first()

    if not latest or latest.status not in (PipelineStatus.INTERRUPTED, PipelineStatus.FAILED):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail="No interrupted or failed pipeline to resume. Start a new run instead."
        )

    return StreamingResponse(
        _resume_pipeline_stream(project_id, latest, from_stage, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _resume_pipeline_stream(project_id: str, run: object, from_stage: str | None, db):
    """Resume an interrupted pipeline, skipping completed stages."""
    import json as _json

    # Load saved context
    saved_ctx = project_service.get_pipeline_context(db, run.id)
    if not saved_ctx:
        yield f"data: {_json.dumps({'type': 'pipeline_error', 'error': 'No saved context found. Cannot resume.'}, ensure_ascii=False)}\n\n"
        return

    pipeline_input = saved_ctx.get("pipeline_input", "")
    tech_stack = saved_ctx.get("tech_stack", {})

    # Determine start stage
    if from_stage:
        start_stage = from_stage
    else:
        plan = saved_ctx.get("plan_stages")
        if not plan:
            start_stage = None
        else:
            start_stage = project_service.get_next_stage(db, run.id, plan)

    # Rebuild plan from saved context or re-extract
    pipeline = AgentPipeline()
    try:
        plan = _json.loads(saved_ctx.get("plan_json", "null")) if saved_ctx.get("plan_json") else None
    except Exception:
        plan = None
    plan = plan or pipeline.orchestrator.run_pipeline_plan(pipeline_input)
    saved_ctx["plan_json"] = _json.dumps(plan, ensure_ascii=False)

    # Create new run (or reopen existing)
    new_run = project_service.create_pipeline_run(db, project_id)
    project_service.update_pipeline_run(db, new_run.id, status=PipelineStatus.RUNNING)

    # Save initial context for resumability of the new run too
    project_service.save_pipeline_context(db, new_run.id, plan["stages"][0]["stage"] if plan.get("stages") else plan["stages"][0], saved_ctx)

    stage_list = [s["stage"] for s in plan.get("stages", [])]
    saved_ctx["plan_stages"] = stage_list

    yield f"data: {_json.dumps({'type': 'plan', 'summary': plan.get('summary', 'Resumed pipeline'), 'stages': stage_list}, ensure_ascii=False)}\n\n"

    from pathlib import Path as _Path
    import os as _os, re as _re
    PROJECT_ROOT = _Path(__file__).resolve().parents[2]
    safe_id = _re.sub(r"[^A-Za-z0-9_-]", "_", project_id)[:8]
    _output_dir = str(PROJECT_ROOT / "projects" / safe_id)
    _os.makedirs(_output_dir, exist_ok=True)

    for stage_result in pipeline.iter_execute(
        pipeline_input,
        input_summarized=True,
        tech_stack=tech_stack,
        plan=plan,
        resume_ctx=saved_ctx,
        start_from_stage=start_stage,
        output_dir=_output_dir,
    ):
        # Per-file streaming for backend/frontend
        if stage_result.get("type") == "file_chunk":
            file_path = stage_result.get("path", "")
            file_code = stage_result.get("code", "")
            stage_name = stage_result.get("stage", "")
            yield f"data: {_json.dumps({'type': 'file_start', 'stage': stage_name, 'path': file_path}, ensure_ascii=False)}\n\n"
            for i in range(0, len(file_code), 50):
                chunk = file_code[i:i + 50]
                yield f"data: {_json.dumps({'type': 'token', 'stage': stage_name, 'content': chunk, 'path': file_path}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type': 'file_complete', 'stage': stage_name, 'path': file_path}, ensure_ascii=False)}\n\n"
            continue

        stage_name = stage_result["stage"]
        project_service.update_pipeline_run(db, new_run.id, current_stage=stage_name)

        if stage_result.get("status") == "completed":
            # If already completed in prior run, don't re-save artifact
            if stage_result.get("duration_seconds") == 0:
                yield f"data: {_json.dumps({'type': 'stage_complete', 'stage': stage_name, 'resumed': True}, ensure_ascii=False)}\n\n"
                continue

            # Save artifact for newly-completed stage
            from backend.models.database import ArtifactType
            stage_type_map = {
                "research": ArtifactType.OTHER, "prd": ArtifactType.PRD,
                "design": ArtifactType.DESIGN, "database": ArtifactType.DATABASE,
                "backend": ArtifactType.BACKEND_CODE, "frontend": ArtifactType.FRONTEND_CODE,
                "review": ArtifactType.REVIEW, "deploy": ArtifactType.DEPLOY,
            }
            artifact_type = stage_type_map.get(stage_name, ArtifactType.OTHER)
            content = stage_result.get("content", "")
            rag = stage_result.get("rag_context", "")

            # Write files to disk (skip backend/frontend — agent already wrote via write_file tool)
            if stage_name not in ("backend", "frontend"):
                from pathlib import Path as _Path
                import os as _os, re as _re
                PROJECT_ROOT = _Path(__file__).resolve().parents[2]
                safe_id = _re.sub(r"[^A-Za-z0-9_-]", "_", project_id)[:8]
                output_dir = str(PROJECT_ROOT / "projects" / safe_id)
                _os.makedirs(output_dir, exist_ok=True)

                STAGE_DEFAULT_FILE = {
                    "research": "research_report.md", "prd": "PRD.md", "design": "design.md",
                    "review": "review_report.md", "database": "database_design.md",
                    "deploy": "docker-compose.yml",
                }
                if stage_name in STAGE_DEFAULT_FILE:
                    fname = STAGE_DEFAULT_FILE[stage_name]
                    with open(_os.path.join(output_dir, fname), "w", encoding="utf-8") as f:
                        f.write(content)
                    if stage_name == "database":
                        sql_c = stage_result.get("sql_content", "")
                        if sql_c:
                            with open(_os.path.join(output_dir, "schema.sql"), "w", encoding="utf-8") as f2:
                                f2.write(sql_c)

                file_path_saved = STAGE_DEFAULT_FILE.get(stage_name)
            else:
                file_path_saved = ", ".join(
                    f.get("path", "") for f in (stage_result.get("files") or [])
                )

            project_service.save_artifact(
                db, project_id, new_run.id,
                artifact_type=artifact_type, stage=stage_name,
                title=stage_result.get("title", stage_name),
                content=content,
                rag_context_used=rag if rag else None,
                file_path=file_path_saved,
            )

            if hasattr(pipeline, "_last_ctx"):
                project_service.save_pipeline_context(db, new_run.id, stage_name, pipeline._last_ctx)

            # Stream content
            for i in range(0, len(content), 50):
                chunk = content[i:i + 50]
                yield f"data: {_json.dumps({'type': 'token', 'stage': stage_name, 'content': chunk}, ensure_ascii=False)}\n\n"

            yield f"data: {_json.dumps({'type': 'stage_complete', 'stage': stage_name, 'resumed': True}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {_json.dumps({'type': 'stage_error', 'stage': stage_name, 'error': stage_result.get('content', 'Unknown error')}, ensure_ascii=False)}\n\n"

    from backend.models.database import PipelineStatus as Ps
    project_service.update_pipeline_run(db, new_run.id, status=Ps.COMPLETED)
    yield f"data: {_json.dumps({'type': 'pipeline_complete', 'run_id': new_run.id, 'resumed': True}, ensure_ascii=False)}\n\n"

