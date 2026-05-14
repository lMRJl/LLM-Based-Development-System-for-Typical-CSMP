"""PRD Agent - RAG thinking first, then strict template-based PRD generation."""

from pathlib import Path
import re

from backend.agents.base import BaseAgent
from backend.core.prompts import PRD_SYSTEM, NO_PREAMBLE


DEFAULT_PRD_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "prd_templates"
    / "standard_prd_template.md"
)


PRD_RAG_THINK_PROMPT = """你是一个产品需求分析专家。请根据用户需求和 RAG 检索结果，提炼生成 PRD 所需的需求思考。
只输出可用于 PRD 写作的结构化要点，不要生成 PRD 正文。

用户需求摘要:
{user_input}

RAG 检索结果:
{rag_context}

请输出:
1. 业务目标
2. 目标用户与使用场景
3. 核心功能清单
4. 数据对象与数据流
5. 权限、安全、合规要求
6. 非功能需求
7. 风险、约束和待确认问题
"""


PRD_TEMPLATE_GENERATION_PROMPT = """你是一个资深产品经理，专注于网络安全领域。请严格参考指定 PRD 模板生成 PRD 文档。

硬性要求:
- PRD 模板是确定性资源，必须完整使用，不允许截断、概括或改写模板结构。
- 必须保留模板中的所有 Markdown 标题和表格结构。
- 不要新增模板之外的一级章节。
- 如果信息缺失，在对应位置写“待确认”，不要编造。
- 内容只能依据“用户需求摘要”和“RAG 思考结果”填写。
- 输出完整 Markdown PRD 文档，不要输出解释、开场白或 Markdown 代码块。

用户需求摘要:
{user_input}

RAG 思考结果:
{rag_thought}

PRD 模板:
{template}
"""


PRD_TEMPLATE_REPAIR_PROMPT = """下面的 PRD 输出没有完整保留指定模板结构。请严格按模板标题和表格结构重写。

要求:
- 必须保留模板中的所有 Markdown 标题。
- 必须保留模板中的所有表格。
- 不要新增模板之外的一级章节。
- 已有内容尽量填入对应章节。
- 信息缺失处写“待确认”。
- 只输出完整 PRD Markdown，不要解释。

PRD 模板:
{template}

当前 PRD:
{prd_content}
"""


class PRDAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="prd",
            system_prompt=PRD_SYSTEM,
            tools=None,
            use_rag=True,
        )

    def generate(self, user_input: str, project_context: str = "", template: str | None = None) -> dict:
        rag = self.rag.retrieve(
            query=self._build_rag_query(user_input),
            top_k=5,
            kb_types=["compliance", "pattern", "template"],
            use_query_transform=True,
            use_rerank=True,
            use_self_rag=True,
            use_graph=True,
        )
        rag_context = rag.get("context_string", "")
        rag_thought = self._think_with_rag(user_input, rag_context)
        template_content = template or self._load_template()
        template_content = self._truncate_template(template_content)

        prompt = PRD_TEMPLATE_GENERATION_PROMPT.format(
            user_input=user_input,
            rag_thought=rag_thought,
            template=template_content,
        )
        prompt += NO_PREAMBLE

        prd_content = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=8192,
        )
        prd_content = self._ensure_template_structure(prd_content, template_content)

        return {
            "prd_content": prd_content,
            "rag_context": rag_context,
            "rag_results": rag.get("documents", []),
            "rag_thought": rag_thought,
            "template_used": template_content,
        }

    def _build_rag_query(self, user_input: str) -> str:
        domain = self._extract_domain_keywords(user_input)
        return f"PRD 需求分析 {domain} 合规 权限 数据对象 验收标准 {user_input[:800]}"

    @staticmethod
    def _extract_domain_keywords(text: str) -> str:
        """Extract domain-specific nouns for better RAG retrieval precision."""
        words = re.findall(r'[一-鿿]{2,6}(?:系统|平台|管理|监控|分析|日志|告警|认证|授权|审计|报表|通知|流程|配置|检测|扫描|防护)?', text[:500])
        stops = {"系统", "平台", "管理", "可以", "需要", "一个", "这个", "所有", "进行", "使用",
                 "我们", "他们", "什么", "怎么", "如何", "是否", "已经", "没有"}
        domain = [w for w in words if w.lower() not in stops]
        seen = set()
        unique = []
        for w in domain:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        return " ".join(unique[:5])

    def _think_with_rag(self, user_input: str, rag_context: str) -> str:
        prompt = PRD_RAG_THINK_PROMPT.format(
            user_input=user_input,
            rag_context=rag_context or "无相关 RAG 检索结果。",
        )
        prompt += NO_PREAMBLE
        return self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048,
        )

    @staticmethod
    def _truncate_template(template: str, max_chars: int = 3000) -> str:
        """Truncate template to max chars, preserving section boundaries."""
        if len(template) <= max_chars:
            return template
        sections = re.split(r"\n(?=## )", template)
        parts = []
        used = 0
        for sec in sections:
            if used + len(sec) <= max_chars:
                parts.append(sec)
                used += len(sec)
            else:
                break
        truncated = "\n".join(parts)
        if len(truncated) < 200:
            return template[:max_chars]
        return truncated

    def _load_template(self) -> str:
        if not DEFAULT_PRD_TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"PRD template not found: {DEFAULT_PRD_TEMPLATE_PATH}")
        template = DEFAULT_PRD_TEMPLATE_PATH.read_text(encoding="utf-8")
        self._validate_template(template)
        return template

    def _validate_template(self, template: str) -> None:
        headings = self._extract_headings(template)
        if not headings:
            raise ValueError("PRD template must contain Markdown headings.")
        if len(template.strip()) < 200:
            raise ValueError("PRD template is unexpectedly short; refusing to generate from a partial template.")

    def _ensure_template_structure(self, prd_content: str, template: str) -> str:
        missing = self._missing_template_headings(prd_content, template)
        if not missing:
            return prd_content

        print(f"[PRDAgent] WARNING: Generated PRD missing {len(missing)} template headings: {missing[:5]}... — attempting repair")

        repair_prompt = PRD_TEMPLATE_REPAIR_PROMPT.format(
            template=template,
            prd_content=prd_content,
        )
        repaired = self.llm.chat_sync(
            messages=[{"role": "user", "content": repair_prompt}],
            temperature=0.1,
            max_tokens=8192,
        )
        repaired_missing = self._missing_template_headings(repaired, template)
        if repaired_missing:
            print(f"[PRDAgent] WARNING: Repair also missing {len(repaired_missing)} headings: {repaired_missing[:5]}... "
                  f"— using original output (best-effort)")
            return prd_content
        return repaired

    def _missing_template_headings(self, prd_content: str, template: str) -> list[str]:
        prd_headings = {self._normalize_heading(h) for h in self._extract_headings(prd_content)}
        return [heading for heading in self._extract_headings(template)
                if self._normalize_heading(heading) not in prd_headings]

    @staticmethod
    def _normalize_heading(heading: str) -> str:
        """Normalize heading for fuzzy comparison: strip trailing punctuation and whitespace."""
        return re.sub(r'[：:。，,、\s\*]+$', '', heading.strip()).strip()

    def _extract_headings(self, markdown: str) -> list[str]:
        headings = []
        for line in markdown.splitlines():
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match:
                headings.append(f"{match.group(1)} {match.group(2).strip()}")
        return headings
