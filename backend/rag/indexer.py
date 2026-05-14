import uuid
import datetime
from pathlib import Path
from sqlalchemy.orm import Session

from backend.rag.chunking import get_chunker
from backend.rag.vector_store import get_vector_store
from backend.rag.contextual_rag import get_contextual_rag
from backend.rag.graph_rag import get_graph_rag
from backend.models.database import (
    KnowledgeBase, Document, Chunk,
    KBType,
)


class DocumentIndexer:
    """文档摄取 & 索引管道"""

    def __init__(self, db: Session):
        self.db = db
        self.vector_store = get_vector_store()
        self.contextual_rag = get_contextual_rag()
        self.graph_rag = get_graph_rag()

    def index_document(
        self,
        kb_name: str,
        kb_type: str,
        title: str,
        content: str,
        source: str = "",
        doc_format: str = "md",
        chunk_strategy: str = "recursive",
        use_contextual: bool = True,
    ) -> Document:
        """Full indexing pipeline: chunk → embed → store → graph."""
        # 1. Get or create knowledge base
        kb = self.db.query(KnowledgeBase).filter(KnowledgeBase.name == kb_name).first()
        if not kb:
            kb = KnowledgeBase(
                id=str(uuid.uuid4()),
                name=kb_name,
                type=KBType(kb_type) if kb_type in [e.value for e in KBType] else KBType.TEMPLATE,
                description=f"Auto-created: {kb_name}",
            )
            self.db.add(kb)
            self.db.commit()
            self.db.refresh(kb)

        # 2. Create document record
        doc = Document(
            id=str(uuid.uuid4()),
            kb_id=kb.id,
            title=title,
            source=source,
            format=doc_format,
            raw_content=content,
            indexed_at=datetime.datetime.utcnow(),
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)

        # 3. Chunking
        chunker = get_chunker(strategy=chunk_strategy)
        chunk_texts = chunker.split(content)

        # 4. Contextual enrichment
        if use_contextual:
            enriched = self.contextual_rag.enrich_chunks(
                chunk_texts, title=title, doc_type=kb_type
            )
        else:
            enriched = [{"content": c, "prefix": "", "enriched_content": c} for c in chunk_texts]

        # 5. Index in ChromaDB
        collection_name = f"kb_{kb_name}"
        docs_to_index = [e["enriched_content"] for e in enriched]
        metadatas = [
            {"kb_name": kb_name, "kb_type": kb_type, "doc_title": title, "chunk_index": i, "source": source}
            for i in range(len(docs_to_index))
        ]
        chroma_ids = [str(uuid.uuid4()) for _ in docs_to_index]

        self.vector_store.add(
            collection_name=collection_name,
            documents=docs_to_index,
            metadatas=metadatas,
            ids=chroma_ids,
        )

        # 6. Save chunks in SQL
        for i, e in enumerate(enriched):
            chunk = Chunk(
                id=str(uuid.uuid4()),
                doc_id=doc.id,
                content=e["content"],
                chunk_index=i,
                context_prefix=e["prefix"],
                chroma_id=chroma_ids[i],
            )
            self.db.add(chunk)

        # 7. Graph RAG
        self.graph_rag.add_document(doc.id, content)

        # 8. Update doc
        doc.chunk_count = len(chunk_texts)
        doc.indexed_at = datetime.datetime.utcnow()
        self.db.commit()

        return doc

    def index_directory(self, directory: str, kb_name: str = None, kb_type: str = "template") -> list[Document]:
        """Index all .md and .txt files in a directory."""
        path = Path(directory)
        if not path.exists():
            return []

        docs = []
        for file_path in path.rglob("*.md"):
            content = file_path.read_text(encoding="utf-8")
            actual_kb = kb_name or path.name
            doc = self.index_document(
                kb_name=actual_kb,
                kb_type=kb_type,
                title=file_path.stem,
                content=content,
                source=str(file_path),
            )
            docs.append(doc)

        for file_path in path.rglob("*.txt"):
            content = file_path.read_text(encoding="utf-8")
            actual_kb = kb_name or path.name
            doc = self.index_document(
                kb_name=actual_kb,
                kb_type=kb_type,
                title=file_path.stem,
                content=content,
                source=str(file_path),
                doc_format="txt",
            )
            docs.append(doc)

        return docs

    def reindex_all(self, kb_name: str) -> int:
        """Re-index all documents in a knowledge base."""
        kb = self.db.query(KnowledgeBase).filter(KnowledgeBase.name == kb_name).first()
        if not kb:
            return 0

        # Clear existing
        for doc in kb.documents:
            self.db.delete(doc)
        self.vector_store.delete_collection(f"kb_{kb_name}")
        self.db.commit()

        # This would need raw content stored somewhere
        # For now, returns 0
        return 0
