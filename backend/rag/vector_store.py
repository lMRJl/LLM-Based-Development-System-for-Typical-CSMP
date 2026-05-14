import chromadb
import uuid
from backend.config import get_settings
from backend.rag.embeddings import get_embedding_client


class VectorStore:
    """ChromaDB 向量存储封装"""

    def __init__(self):
        settings = get_settings()
        self.client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self.embedding_client = get_embedding_client()

    def get_or_create_collection(self, name: str):
        return self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def list_collections(self) -> list[str]:
        return self.client.list_collections()

    def add(
        self,
        collection_name: str,
        documents: list[str],
        metadatas: list[dict] | None = None,
        ids: list[str] | None = None,
    ):
        """Add documents with automatic embedding."""
        collection = self.get_or_create_collection(collection_name)
        embeddings = self.embedding_client.embed(documents)

        if ids is None:
            ids = [str(uuid.uuid4()) for _ in documents]

        collection.add(
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas or [{}] * len(documents),
            ids=ids,
        )

    def query(
        self,
        collection_name: str,
        query_text: str,
        top_k: int = 5,
        where: dict | None = None,
    ) -> dict:
        """Query with dense embedding."""
        collection = self.get_or_create_collection(collection_name)
        query_embedding = self.embedding_client.embed_query(query_text)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        return {
            "ids": results["ids"][0] if results["ids"] else [],
            "documents": results["documents"][0] if results["documents"] else [],
            "metadatas": results["metadatas"][0] if results["metadatas"] else [],
            "distances": results["distances"][0] if results["distances"] else [],
        }

    def query_by_embeddings(
        self,
        collection_name: str,
        query_embeddings: list[list[float]],
        top_k: int = 5,
        where: dict | None = None,
    ) -> dict:
        """Query with pre-computed embeddings."""
        collection = self.get_or_create_collection(collection_name)
        results = collection.query(
            query_embeddings=query_embeddings,
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return {
            "ids": results["ids"][0] if results["ids"] else [],
            "documents": results["documents"][0] if results["documents"] else [],
            "metadatas": results["metadatas"][0] if results["metadatas"] else [],
            "distances": results["distances"][0] if results["distances"] else [],
        }

    def delete_collection(self, name: str):
        try:
            self.client.delete_collection(name)
        except Exception:
            pass

    def count(self, collection_name: str) -> int:
        collection = self.get_or_create_collection(collection_name)
        return collection.count()


_vector_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store
