from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.models.database import init_db, get_session
from backend.api.projects import router as projects_router
from backend.api.agents import router as agents_router
from backend.api.artifacts import router as artifacts_router
from backend.api.knowledge import router as knowledge_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = init_db(settings.database_url)
    app.state.engine = engine
    app.state.settings = settings

    from backend.rag.knowledge_base import KnowledgeBaseManager
    from backend.models.database import get_session
    db = get_session(engine)
    manager = KnowledgeBaseManager(db)
    if not manager.list_kbs():
        print("[STARTUP] Knowledge base empty — running seed...")
        from backend.seed import seed_all
        seed_all(force=False)
    db.close()

    db2 = get_session(engine)
    try:
        from backend.services.project_service import cleanup_stale_runs
        count = cleanup_stale_runs(db2)
        if count:
            print(f"[STARTUP] Marked {count} stale pipeline runs as interrupted")
    finally:
        db2.close()

    yield


app = FastAPI(title="Agent Platform", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router)
app.include_router(agents_router)
app.include_router(artifacts_router)
app.include_router(knowledge_router)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/system/mock-stats")
def mock_stats():
    import os
    if os.environ.get("MOCK_LLM", "").lower() not in ("true", "1", "yes"):
        return {"mock_active": False}
    from backend.core.mock_llm import get_mock_llm
    mock = get_mock_llm()
    return {"mock_active": True, **mock.get_stats()}
