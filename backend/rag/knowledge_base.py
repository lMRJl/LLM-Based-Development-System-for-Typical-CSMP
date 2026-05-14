import uuid
from sqlalchemy.orm import Session

from backend.models.database import KnowledgeBase, Document, Chunk, KBType
from backend.rag.vector_store import get_vector_store


class KnowledgeBaseManager:
    """知识库管理 — CRUD + 种子数据注入"""

    def __init__(self, db: Session):
        self.db = db
        self.vector_store = get_vector_store()

    def create_kb(self, name: str, kb_type: str, description: str = "") -> KnowledgeBase:
        kb = KnowledgeBase(
            id=str(uuid.uuid4()),
            name=name,
            type=KBType(kb_type) if kb_type in [e.value for e in KBType] else KBType.TEMPLATE,
            description=description,
        )
        self.db.add(kb)
        self.db.commit()
        self.db.refresh(kb)
        return kb

    def get_kb(self, name: str) -> KnowledgeBase | None:
        return self.db.query(KnowledgeBase).filter(KnowledgeBase.name == name).first()

    def list_kbs(self) -> list[KnowledgeBase]:
        return self.db.query(KnowledgeBase).all()

    def delete_kb(self, name: str) -> bool:
        kb = self.get_kb(name)
        if not kb:
            return False
        self.vector_store.delete_collection(f"kb_{name}")
        self.db.delete(kb)
        self.db.commit()
        return True

    def get_stats(self) -> dict:
        kbs = self.list_kbs()
        doc_count = self.db.query(Document).count()
        chunk_count = self.db.query(Chunk).count()
        by_type = {}
        for kb in kbs:
            t = kb.type.value if kb.type else "unknown"
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "kb_count": len(kbs),
            "doc_count": doc_count,
            "chunk_count": chunk_count,
            "by_type": by_type,
        }

    def seed_defaults(self):
        """Inject seed knowledge bases if empty."""
        if self.list_kbs():
            return  # Already has data

        from backend.rag.indexer import DocumentIndexer
        indexer = DocumentIndexer(self.db)

        # Seed compliance KB
        kb_compliance = self.create_kb(
            "security_compliance",
            "compliance",
            "网络安全合规知识库 — 等保2.0 / OWASP / 法律法规",
        )

        # Seed design patterns KB
        kb_design = self.create_kb(
            "design_patterns",
            "pattern",
            "安全架构设计模式 — 零信任 / API网关 / 微服务安全",
        )

        # Seed code templates KB
        kb_code = self.create_kb(
            "code_templates",
            "template",
            "代码模板库 — FastAPI安全中间件 / React安全组件",
        )

        # Seed DB schemas KB
        kb_db = self.create_kb(
            "db_schemas",
            "schema",
            "数据库模板 — RBAC / 审计日志 / 告警",
        )

        # Seed deployment KB
        kb_deploy = self.create_kb(
            "deployment",
            "deploy",
            "部署模板 — Docker / K8s / CI/CD",
        )

        self.db.commit()
        return [kb_compliance, kb_design, kb_code, kb_db, kb_deploy]
