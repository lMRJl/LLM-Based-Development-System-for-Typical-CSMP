"""
Advanced RAG Module — 统一的检索增强生成管道
"""

from backend.rag.query_transformer import get_query_transformer
from backend.rag.retriever import get_hybrid_retriever
from backend.rag.reranker import get_reranker
from backend.rag.self_rag import get_self_rag
from backend.rag.graph_rag import get_graph_rag


class RAGPipeline:
    """Unified RAG Pipeline: Query Transform → Hybrid Retrieve → Rerank → Self-RAG Check → Graph Context → Assemble"""

    MAX_DOC_CONTENT_CHARS = 600

    def __init__(self):
        self.transformer = get_query_transformer()
        self.retriever = get_hybrid_retriever()
        self.reranker = get_reranker()
        self.self_rag = get_self_rag()
        self.graph_rag = get_graph_rag()
        # Collection cache with 30s TTL
        self._collections_cache: tuple[float, list[str]] | None = None
        self._collections_cache_ttl = 30.0

    def _discover_collections(self, use_cache: bool = True) -> list[str]:
        """Discover all knowledge base collections (kb_*) with optional TTL cache."""
        import time
        if use_cache and self._collections_cache is not None:
            ts, cached = self._collections_cache
            if time.time() - ts < self._collections_cache_ttl:
                return cached

        try:
            all_collections = self.retriever.vector_store.client.list_collections()
            names = [c.name if hasattr(c, 'name') else str(c) for c in all_collections]
            kb_collections = [n for n in names if n.startswith("kb_")]
            self._collections_cache = (time.time(), kb_collections)
            print(f"[RAG] _discover_collections: all={names}, kb={kb_collections}")
            return kb_collections
        except Exception as e:
            print(f"[RAG] _discover_collections ERROR: {e}")
            return []

    def _has_docs(self) -> bool:
        """Check if any kb_* collection has documents."""
        for coll_name in self._discover_collections():
            count = self.retriever.vector_store.count(coll_name)
            if count > 0:
                return True
        return False

    def invalidate_collection_cache(self):
        """Force next _discover_collections call to bypass cache."""
        self._collections_cache = None

    def _do_retrieval(
        self,
        queries: list[str],
        search_collections: list[str],
        top_k: int,
        where: dict | None,
        use_rerank: bool = True,
    ) -> list[dict]:
        """Shared retrieval loop. Pre-computes embeddings for unique queries to avoid redundant API calls."""
        all_docs = []
        seen_content = set()

        # Pre-compute embeddings: one per unique query, not per collection
        unique_queries = list(dict.fromkeys(queries[:3]))  # dedup preserving order
        query_embeddings: dict[str, list[float]] = {}
        for q in unique_queries:
            try:
                query_embeddings[q] = self.retriever.embedder.embed_query(q)
            except Exception as e:
                print(f"[RAG] embed_query failed for '{q[:30]}': {e}")
        print(f"[RAG] _do_retrieval: {len(unique_queries)} unique queries, {len(search_collections)} collections, {len(unique_queries) * len(search_collections)} searches")

        for coll_name in search_collections:
            for q in unique_queries:
                q_embedding = query_embeddings.get(q)
                if q_embedding is None:
                    continue
                try:
                    docs = self.retriever.retrieve_with_embedding(
                        query=q, embedding=q_embedding, collection_name=coll_name,
                        top_k=max(top_k, 5),
                        dense_top_k=20, bm25_top_k=20,
                        where=where,
                    )
                    for doc in docs:
                        key = doc["content"][:200]
                        if key not in seen_content:
                            seen_content.add(key)
                            all_docs.append(doc)
                except Exception as e:
                    print(f"[RAG] retrieve: coll='{coll_name}' ERROR: {e}")
                    continue

        if not all_docs:
            return []

        if use_rerank and len(all_docs) > top_k:
            all_docs = self.reranker.rerank(queries[0], all_docs, top_k=min(top_k * 2, len(all_docs)))
            all_docs = self.reranker.mmr_diversify(all_docs, top_k=top_k)
        return all_docs[:top_k]

    def _truncate_doc_content(self, content: str) -> str:
        """Truncate document content to max chars, keeping head and tail."""
        if len(content) <= self.MAX_DOC_CONTENT_CHARS:
            return content
        separator = "\n...(truncated)...\n"
        head = int(self.MAX_DOC_CONTENT_CHARS * 0.65)
        tail = self.MAX_DOC_CONTENT_CHARS - head - len(separator)
        if tail < 20:
            # Budget too tight — just take the head
            return content[:self.MAX_DOC_CONTENT_CHARS]
        return content[:head] + separator + content[-tail:]

    def retrieve(
        self,
        query: str,
        collection_name: str | None = None,
        top_k: int = 5,
        kb_types: list[str] | None = None,
        use_query_transform: bool = True,
        use_rerank: bool = True,
        use_self_rag: bool = True,
        use_graph: bool = True,
    ) -> dict:
        """Full RAG pipeline with per-step elapsed time logging and graceful degradation."""
        import time
        t_start = time.perf_counter()
        where = None
        if kb_types:
            where = {"kb_type": {"$in": kb_types}} if len(kb_types) > 1 else {"kb_type": kb_types[0]}

        result: dict = {"query_variants": [query], "graph_context": [], "evaluation": None}
        timings: dict[str, float] = {}

        # Step 1: Query transform (parallel hyde + step_back; multi_query synchronous baseline)
        t0 = time.perf_counter()
        if use_query_transform:
            try:
                transformed = self.transformer.transform(query, methods=["multi_query", "hyde", "step_back"], kb_types=kb_types)
                queries = transformed["queries"]
                hyde_embedding = transformed.get("hyde_embedding")
                result["query_variants"] = queries
            except Exception as e:
                print(f"[RAG] Step 1 (QueryTransform) FAILED: {e} — falling back to raw query")
                queries = [query]
                hyde_embedding = None
        else:
            queries = [query]
            hyde_embedding = None
        timings["1_query_transform"] = round(time.perf_counter() - t0, 3)
        print(f"[RAG] Step 1 query_transform: {len(queries)} variants, hyde={'yes' if hyde_embedding else 'no'} ({timings['1_query_transform']}s)")

        # Step 2: Discover collections (cached) and check if docs exist
        t0 = time.perf_counter()
        if collection_name:
            search_collections = [collection_name]
        else:
            search_collections = self._discover_collections()

        if not search_collections or not self._has_docs():
            result["documents"] = []
            result["context_string"] = ""
            print(f"[RAG] Step 2 abort: no collections or empty (collections={len(search_collections)})")
            return result
        timings["2_collections"] = round(time.perf_counter() - t0, 3)
        print(f"[RAG] Step 2 collections: {len(search_collections)} KBs, kb_types={kb_types} ({timings['2_collections']}s)")

        # Step 3: Hybrid retrieval via shared loop; HyDE as independent query variant
        t0 = time.perf_counter()
        if hyde_embedding:
            hyde_docs = []
            hyde_seen = set()
            for coll_name in search_collections:
                try:
                    result_d = self.retriever.vector_store.query_by_embeddings(
                        collection_name=coll_name,
                        query_embeddings=[hyde_embedding],
                        top_k=top_k,
                        where=where,
                    )
                    for i, doc in enumerate(result_d.get("documents", [])):
                        key = doc[:200]
                        if key not in hyde_seen:
                            hyde_seen.add(key)
                            meta = result_d.get("metadatas", [])
                            dists = result_d.get("distances", [])
                            hyde_docs.append({
                                "content": doc,
                                "metadata": meta[i] if i < len(meta) else {},
                                "score": 1.0 - dists[i] if i < len(dists) else 0.0,
                                "source": "hyde_dense",
                            })
                except Exception as e:
                    print(f"[RAG] hyde retrieval: coll='{coll_name}' ERROR: {e}")

        all_docs = self._do_retrieval(queries, search_collections, top_k, where, use_rerank)

        # Merge HyDE docs
        if hyde_embedding and hyde_docs:
            seen_keys = {d["content"][:200] for d in all_docs}
            for d in hyde_docs:
                key = d["content"][:200]
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_docs.append(d)

        if not all_docs:
            result["documents"] = []
            result["context_string"] = ""
            timings["3_retrieval"] = round(time.perf_counter() - t0, 3)
            print(f"[RAG] Step 3 retrieval: 0 docs found after searching {len(search_collections)} KBs ({timings['3_retrieval']}s)")
            result["_timings"] = timings
            return result

        timings["3_retrieval"] = round(time.perf_counter() - t0, 3)
        print(f"[RAG] Step 3 retrieval: {len(all_docs)} docs from {len(search_collections)} KBs ({timings['3_retrieval']}s)")

        # Rerank
        t0 = time.perf_counter()
        if use_rerank and len(all_docs) > top_k:
            try:
                all_docs = self.reranker.rerank(query, all_docs, top_k=min(top_k * 2, len(all_docs)))
                all_docs = self.reranker.mmr_diversify(all_docs, top_k=top_k)
            except Exception as e:
                print(f"[RAG] Step 4 (Rerank) FAILED: {e} — using un-reranked results")
        all_docs = all_docs[:top_k]
        timings["4_rerank"] = round(time.perf_counter() - t0, 3)
        print(f"[RAG] Step 4 rerank: {len(all_docs)} docs after LLM rerank+MMR ({timings['4_rerank']}s)")

        # Self-RAG evaluation + refinement (uses shared _do_retrieval)
        t0 = time.perf_counter()
        if use_self_rag:
            current_query = query
            evaluations = []
            refinements = []
            trigger_count = 0
            max_rounds = max(1, self.self_rag.max_retries + 1)

            for round_index in range(max_rounds):
                evaluation = self.self_rag.evaluate(current_query, all_docs)
                evaluations.append({"round": round_index, "query": current_query, "evaluation": evaluation})
                result["evaluation"] = evaluation

                if evaluation.get("relevance") != "low" or not evaluation.get("refined_query"):
                    break

                refined = str(evaluation["refined_query"]).strip()
                if not refined or refined == current_query:
                    break

                refined_docs = self._do_retrieval(
                    [refined], search_collections, top_k, where, use_rerank=use_rerank,
                )
                if not refined_docs:
                    refinements.append({"round": round_index + 1, "query": refined, "accepted": False, "reason": "no_documents"})
                    break

                all_docs = refined_docs
                current_query = refined
                trigger_count += 1
                result["query_variants"].append(refined)
                refinements.append({"round": round_index + 1, "query": refined, "accepted": True, "doc_count": len(all_docs)})

            result["self_rag"] = {
                "triggered": trigger_count > 0,
                "trigger_count": trigger_count,
                "initial_query": query,
                "final_query": current_query,
                "evaluations": evaluations,
                "refinements": refinements,
            }
        else:
            result["self_rag"] = None
        timings["5_self_rag"] = round(time.perf_counter() - t0, 3)
        print(f"[RAG] Step 5 self_rag: triggered={result.get('self_rag',{}).get('triggered',False) if result.get('self_rag') else False} ({timings['5_self_rag']}s)")

        # Graph context
        t0 = time.perf_counter()
        if use_graph:
            try:
                result["graph_context"] = self.graph_rag.get_subgraph_context(query)
            except Exception as e:
                print(f"[RAG] Step 6 (GraphRAG) FAILED: {e}")
        timings["6_graph"] = round(time.perf_counter() - t0, 3)

        # Assemble context string with truncated doc content
        context_parts = []
        for i, doc in enumerate(all_docs):
            source_info = doc.get("metadata", {})
            kb = source_info.get("kb_name", "unknown")
            title = source_info.get("doc_title", "unknown")
            truncated = self._truncate_doc_content(doc["content"])
            context_parts.append(f"[Source {i+1}] KB: {kb} | Doc: {title}\n{truncated}")

        if result["graph_context"]:
            graph_parts = ["\n--- Knowledge Graph Context ---"]
            for ec in result["graph_context"][:5]:
                graph_parts.append(
                    f"Entity: {ec['entity']} ({ec['type']})\n"
                    f"Description: {ec['description']}\n"
                    f"Relations: {', '.join(r['target'] + ' - ' + r['relation'] for r in ec.get('relations', []))}"
                )
            context_parts.extend(graph_parts)

        context_string = "\n\n---\n\n".join(context_parts)
        result["documents"] = all_docs
        result["context_string"] = context_string
        timings["total"] = round(time.perf_counter() - t_start, 3)
        result["_timings"] = timings
        print(f"[RAG] retrieve complete: {len(all_docs)} docs, {len(context_string)} chars, total={timings['total']}s steps={timings}")
        return result


_rag_pipeline: RAGPipeline | None = None


def get_rag_pipeline() -> RAGPipeline:
    global _rag_pipeline
    if _rag_pipeline is None:
        _rag_pipeline = RAGPipeline()
    return _rag_pipeline
