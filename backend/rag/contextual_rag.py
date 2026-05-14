from backend.core.llm import get_llm

CONTEXT_PREFIX_PROMPT = """你是一个文档上下文生成助手。为以下文档片段生成一个简短的上下文前缀（1-3句话），
说明这个片段来自什么类型的文档、在全文中的位置/作用。

文档标题: {title}
文档类型: {doc_type}

片段内容:
{chunk_content}

上下文前缀 (简洁，不超过80字):"""


class ContextualRAG:
    """
    Contextual Retrieval (Anthropic 方案):
    为每个 chunk 自动生成上下文前缀，提升检索时的匹配精度。
    """

    def __init__(self):
        self.llm = get_llm()

    def generate_context_prefix(self, chunk_content: str, title: str = "", doc_type: str = "") -> str:
        """Generate a context prefix for a chunk."""
        prompt = CONTEXT_PREFIX_PROMPT.format(
            title=title or "未知文档",
            doc_type=doc_type or "技术文档",
            chunk_content=chunk_content[:1000],
        )
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=128,
        )
        return response.strip()

    def enrich_chunks(self, chunks: list[str], title: str = "", doc_type: str = "") -> list[dict]:
        """
        Generate context prefixes for all chunks.

        Returns:
            List of {content, prefix, enriched_content}
        """
        enriched = []
        for chunk in chunks:
            prefix = self.generate_context_prefix(chunk, title, doc_type)
            enriched_content = f"{prefix}\n\n{chunk}"
            enriched.append({
                "content": chunk,
                "prefix": prefix,
                "enriched_content": enriched_content,
            })
        return enriched

    def batch_generate(self, chunks: list[str], title: str = "", doc_type: str = "", batch_size: int = 5) -> list[str]:
        """Batch generate prefixes for efficiency."""
        prefixes = []
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            for chunk in batch:
                prefix = self.generate_context_prefix(chunk, title, doc_type)
                prefixes.append(prefix)
        return prefixes


_contextual_rag: ContextualRAG | None = None


def get_contextual_rag() -> ContextualRAG:
    global _contextual_rag
    if _contextual_rag is None:
        _contextual_rag = ContextualRAG()
    return _contextual_rag
