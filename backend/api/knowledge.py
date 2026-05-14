"""Knowledge API — wired to RAG pipeline and indexer"""

from fastapi import APIRouter, Request
from backend.models.database import get_session
from backend.schemas.api import KnowledgeSearchRequest, KnowledgeSearchResponse, KnowledgeSearchResult, DocumentUploadRequest, KnowledgeStatsResponse
from backend.rag import get_rag_pipeline
from backend.rag.indexer import DocumentIndexer
from backend.rag.knowledge_base import KnowledgeBaseManager

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def get_db(request: Request):
    return get_session(request.app.state.engine)


@router.post("/search", response_model=KnowledgeSearchResponse)
def search_knowledge(data: KnowledgeSearchRequest, request: Request):
    """Search knowledge base with full Advanced RAG pipeline."""
    pipeline = get_rag_pipeline()
    result = pipeline.retrieve(
        query=data.query,
        top_k=data.top_k,
        kb_types=data.kb_types or None,
        use_query_transform=True,
        use_rerank=True,
        use_self_rag=True,
        use_graph=True,
    )

    docs = result.get("documents", [])
    results = []
    for d in docs:
        meta = d.get("metadata", {})
        results.append(KnowledgeSearchResult(
            content=d.get("content", ""),
            doc_title=meta.get("doc_title", "unknown"),
            kb_name=meta.get("kb_name", "unknown"),
            score=d.get("rrf_score", d.get("score", 0)),
            metadata=meta,
        ))

    return KnowledgeSearchResponse(
        results=results,
        query_variants=result.get("query_variants", []),
    )


@router.post("/documents")
def upload_document(data: DocumentUploadRequest, request: Request):
    """Upload and index a document into the knowledge base."""
    db = get_db(request)
    indexer = DocumentIndexer(db)
    doc = indexer.index_document(
        kb_name=data.kb_name,
        kb_type=data.kb_type,
        title=data.title,
        content=data.content,
        doc_format=data.format,
    )
    return {
        "status": "ok",
        "doc_id": doc.id,
        "chunk_count": doc.chunk_count,
        "message": f"Document '{data.title}' indexed with {doc.chunk_count} chunks",
    }


@router.get("/stats", response_model=KnowledgeStatsResponse)
def get_knowledge_stats(request: Request):
    """Get knowledge base statistics."""
    db = get_db(request)
    manager = KnowledgeBaseManager(db)
    stats = manager.get_stats()
    return KnowledgeStatsResponse(
        kb_count=stats["kb_count"],
        doc_count=stats["doc_count"],
        chunk_count=stats["chunk_count"],
        by_type=stats["by_type"],
    )


@router.delete("/{kb_name}")
def delete_knowledge_base(kb_name: str, request: Request):
    """Delete a knowledge base and all its documents, chunks, and ChromaDB collection."""
    db = get_db(request)
    manager = KnowledgeBaseManager(db)
    success = manager.delete_kb(kb_name)
    if not success:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_name}' not found")
    return {"status": "ok", "message": f"Knowledge base '{kb_name}' and all its data deleted"}


@router.post("/reindex")
def reindex_knowledge(request: Request):
    """Rebuild knowledge base index from stored documents."""
    db = get_db(request)
    manager = KnowledgeBaseManager(db)
    from backend.seed import seed_all
    seed_all(force=True)
    db.expire_all()
    stats = manager.get_stats()
    return {"status": "ok", "message": f"Reindexed: {stats['doc_count']} docs, {stats['chunk_count']} chunks"}
