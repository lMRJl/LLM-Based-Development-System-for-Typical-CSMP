from backend.core.llm import get_llm

SELF_RAG_PROMPT = """你是一个检索质量评估助手。评估以下检索结果是否与查询相关且有用。

查询: {query}

检索结果:
{documents}

请评估:
1. 整体相关性: 高 / 中 / 低
2. 每个结果是否与查询相关: 相关 / 部分相关 / 不相关
3. 如果整体相关性为"低"，建议如何改进查询

输出 JSON 格式:
{{
  "relevance": "high|medium|low",
  "per_doc": [
    {{"index": 0, "relevance": "relevant", "reason": "..."}}
  ],
  "refined_query": "改进后的查询 (仅当 relevance=low 时提供)"
}}"""


class SelfRAG:
    """Self-RAG: LLM 自评估检索质量 + 修正循环"""

    def __init__(self, relevance_threshold: str = "medium", max_retries: int = 2):
        """
        Args:
            relevance_threshold: Minimum relevance to accept ("high" or "medium")
            max_retries: Maximum refinement retries
        """
        self.relevance_threshold = relevance_threshold
        self.max_retries = max_retries
        self.llm = get_llm()

    def evaluate(self, query: str, documents: list[dict]) -> dict:
        """Evaluate retrieval quality."""
        import json

        if not documents:
            return {"relevance": "low", "per_doc": [], "refined_query": query}

        doc_list = ""
        for i, doc in enumerate(documents[:10]):
            content_preview = doc["content"][:300]
            doc_list += f"[{i}] {content_preview}\n\n"

        # Truncate query to avoid prompt bloat
        short_query = query[:300] if len(query) > 300 else query
        prompt = SELF_RAG_PROMPT.format(query=short_query, documents=doc_list)
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024,
        )

        try:
            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1]) if response.endswith("```") else "\n".join(lines[1:])
            return json.loads(response)
        except json.JSONDecodeError:
            return {"relevance": "medium", "per_doc": [], "refined_query": query}

    def retrieve_with_feedback(
        self,
        query: str,
        retrieve_fn,
        top_k: int = 5,
    ) -> tuple[list[dict], dict]:
        """
        Retrieve and refine until quality meets threshold.

        Args:
            query: Original query
            retrieve_fn: Function(query, top_k) -> list[dict]
            top_k: Number of documents to return

        Returns:
            (final_documents, evaluation_result)
        """
        current_query = query
        evaluation = {"relevance": "low"}
        all_docs = []

        for attempt in range(self.max_retries + 1):
            docs = retrieve_fn(current_query, top_k=top_k)
            all_docs = docs
            evaluation = self.evaluate(current_query, docs)

            relevance_rank = {"high": 3, "medium": 2, "low": 1}
            if relevance_rank.get(evaluation["relevance"], 0) >= relevance_rank.get(self.relevance_threshold, 2):
                break

            if evaluation.get("refined_query") and evaluation["refined_query"] != current_query:
                current_query = evaluation["refined_query"]

        return all_docs, evaluation


_self_rag: SelfRAG | None = None


def get_self_rag() -> SelfRAG:
    global _self_rag
    if _self_rag is None:
        _self_rag = SelfRAG()
    return _self_rag
