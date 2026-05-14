from collections import defaultdict
from rank_bm25 import BM25Okapi

from backend.rag.vector_store import get_vector_store
from backend.rag.embeddings import get_embedding_client


class HybridRetriever:
    """Hybrid retriever: Dense (ChromaDB) + Sparse (BM25) + RRF fusion with BM25 cache."""

    def __init__(self, dense_weight: float = 0.6, bm25_weight: float = 0.4):
        self.dense_weight = dense_weight
        self.bm25_weight = bm25_weight
        self.vector_store = get_vector_store()
        self.embedder = get_embedding_client()
        # BM25 cache: key=(collection_name, frozenset(where_items)) → (doc_count, tokenized_corpus, BM25Okapi, filtered_docs, filtered_metadata)
        self._bm25_cache: dict[tuple, tuple] = {}

    def retrieve_with_embedding(
        self,
        query: str,
        embedding: list[float],
        collection_name: str = "knowledge_base",
        top_k: int = 5,
        dense_top_k: int = 20,
        bm25_top_k: int = 20,
        where: dict | None = None,
    ) -> list[dict]:
        """
        Hybrid retrieval using a pre-computed embedding — avoids redundant embedding API calls.
        """
        all_dense_results = []
        result = self.vector_store.query_by_embeddings(
            collection_name=collection_name,
            query_embeddings=[embedding],
            top_k=dense_top_k,
            where=where,
        )
        for i, doc in enumerate(result["documents"]):
            all_dense_results.append({
                "content": doc,
                "metadata": result["metadatas"][i] if i < len(result["metadatas"]) else {},
                "score": 1.0 - result["distances"][i] if i < len(result["distances"]) else 0.0,
                "source": "dense",
            })

        bm25_results = self._bm25_retrieve(
            collection_name=collection_name,
            query=query,
            top_k=bm25_top_k,
            where=where,
        )

        fused = self._rrf_fusion(all_dense_results, bm25_results, top_k)
        return fused

    def retrieve(
        self,
        query: str,
        collection_name: str = "knowledge_base",
        top_k: int = 5,
        dense_top_k: int = 20,
        bm25_top_k: int = 20,
        where: dict | None = None,
        extra_query_embeddings: list[list[float]] | None = None,
    ) -> list[dict]:
        """
        Hybrid retrieval with RRF fusion.

        Returns:
            List of dicts: {content, metadata, score, source}
        """
        # 1. Dense retrieval (query transform already done by RAGPipeline)
        all_dense_results = []
        # Use the query as-is; multi-query expansion is handled upstream
        queries = [query]
        for q in queries:
            result = self.vector_store.query(
                collection_name=collection_name,
                query_text=q,
                top_k=dense_top_k,
                where=where,
            )
            for i, doc in enumerate(result["documents"]):
                all_dense_results.append({
                    "content": doc,
                    "metadata": result["metadatas"][i] if i < len(result["metadatas"]) else {},
                    "score": 1.0 - result["distances"][i] if i < len(result["distances"]) else 0.0,
                    "source": "dense",
                })

        if extra_query_embeddings:
            result = self.vector_store.query_by_embeddings(
                collection_name=collection_name,
                query_embeddings=extra_query_embeddings,
                top_k=dense_top_k,
                where=where,
            )
            for i, doc in enumerate(result["documents"]):
                all_dense_results.append({
                    "content": doc,
                    "metadata": result["metadatas"][i] if i < len(result["metadatas"]) else {},
                    "score": 1.0 - result["distances"][i] if i < len(result["distances"]) else 0.0,
                    "source": "hyde_dense",
                })

        # 2. BM25 sparse retrieval
        bm25_results = self._bm25_retrieve(
            collection_name=collection_name,
            query=query,
            top_k=bm25_top_k,
            where=where,
        )

        # 3. RRF (Reciprocal Rank Fusion)
        fused = self._rrf_fusion(all_dense_results, bm25_results, top_k)

        return fused

    @staticmethod
    def _make_cache_key(collection_name: str, where: dict | None) -> tuple:
        """Create a hashable cache key from collection name and where filter."""
        if not where:
            return (collection_name,)
        # Flatten nested dict values (like {"$in": [...]}) to hashable tuples
        flat = []
        for k, v in sorted(where.items()):
            if isinstance(v, dict):
                flat.append((k, tuple(sorted((sk, str(sv)) for sk, sv in v.items()))))
            elif isinstance(v, list):
                flat.append((k, tuple(v)))
            else:
                flat.append((k, v))
        return (collection_name, tuple(flat))

    def _bm25_retrieve(
        self,
        collection_name: str,
        query: str,
        top_k: int = 20,
        where: dict | None = None,
    ) -> list[dict]:
        """BM25 sparse retrieval with per-collection index cache."""
        cache_key = self._make_cache_key(collection_name, where)

        collection = self.vector_store.client.get_or_create_collection(collection_name)
        current_count = collection.count()

        # Check cache validity
        cached = self._bm25_cache.get(cache_key)
        if cached is not None:
            cached_count, tokenized_corpus, bm25, cached_docs, cached_meta = cached
            if cached_count == current_count:
                # Cache hit — reuse BM25 index
                tokenized_query = self._tokenize(query)
                scores = bm25.get_scores(tokenized_query)
                indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
                results = []
                for idx, score in indexed[:top_k]:
                    if score > 0:
                        results.append({
                            "content": cached_docs[idx],
                            "metadata": cached_meta[idx] if idx < len(cached_meta) else {},
                            "score": float(score),
                            "source": "bm25",
                        })
                return results
            else:
                # Stale cache — evict
                del self._bm25_cache[cache_key]

        # Cache miss — rebuild
        all_data = collection.get(include=["documents", "metadatas"])
        if not all_data["documents"]:
            return []

        if where:
            filtered_docs = []
            filtered_metadata = []
            for i, meta in enumerate(all_data["metadatas"]):
                if self._metadata_matches(meta or {}, where):
                    filtered_docs.append(all_data["documents"][i])
                    filtered_metadata.append(meta)
        else:
            filtered_docs = list(all_data["documents"])
            filtered_metadata = list(all_data["metadatas"])

        if not filtered_docs:
            return []

        tokenized_corpus = [self._tokenize(doc) for doc in filtered_docs]
        bm25 = BM25Okapi(tokenized_corpus)
        # Store in cache
        self._bm25_cache[cache_key] = (current_count, tokenized_corpus, bm25, filtered_docs, filtered_metadata)

        tokenized_query = self._tokenize(query)
        scores = bm25.get_scores(tokenized_query)
        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results = []
        for idx, score in indexed[:top_k]:
            if score > 0:
                results.append({
                    "content": filtered_docs[idx],
                    "metadata": filtered_metadata[idx] if idx < len(filtered_metadata) else {},
                    "score": float(score),
                    "source": "bm25",
                })
        return results

    def invalidate_bm25_cache(self, collection_name: str | None = None):
        """Invalidate BM25 cache for a specific collection or all collections."""
        if collection_name is None:
            self._bm25_cache.clear()
            return
        keys_to_remove = [k for k in self._bm25_cache if k[0] == collection_name]
        for k in keys_to_remove:
            del self._bm25_cache[k]

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization for BM25."""
        import re
        return re.findall(r'[一-鿿]|[a-zA-Z]+|\d+', text.lower())

    def _metadata_matches(self, metadata: dict, where: dict) -> bool:
        """Support the subset of Chroma where filters used by this project."""
        for key, expected in where.items():
            actual = metadata.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$eq" in expected and actual != expected["$eq"]:
                    return False
                continue
            if actual != expected:
                return False
        return True

    def _rrf_fusion(self, dense: list[dict], bm25: list[dict], top_k: int) -> list[dict]:
        """Reciprocal Rank Fusion."""
        k = 60  # RRF constant
        score_map = defaultdict(float)
        doc_map = {}
        source_map = defaultdict(set)

        for rank, item in enumerate(dense):
            key = item["content"][:200]
            rrf_score = self.dense_weight / (k + rank + 1)
            score_map[key] += rrf_score
            doc_map[key] = item
            source_map[key].add(item.get("source", "dense"))

        for rank, item in enumerate(bm25):
            key = item["content"][:200]
            rrf_score = self.bm25_weight / (k + rank + 1)
            score_map[key] += rrf_score
            source_map[key].add(item.get("source", "bm25"))
            if key not in doc_map:
                doc_map[key] = item

        sorted_keys = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
        results = []
        for key, score in sorted_keys[:top_k]:
            item = doc_map[key].copy()
            item["rrf_score"] = score
            item["source"] = "+".join(sorted(source_map[key]))
            results.append(item)

        return results


_hybrid_retriever: HybridRetriever | None = None


def get_hybrid_retriever() -> HybridRetriever:
    global _hybrid_retriever
    if _hybrid_retriever is None:
        _hybrid_retriever = HybridRetriever()
    return _hybrid_retriever
