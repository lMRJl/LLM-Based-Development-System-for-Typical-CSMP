import datetime
from sqlalchemy import create_engine, Column, String, Text, DateTime, Integer, Float, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, relationship, Session
import enum


class Base(DeclarativeBase):
    pass


# --- Enums ---
class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class StageName(str, enum.Enum):
    ORCHESTRATOR = "orchestrator"
    RESEARCH = "research"
    PRD = "prd"
    DESIGN = "design"
    DATABASE = "database"
    BACKEND = "backend"
    FRONTEND = "frontend"
    REVIEW = "review"
    DEPLOY = "deploy"


class ArtifactType(str, enum.Enum):
    PRD = "prd"
    DESIGN = "design"
    DATABASE = "database"
    BACKEND_CODE = "backend_code"
    FRONTEND_CODE = "frontend_code"
    REVIEW = "review"
    DEPLOY = "deploy"
    OTHER = "other"


class KBType(str, enum.Enum):
    COMPLIANCE = "compliance"
    PATTERN = "pattern"
    TEMPLATE = "template"
    SCHEMA = "schema"
    DEPLOY = "deploy"
    PROJECT = "project"


# --- Models ---
class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    status = Column(SAEnum(ProjectStatus), default=ProjectStatus.DRAFT)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    pipeline_runs = relationship("PipelineRun", back_populates="project", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="project", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="project", cascade="all, delete-orphan")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    status = Column(SAEnum(PipelineStatus), default=PipelineStatus.PENDING)
    current_stage = Column(SAEnum(StageName), nullable=True)
    context_json = Column(Text, nullable=True)   # serialized ctx for resume
    start_stage = Column(String, nullable=True)  # stage to start/resume from
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="pipeline_runs")


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    pipeline_run_id = Column(String, ForeignKey("pipeline_runs.id"), nullable=True)
    type = Column(SAEnum(ArtifactType), nullable=False)
    stage = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, default="")
    file_path = Column(String, nullable=True)
    version = Column(Integer, default=1)
    rag_context_used = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    project = relationship("Project", back_populates="artifacts")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    role = Column(String, nullable=False)  # user / assistant / system
    content = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    project = relationship("Project", back_populates="messages")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    type = Column(SAEnum(KBType), nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    documents = relationship("Document", back_populates="knowledge_base", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True)
    kb_id = Column(String, ForeignKey("knowledge_bases.id"), nullable=False)
    title = Column(String, nullable=False)
    source = Column(String, default="")
    format = Column(String, default="md")
    raw_content = Column(Text, default="")
    chunk_count = Column(Integer, default=0)
    indexed_at = Column(DateTime, nullable=True)

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(String, primary_key=True)
    doc_id = Column(String, ForeignKey("documents.id"), nullable=False)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, default=0)
    context_prefix = Column(Text, default="")
    metadata_json = Column(Text, default="{}")
    chroma_id = Column(String, nullable=True)

    document = relationship("Document", back_populates="chunks")


# --- Engine & Session ---
def init_db(database_url: str = "sqlite:///./agent_platform.db"):
    engine = create_engine(database_url, connect_args={"check_same_thread": False} if "sqlite" in database_url else {})
    Base.metadata.create_all(engine)
    return engine


def get_session(engine) -> Session:
    return Session(engine)
