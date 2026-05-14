"""Design Agent — 概要设计 / 技术方案"""

import re

from backend.agents.base import BaseAgent
from backend.core.prompts import DESIGN_SYSTEM


# Required top-level sections in a valid design document
DESIGN_REQUIRED_SECTIONS = [
    r"系统架构", r"模块划分", r"API\s*设计", r"安全",
]


class DesignAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="design",
            system_prompt=DESIGN_SYSTEM,
            tools=None,
            use_rag=True,
        )

    def generate(self, user_input: str, prd_content: str = "", tech_stack: dict | None = None) -> dict:
        tech_stack = tech_stack or {}
        prompt = DESIGN_SYSTEM.format(
            language=tech_stack.get("language", "未指定"),
            framework_front=tech_stack.get("framework_front", "未指定"),
            framework_back=tech_stack.get("framework_back", "未指定"),
            database=tech_stack.get("database", "未指定"),
            prd_content=prd_content or user_input,
            rag_context="{rag_context}",
        )
        self.system_prompt = prompt

        # Customize RAG query with design-stage context
        rag_query = f"系统架构 模块划分 API设计 安全设计 {user_input[:300]}"
        state = self.run(user_input=rag_query, stage="design")

        design_content = state.get("generation_output", "")
        rag_context = state.get("rag_context", "")

        # Validate required sections
        missing_sections = self._check_required_sections(design_content)
        if missing_sections:
            print(f"[DesignAgent] WARNING: Design output missing sections: {missing_sections}")
            # Attempt repair via single retry with stronger section requirements
            repair_prompt = (
                prompt.replace("{rag_context}", rag_context or "无相关上下文")
                + "\n\n重要提醒：你的输出缺少以下必要章节："
                + "、".join(missing_sections)
                + "。请确保输出包含这些章节。"
            )
            self.system_prompt = repair_prompt
            retry_state = self.run(user_input=rag_query, stage="design")
            retry_content = retry_state.get("generation_output", "")
            retry_missing = self._check_required_sections(retry_content)
            if not retry_missing:
                design_content = retry_content
                rag_context = retry_state.get("rag_context", rag_context)
            else:
                print(f"[DesignAgent] WARNING: Repair also missing sections: {retry_missing} — using original output")

        return {
            "design_content": design_content,
            "rag_context": rag_context,
        }

    @staticmethod
    def _check_required_sections(content: str) -> list[str]:
        """Check if required top-level sections are present in the design document."""
        missing = []
        for pattern in DESIGN_REQUIRED_SECTIONS:
            if not re.search(pattern, content):
                missing.append(pattern)
        return missing
