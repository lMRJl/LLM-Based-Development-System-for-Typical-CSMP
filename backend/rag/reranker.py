from backend.core.llm import get_llm

RERANK_PROMPT = """你是一个检索结果排序专家。评估以下检索结果与查询的相关性，并按相关度从高到低排序。

查询: {query}

检索结果:
{documents}

请为每个结果打分 (0-10)，然后按分数从高到低排序输出。输出 JSON 数组格式:
[
  {{"index": 0, "score": 8.5, "reason": "短评"}},
  ...
]

只输出 JSON，不要其他内容。"""


class LLMReranker:
    """LLM-based 重排序 — 用 DeepSeek 直接打分"""

    def __init__(self):
        self.llm = get_llm()

    def rerank(self, query: str, documents: list[dict], top_k: int = 5) -> list[dict]:
        """
        Rerank documents using LLM scoring.

        Args:
            query: Original query
            documents: List of {content, metadata, score, ...}
            top_k: Number of top results to return

        Returns:
            Reranked list of documents
        """
        if len(documents) <= top_k:
            return documents

        import json

        # Build document list for prompt
        doc_list = ""
        for i, doc in enumerate(documents):
            content_preview = doc["content"][:500]
            doc_list += f"[{i}] {content_preview}\n\n"

        # Truncate query to avoid prompt bloat
        short_query = query[:300] if len(query) > 300 else query
        prompt = RERANK_PROMPT.format(query=short_query, documents=doc_list)
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024,
        )

        try:
            # Extract JSON
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1]
                if response.endswith("```"):
                    response = response[:-3]
            scores = json.loads(response)
        except (json.JSONDecodeError, IndexError):
            # Fallback: return original order
            return documents[:top_k]

        # Sort and take top_k
        reordered = []
        for item in sorted(scores, key=lambda x: x.get("score", 0), reverse=True):
            idx = item.get("index", 0)
            if 0 <= idx < len(documents):
                doc = documents[idx].copy()
                doc["rerank_score"] = item.get("score", 0)
                doc["rerank_reason"] = item.get("reason", "")
                reordered.append(doc)

        return reordered[:top_k]

    def mmr_diversify(self, documents: list[dict], top_k: int = 5, lambda_param: float = 0.7) -> list[dict]:
        """
        Maximal Marginal Relevance — 平衡相关性和多样性.
        Simple keyword-based MMR (no embedding) for speed.
        """
        if len(documents) <= top_k:
            return documents

        from backend.rag.embeddings import get_embedding_client
        embedder = get_embedding_client()

        doc_texts = [d["content"][:500] for d in documents]
        doc_embeddings = embedder.embed(doc_texts)

        selected = [0]  # Start with the highest-scored
        remaining = list(range(1, len(documents)))

        while len(selected) < top_k and remaining:
            mmr_scores = []
            for idx in remaining:
                relevance = self._cosine_sim(
                    doc_embeddings[idx],
                    doc_embeddings[0],  # Query proxy: first document
                )
                max_redundancy = max(
                    self._cosine_sim(doc_embeddings[idx], doc_embeddings[s])
                    for s in selected
                )
                mmr = lambda_param * relevance - (1 - lambda_param) * max_redundancy
                mmr_scores.append((idx, mmr))

            best = max(mmr_scores, key=lambda x: x[1])
            selected.append(best[0])
            remaining.remove(best[0])

        return [documents[i] for i in selected]

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x ** 2 for x in a) ** 0.5
        norm_b = sum(x ** 2 for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


_reranker: LLMReranker | None = None


def get_reranker() -> LLMReranker:
    global _reranker
    if _reranker is None:
        _reranker = LLMReranker()
    return _reranker
