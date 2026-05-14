"""种子知识库索引脚本 — 将 knowledge_bases/ 目录下的所有 .md 文件索引到 ChromaDB"""

import sys
import os

# Ensure backend is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import get_settings
from backend.models.database import init_db, get_session
from backend.rag.knowledge_base import KnowledgeBaseManager
from backend.rag.indexer import DocumentIndexer
from backend.models.database import KnowledgeBase

# 种子目录 → KB 类型映射
SEED_MAPPING = {
    "security_compliance": "compliance",
    "design_patterns": "pattern",
    "code_templates": "template",
    "prd_templates": "template",
    "db_templates": "template",
    "db_schemas": "schema",
    "deployment": "deploy",
}


def reset_seed_data(manager: KnowledgeBaseManager, db) -> None:
    """Remove SQL KB records and Chroma kb_* collections before a force reindex."""
    try:
        collections = manager.vector_store.client.list_collections()
        collection_names = [c.name if hasattr(c, "name") else str(c) for c in collections]
    except Exception as e:
        print(f"[WARN] Unable to list Chroma collections before reset: {e}")
        collection_names = []

    for name in collection_names:
        if not name.startswith("kb_"):
            continue
        try:
            manager.vector_store.delete_collection(name)
            print(f"  Deleted Chroma collection: {name}")
        except Exception as e:
            print(f"  [WARN] Failed to delete Chroma collection {name}: {e}")

    kb_count = db.query(KnowledgeBase).count()
    if kb_count:
        for kb in db.query(KnowledgeBase).all():
            db.delete(kb)
        db.commit()
        print(f"  Deleted SQL knowledge bases: {kb_count}")


def delete_seed_kb(manager: KnowledgeBaseManager, db, kb_name: str) -> None:
    """Delete one seeded KB from both Chroma and SQL, tolerating partial corruption."""
    collection_name = f"kb_{kb_name}"
    try:
        manager.vector_store.delete_collection(collection_name)
        print(f"  Deleted Chroma collection: {collection_name}")
    except Exception as e:
        print(f"  [WARN] Failed to delete Chroma collection {collection_name}: {e}")

    kb = manager.get_kb(kb_name)
    if kb:
        db.delete(kb)
        db.commit()
        print(f"  Deleted SQL knowledge base: {kb_name}")


def validate_seed_collection(manager: KnowledgeBaseManager, kb_name: str) -> int:
    """Run a query that forces Chroma to open the HNSW segment reader."""
    collection_name = f"kb_{kb_name}"
    count = manager.vector_store.count(collection_name)
    if count <= 0:
        raise RuntimeError(f"{collection_name} contains no indexed chunks")

    manager.vector_store.query(
        collection_name=collection_name,
        query_text="知识库索引健康检查",
        top_k=1,
    )
    return count


def index_directory_with_repair(
    indexer: DocumentIndexer,
    manager: KnowledgeBaseManager,
    db,
    folder_path: str,
    folder_name: str,
    kb_type: str,
    max_attempts: int = 2,
) -> list:
    """Index one directory; if Chroma indexing/querying fails, rebuild that collection once."""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(f"  [REPAIR] Rebuilding {folder_name} after index validation failure...")
            delete_seed_kb(manager, db, folder_name)

        try:
            docs = indexer.index_directory(
                directory=folder_path,
                kb_name=folder_name,
                kb_type=kb_type,
            )
            chunk_count = validate_seed_collection(manager, folder_name)
            print(f"  [OK] {folder_name}: {len(docs)} documents, {chunk_count} chunks")
            return docs
        except Exception as e:
            last_error = e
            print(f"  [ERROR] {folder_name} indexing/check failed on attempt {attempt}: {e}")
            db.rollback()

    delete_seed_kb(manager, db, folder_name)
    raise RuntimeError(f"Failed to index {folder_name} after {max_attempts} attempts") from last_error


def seed_all(force: bool = False):
    """Index all seed knowledge base files into ChromaDB."""
    settings = get_settings()
    engine = init_db(settings.database_url)
    db = get_session(engine)

    manager = KnowledgeBaseManager(db)

    if not force and manager.list_kbs():
        print("[SKIP] Knowledge bases already exist. Use --force to reindex.")
        db.close()
        return
    if force:
        print("[RESET] Force reindex requested. Clearing existing KB records and Chroma collections...")
        reset_seed_data(manager, db)

    indexer = DocumentIndexer(db)

    # Find knowledge_bases root directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kb_root = os.path.join(base_dir, "knowledge_bases")
    if not os.path.isdir(kb_root):
        kb_root = os.path.join(os.getcwd(), "knowledge_bases")

    print(f"Seeding from: {kb_root}")
    total = 0

    for folder_name, kb_type in SEED_MAPPING.items():
        folder_path = os.path.join(kb_root, folder_name)
        if not os.path.isdir(folder_path):
            print(f"  [SKIP] {folder_path} not found")
            continue

        print(f"\n  Indexing {folder_name} ({kb_type})...")
        docs = index_directory_with_repair(
            indexer=indexer,
            manager=manager,
            db=db,
            folder_path=folder_path,
            folder_name=folder_name,
            kb_type=kb_type,
        )
        print(f"  -> {len(docs)} documents indexed")
        total += len(docs)

    print(f"\n[DONE] Total: {total} documents indexed across {len(SEED_MAPPING)} knowledge bases")

    # Show stats
    stats = manager.get_stats()
    print(f"\nKnowledge Base Stats:")
    print(f"  KBs: {stats['kb_count']}")
    print(f"  Documents: {stats['doc_count']}")
    print(f"  Chunks: {stats['chunk_count']}")
    print(f"  By type: {stats['by_type']}")

    db.close()


if __name__ == "__main__":
    force = "--force" in sys.argv
    seed_all(force=force)
