from backend.core.llm import get_llm
from backend.rag.embeddings import get_embedding_client


MULTI_QUERY_PROMPT = """从原始查询中提取核心概念，生成 {n} 个不同视角的检索变体，用于从知识库中召回相关文档。

规则:
1. 识别原始查询中的: 技术名词、业务对象、安全概念、标准/规范名、框架/工具名。
2. 每个变体从不同维度扩展原文，覆盖:
   - 概念定义: "什么是X" / "X的定义"
   - 实践指南: "X的实现方法" / "X的最佳实践"
   - 安全/合规: "X的安全要求" / "X的合规标准"
   - 代码/模板: "X的代码示例" / "X的配置模板"
   - 关联概念: 把X和其他相关概念组合查询
3. 如果指定了知识库类型({kb_types})，优先生成与该类型匹配的变体。
4. 不要编造原文没有的概念。查询尽量简短(≤40字)。
5. 每行一个查询，不编号，不用Markdown。直接输出查询文本。

原始查询: {query}
查询变体:
"""

HYDE_PROMPT = """根据查询，写一段假设性的知识库文档片段（200-300字），用于向量检索召回相似内容。

要求: 这不是真实答案，而是模拟的知识库条目。围绕查询中的核心概念展开，包含技术定义、关键要点、常见实践。使用查询中出现的术语和技术名词。

查询: {query}
假设文档片段:
"""

STEP_BACK_PROMPT = """将具体查询抽象为一个更通用的上位查询，用于从更高层次召回相关知识。

要求: 从具体细节退一步到概念/模式/方法论层面。例如 "React useState闭包陷阱" → "React Hooks 状态管理最佳实践"。保留技术栈名称。只输出一个查询句。

查询: {query}
上位查询:
"""


MAX_QUERY_LENGTH = 300


class QueryTransformer:
    """Query transformer: multi-query, HyDE, and step-back rewriting."""

    def __init__(self):
        self.llm = get_llm()
        self.embedder = get_embedding_client()

    @staticmethod
    def _truncate(query: str) -> str:
        """Trim long queries while keeping both task and constraints."""
        if len(query) <= MAX_QUERY_LENGTH:
            return query
        return query[:220] + " ... " + query[-80:]

    def multi_query(self, query: str, n: int = 4, kb_types: list[str] | None = None) -> list[str]:
        """Generate query-adaptive query variants."""
        query = self._truncate(query)
        kb_type_str = ", ".join(kb_types) if kb_types else "通用"
        prompt = MULTI_QUERY_PROMPT.format(n=n, query=query, kb_types=kb_type_str)
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=512,
        )
        raw_lines = [line.strip() for line in response.strip().split("\n") if line.strip()]
        print(f"[QueryTransform] multi_query('{query}'): LLM raw ({len(raw_lines)} lines)")

        import re
        cleaned = []
        for variant in raw_lines:
            variant = re.sub(r"^[\d]+[\.、)]\s*", "", variant).strip()
            variant = variant.strip("-*` ")
            if variant and len(variant) >= 3:
                cleaned.append(variant)

        seen = set()
        unique = []
        for variant in cleaned:
            key = variant.lower()
            if key not in seen and variant != query:
                seen.add(key)
                unique.append(variant)

        if not unique:
            # Generic fallback: append domain-relevant suffixes extracted from query
            words = re.findall(r'[\w一-鿿]{2,}', query)
            key_terms = " ".join(words[:4]) if words else ""
            fallbacks = [
                f"{key_terms} 定义 概念",
                f"{key_terms} 实现 最佳实践",
                f"{key_terms} 安全 合规",
                f"{key_terms} 代码 模板",
            ]
            for f in fallbacks:
                key = f.lower()
                if key not in seen:
                    seen.add(key)
                    unique.append(f)

        if query not in unique:
            unique.insert(0, query)

        result = unique[:n + 1]
        print(f"[QueryTransform] multi_query final: {result}")
        return result

    def hyde(self, query: str) -> list[float]:
        """Generate a hypothetical design document fragment and embed it."""
        query = self._truncate(query)
        prompt = HYDE_PROMPT.format(query=query)
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=512,
        )
        hypothetical_answer = response.strip()
        return self.embedder.embed_query(hypothetical_answer)

    def step_back(self, query: str) -> str:
        """Abstract the query to a broader software-design query."""
        query = self._truncate(query)
        prompt = STEP_BACK_PROMPT.format(query=query)
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=256,
        )
        return response.strip()

    def transform(self, query: str, methods: list[str] | None = None, kb_types: list[str] | None = None) -> dict:
        """
        Apply multiple transformation methods. Parallelizes independent LLM calls.

        Returns:
            dict with:
                - queries: list of query strings for retrieval
                - hyde_embedding: optional HyDE embedding
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if methods is None:
            methods = ["multi_query", "step_back"]

        result = {"queries": [query], "hyde_embedding": None}

        need_multi = "multi_query" in methods
        need_hyde = "hyde" in methods
        need_step_back = "step_back" in methods

        if need_multi:
            result["queries"] = self.multi_query(query, n=3, kb_types=kb_types)

        # Run hyde and step_back in parallel (independent of each other)
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            if need_hyde:
                futures["hyde"] = executor.submit(self._safe_hyde, query)
            if need_step_back:
                futures["step_back"] = executor.submit(self._safe_step_back, query)

            for future in as_completed(futures.values()):
                pass  # Results handled via the futures dict below

        if "hyde" in futures:
            try:
                result["hyde_embedding"] = futures["hyde"].result()
            except Exception as e:
                print(f"[QueryTransform] hyde failed: {e}")

        if "step_back" in futures:
            try:
                step_back_query = futures["step_back"].result()
                if step_back_query and step_back_query != query:
                    result["queries"].append(step_back_query)
            except Exception as e:
                print(f"[QueryTransform] step_back failed: {e}")

        return result

    def _safe_hyde(self, query: str) -> list[float] | None:
        """Wrapper to make hyde safe for thread-pool submission."""
        return self.hyde(query)

    def _safe_step_back(self, query: str) -> str:
        """Wrapper to make step_back safe for thread-pool submission."""
        return self.step_back(query)


_query_transformer: QueryTransformer | None = None


def get_query_transformer() -> QueryTransformer:
    global _query_transformer
    if _query_transformer is None:
        _query_transformer = QueryTransformer()
    return _query_transformer
