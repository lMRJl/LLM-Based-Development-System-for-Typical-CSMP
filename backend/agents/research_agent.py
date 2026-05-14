"""Research Agent — 深度多轮 RAG 检索 + 信息综合"""

from backend.core.llm import get_llm
from backend.core.prompts import RESEARCH_SYSTEM
from backend.rag import get_rag_pipeline

RESEARCH_SUMMARY_PROMPT = """你是一个技术研究助手。基于以下多轮检索结果，为开发团队输出一份简洁的研究摘要。

当前阶段: {stage}
用户需求: {user_input}

检索结果:
{rag_results}

请输出:
1. 关键发现 (3-5条，每条一句话)
2. 推荐的技术方案方向
3. 需要注意的风险或合规要点
4. 可复用的模板/模式

直接输出内容，不要加开场白或"基于检索结果..."等过渡语句。"""



class ResearchAgent:
    """深度研究 Agent — 多轮 RAG 检索并综合信息"""

    def __init__(self):
        self.llm = get_llm()
        self.rag = get_rag_pipeline()

    def research(self, user_input: str, stage: str, kb_types: list[str] | None = None) -> dict:
        """
        Multi-round research:
        1. Initial broad retrieval
        2. Identify gaps → targeted retrieval
        3. Synthesize findings
        """
        # Round 1: Broad retrieval
        round1 = self.rag.retrieve(
            query=user_input,
            top_k=5,
            kb_types=kb_types,
            use_query_transform=True,
            use_rerank=True,
            use_self_rag=True,
            use_graph=True,
        )

        # Round 2: Targeted retrieval based on gaps
        queries = self._identify_gaps(user_input, stage, round1)
        round2_results = []
        if queries:
            for q in queries[:2]:
                r2 = self.rag.retrieve(
                    query=q,
                    top_k=3,
                    kb_types=kb_types,
                    use_query_transform=False,
                    use_rerank=True,
                    use_self_rag=False,
                    use_graph=False,
                )
                round2_results.extend(r2.get("documents", []))

        # Combine and synthesize
        all_docs = round1.get("documents", [])
        seen = set()
        unique_round2 = []
        for d in round2_results:
            key = d["content"][:200]
            if key not in seen:
                seen.add(key)
                unique_round2.append(d)
        all_docs.extend(unique_round2)

        # Early return if no documents found (empty KB) — skip LLM summary call
        if not all_docs:
            return {
                "summary": "知识库为空，未检索到相关文档。请先上传知识文档或运行种子数据注入。",
                "documents": [],
                "context_string": "",
                "graph_context": [],
                "queries_used": round1.get("query_variants", []) + queries,
            }

        # Generate synthesis
        context_str = "\n\n".join(
            f"[{i}] {d['content'][:500]}" for i, d in enumerate(all_docs[:8])
        )

        prompt = RESEARCH_SUMMARY_PROMPT.format(
            stage=stage,
            user_input=user_input,
            rag_results=context_str,
        )

        summary = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1024,
        )

        return {
            "summary": summary,
            "documents": all_docs,
            "context_string": round1.get("context_string", ""),
            "graph_context": round1.get("graph_context", []),
            "queries_used": round1.get("query_variants", []) + queries,
        }

    def _identify_gaps(self, user_input: str, stage: str, round1: dict) -> list[str]:
        """Identify knowledge gaps and generate follow-up queries."""
        evaluation = round1.get("evaluation", {})
        if evaluation.get("relevance") == "low":
            refined = evaluation.get("refined_query", "")
            if refined and refined != user_input:
                return [refined]

        # For DB stage, add schema-specific queries
        if stage == "database":
            return [f"数据库表结构设计 网络安全 {user_input[:100]}"]
        if stage == "backend":
            return [f"API安全 认证授权 代码实现 {user_input[:100]}"]
        if stage == "frontend":
            return [f"前端安全防护 XSS CSRF 组件实现"]

        return []
