"""Orchestrator Agent — 任务分解 + 流水线调度 + 检索策略决策"""

import json
from langgraph.graph import StateGraph, END

from backend.core.llm import get_llm
from backend.core.prompts import ORCHESTRATOR_SYSTEM
from backend.rag import get_rag_pipeline

# Define pipeline stages
ALL_STAGES = [
    "research",
    "prd",
    "design",
    "database",
    "backend",
    "frontend",
    "review",
    "deploy",
]


class OrchestratorAgent:
    """主编排器 — 决定哪些阶段需要执行、检索什么知识库"""

    def __init__(self):
        self.llm = get_llm()
        self.rag = get_rag_pipeline()

    def plan(self, user_input: str, project_context: str = "") -> dict:
        """Analyze user input and create an execution plan."""
        prompt = ORCHESTRATOR_SYSTEM.format(
            user_input=user_input,
            project_context=project_context or "新项目，无历史上下文",
        )

        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1024,
        )

        # Extract JSON plan
        plan = self._parse_plan(response)
        return plan

    def _parse_plan(self, response: str) -> dict:
        """Parse LLM response to extract execution plan. Uses regex for robust JSON extraction."""
        import re

        # Strategy 1: extract ```json ... ``` code block
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
        else:
            # Strategy 2: find first { ... } pair (largest valid JSON object)
            brace_start = response.find('{')
            brace_end = response.rfind('}')
            if brace_start >= 0 and brace_end > brace_start:
                json_str = response[brace_start:brace_end + 1].strip()
            else:
                json_str = response.strip()

        try:
            plan = json.loads(json_str)
            stages_raw = plan.get("stages", [])
            # Normalize: stages can be list of strings or list of dicts with "stage" key
            if stages_raw and isinstance(stages_raw[0], dict):
                stages = [s.get("stage", s.get("name", str(s))) for s in stages_raw]
            else:
                stages = stages_raw
            return {"stages": stages, "summary": plan.get("summary", "")}
        except (json.JSONDecodeError, KeyError, AttributeError):
            return {"stages": ALL_STAGES, "summary": "自动分解为全流程开发"}

    def get_rag_strategy(self, stage: str) -> dict:
        """Determine RAG retrieval strategy for each stage."""
        strategies = {
            "research": {
                "kb_types": ["compliance", "pattern", "template"],
                "query_prefix": "网络安全 ",
                "use_graph": True,
            },
            "prd": {
                "kb_types": ["compliance"],
                "query_prefix": "需求文档 安全要求 ",
                "use_graph": False,
            },
            "design": {
                "kb_types": ["pattern", "compliance"],
                "query_prefix": "系统架构 网络安全 ",
                "use_graph": True,
            },
            "database": {
                "kb_types": ["schema", "compliance"],
                "query_prefix": "数据库设计 ",
                "use_graph": False,
            },
            "backend": {
                "kb_types": ["template"],
                "query_prefix": "FastAPI 安全中间件 ",
                "use_graph": False,
            },
            "frontend": {
                "kb_types": ["template"],
                "query_prefix": "React 安全组件 ",
                "use_graph": False,
            },
            "review": {
                "kb_types": ["compliance", "pattern"],
                "query_prefix": "安全审计 OWASP 等保 ",
                "use_graph": True,
            },
            "deploy": {
                "kb_types": ["deploy", "compliance"],
                "query_prefix": "Docker 安全部署 ",
                "use_graph": False,
            },
        }
        return strategies.get(stage, {"kb_types": None, "query_prefix": "", "use_graph": False})

    def run_pipeline_plan(self, user_input: str, project_context: str = "") -> dict:
        """Generate a complete pipeline plan with RAG strategies."""
        plan = self.plan(user_input, project_context)
        stages = plan.get("stages", ALL_STAGES)

        stage_plans = []
        for stage in stages:
            strategy = self.get_rag_strategy(stage)
            stage_plans.append({
                "stage": stage,
                "kb_types": strategy["kb_types"],
                "rag_query": strategy["query_prefix"] + user_input[:200],
            })

        return {
            "summary": plan.get("summary", ""),
            "stages": stage_plans,
            "user_input": user_input,
        }
