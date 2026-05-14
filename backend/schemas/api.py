from pydantic import BaseModel, Field
from typing import Optional
import datetime


# --- Project ---
class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime

    class Config:
        from_attributes = True


# --- Pipeline ---
class PipelineRunRequest(BaseModel):
    project_id: str
    user_input: str


class PipelineStatusResponse(BaseModel):
    run_id: str
    project_id: str
    status: str
    current_stage: Optional[str]
    created_at: datetime.datetime
    completed_at: Optional[datetime.datetime]

    class Config:
        from_attributes = True


# --- Artifact ---
class ArtifactResponse(BaseModel):
    id: str
    project_id: str
    type: str
    stage: str
    title: str
    content: str
    version: int
    rag_context_used: Optional[str]
    created_at: datetime.datetime

    class Config:
        from_attributes = True


# --- Message ---
class MessageResponse(BaseModel):
    id: str
    project_id: str
    role: str
    content: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True


# --- Knowledge ---
class KnowledgeSearchRequest(BaseModel):
    query: str
    kb_types: Optional[list[str]] = None
    top_k: int = 5


class KnowledgeSearchResult(BaseModel):
    content: str
    doc_title: str
    kb_name: str
    score: float
    metadata: dict = {}


class KnowledgeSearchResponse(BaseModel):
    results: list[KnowledgeSearchResult]
    query_variants: list[str] = []


class DocumentUploadRequest(BaseModel):
    kb_name: str
    kb_type: str
    title: str
    content: str
    format: str = "md"


class KnowledgeStatsResponse(BaseModel):
    kb_count: int
    doc_count: int
    chunk_count: int
    by_type: dict
