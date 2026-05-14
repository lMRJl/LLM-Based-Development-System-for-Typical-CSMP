"""Agent Pipeline - unified linear pipeline execution entry."""

import json
import re
import time

from backend.agents.orchestrator import OrchestratorAgent
from backend.agents.research_agent import ResearchAgent
from backend.agents.prd_agent import PRDAgent
from backend.agents.design_agent import DesignAgent
from backend.agents.database_agent import DatabaseAgent
from backend.agents.backend_dev import BackendAgent
from backend.agents.frontend_dev import FrontendAgent
from backend.agents.reviewer import ReviewerAgent
from backend.agents.deploy_agent import DeployAgent
from backend.core.llm import get_llm


class AgentPipeline:
    """Full development Agent pipeline."""

    def __init__(self):
        self.orchestrator = OrchestratorAgent()
        self.research = ResearchAgent()
        self.prd = PRDAgent()
        self.design = DesignAgent()
        self.database = DatabaseAgent()
        self.backend = BackendAgent()
        self.frontend = FrontendAgent()
        self.reviewer = ReviewerAgent()
        self.deploy = DeployAgent()
        self.llm = get_llm()

        self.stage_agents = {
            "research": self.research,
            "prd": self.prd,
            "design": self.design,
            "database": self.database,
            "backend": self.backend,
            "frontend": self.frontend,
            "review": self.reviewer,
            "deploy": self.deploy,
        }

    def prepare_pipeline_input(self, user_input: str) -> str:
        """Summarize long user input once for the linear generation pipeline."""
        text = (user_input or "").strip()
        if len(text) <= 1800:
            return text

        prompt = f"""请将下面的用户原始需求压缩成一份用于软件开发线性流水线的需求摘要。
要求:
- 保留业务目标、用户角色、核心功能、权限/安全/合规要求、数据对象、接口/集成、技术偏好、非功能要求和验收标准。
- 删除重复描述、寒暄、背景噪音和与开发无关的内容。
- 不要扩写或新增需求。
- 控制在 1200 字以内，使用结构化条目。

用户原始需求:
{text}

需求摘要:
"""
        return self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048,
        ).strip() or text[:1800]

    def extract_tech_stack(self, user_input: str) -> dict[str, str]:
        """Extract technology stack from raw user input as an independent key-value dict."""
        prompt = f"""请从下面的用户原始需求中提取技术栈，只输出固定四行键值对。
如果用户没有明确指定某项，值填写“未指定”。不要推断、不要解释、不要输出 Markdown。

输出格式必须严格如下:
language:{{}}
framework_front:{{}}
framework_back:{{}}
database:{{}}

字段含义:
- language: 明确指定的主要编程语言；如果前后端语言不同，优先填写后端语言。
- framework_front: 前端框架，例如 React、Vue、Angular、Next.js。
- framework_back: 后端框架，例如 FastAPI、Spring Boot、Gin、NestJS、Express。
- database: 数据库，例如 PostgreSQL、MySQL、SQLite、MongoDB。

用户原始需求:
{user_input}
"""
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
        )
        return self._parse_tech_stack(response)

    def _parse_tech_stack(self, text: str) -> dict[str, str]:
        stack = {
            "language": "未指定",
            "framework_front": "未指定",
            "framework_back": "未指定",
            "database": "未指定",
        }
        raw = (text or "").strip()
        if not raw:
            return stack

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for key in stack:
                    value = str(parsed.get(key, "")).strip()
                    if value:
                        stack[key] = value
                return stack
        except json.JSONDecodeError:
            pass

        for key in stack:
            match = re.search(rf"^\s*{key}\s*[:：]\s*(.*?)\s*$", raw, re.MULTILINE | re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                stack[key] = value or "未指定"
        return stack

    def execute(
        self,
        user_input: str,
        project_context: str = "",
        input_summarized: bool = False,
        tech_stack: dict | None = None,
    ) -> list[dict]:
        return list(self.iter_execute(user_input, project_context, input_summarized, tech_stack))

    def iter_execute(
        self,
        user_input: str,
        project_context: str = "",
        input_summarized: bool = False,
        tech_stack: dict | None = None,
        plan: dict | None = None,
        resume_ctx: dict | None = None,
        start_from_stage: str | None = None,
        output_dir: str = "",
    ):
        """Execute the pipeline stage by stage. Supports resume via resume_ctx + start_from_stage."""
        pipeline_input = user_input if input_summarized else self.prepare_pipeline_input(user_input)
        plan = plan or self.orchestrator.run_pipeline_plan(pipeline_input, project_context)
        stages = plan.get("stages", [])

        if resume_ctx:
            ctx = resume_ctx
            ctx.setdefault("generated_files", {})
            ctx.setdefault("validations", {})
            ctx["_completed_stages"] = list(ctx.get("completed_stages", []))
        else:
            ctx = {
                "pipeline_input": pipeline_input,
                "tech_stack": tech_stack or self.extract_tech_stack(user_input),
                "generated_files": {},
                "validations": {},
                "_completed_stages": [],
            }

        stage_titles = {
            "research": "深度研究",
            "prd": "产品需求文档(PRD)",
            "design": "概要设计",
            "database": "数据库设计",
            "backend": "后端代码",
            "frontend": "前端代码",
            "review": "代码审查报告",
            "deploy": "部署配置",
        }

        # Build skip-set from completed stages
        completed_set = set(ctx.get("_completed_stages", []))
        skip_until_found = start_from_stage is not None

        for stage_info in stages:
            name = stage_info["stage"]

            # Skip already-completed stages when resuming
            if skip_until_found:
                if name != start_from_stage and name in completed_set:
                    print(f"[AgentPipeline] skipping completed stage: {name}")
                    stage_payload = {
                        "stage": name, "title": stage_titles.get(name, name),
                        "content": f"(Resumed — previously completed)", "rag_context": "",
                        "status": "completed", "duration_seconds": 0,
                    }
                    yield stage_payload
                    continue
                elif name == start_from_stage:
                    skip_until_found = False

            agent = self.stage_agents.get(name)
            if not agent:
                continue

            started_at = time.perf_counter()
            try:
                if name == "research":
                    r = agent.research(pipeline_input, name, kb_types=stage_info.get("kb_types"))
                    content, rag = r.get("summary", ""), r.get("context_string", "")
                elif name == "prd":
                    r = agent.generate(pipeline_input)
                    content, rag = r.get("prd_content", ""), r.get("rag_context", "")
                    ctx["prd"] = content
                elif name == "design":
                    r = agent.generate(
                        pipeline_input,
                        prd_content=ctx.get("prd", ""),
                        tech_stack=ctx["tech_stack"],
                    )
                    content, rag = r.get("design_content", ""), r.get("rag_context", "")
                    ctx["design"] = content
                elif name == "database":
                    r = agent.generate(
                        pipeline_input,
                        design_content=ctx.get("design", ""),
                        database=ctx["tech_stack"].get("database", ""),
                    )
                    content, rag = r.get("database_content", ""), r.get("rag_context", "")
                    sql_content = r.get("sql_content", "")
                    ctx["database"] = content
                    ctx["database_sql"] = sql_content
                elif name == "backend":
                    content, rag, files, validation = "", "", [], {}
                    for item in agent.generate(
                        pipeline_input,
                        design_content=ctx.get("design", ""),
                        database_content=ctx.get("database", ""),
                        tech_stack=ctx["tech_stack"],
                        output_dir=output_dir,
                    ):
                        if item.get("type") == "file":
                            yield {"stage": "backend", "type": "file_chunk",
                                   "path": item["path"], "code": item.get("code", ""),
                                   "index": item.get("index", 0), "total": item.get("total", 0)}
                        elif item.get("type") == "done":
                            content = item.get("backend_code", "")
                            rag = item.get("rag_context", "")
                            files = item.get("files", [])
                            validation = item.get("validation", {})
                    ctx["backend"] = content
                    ctx["generated_files"]["backend"] = files
                    ctx["validations"]["backend"] = validation
                    ctx["backend_api_list"] = self._extract_api_routes(content)
                elif name == "frontend":
                    backend_api = ctx.get("backend_api_list", ctx.get("backend", "")[:500])
                    content, rag, files, validation = "", "", [], {}
                    for item in agent.generate(
                        pipeline_input,
                        design_content=ctx.get("design", ""),
                        backend_api_summary=backend_api,
                        tech_stack=ctx["tech_stack"],
                        output_dir=output_dir,
                    ):
                        if item.get("type") == "file":
                            yield {"stage": "frontend", "type": "file_chunk",
                                   "path": item["path"], "code": item.get("code", ""),
                                   "index": item.get("index", 0), "total": item.get("total", 0)}
                        elif item.get("type") == "done":
                            content = item.get("frontend_code", "")
                            rag = item.get("rag_context", "")
                            files = item.get("files", [])
                            validation = item.get("validation", {})
                    ctx["frontend"] = content
                    ctx["generated_files"]["frontend"] = files
                    ctx["validations"]["frontend"] = validation
                elif name == "review":
                    artifacts = self._assemble_artifacts(ctx)
                    r = agent.review(
                        pipeline_input,
                        artifacts_to_review=artifacts,
                        generated_files=ctx.get("generated_files", {}),
                        validations=ctx.get("validations", {}),
                        tech_stack=ctx.get("tech_stack", {}),
                    )
                    content, rag = r.get("review_report", ""), r.get("rag_context", "")
                    files = r.get("files", [])
                    findings = r.get("findings", [])
                    if files:
                        self._merge_repaired_files(ctx, files)
                elif name == "deploy":
                    summary = self._project_summary(pipeline_input, ctx)
                    r = agent.generate(pipeline_input, project_summary=summary)
                    content, rag = r.get("deploy_config", ""), r.get("rag_context", "")
                else:
                    content, rag = "", ""

                stage_payload = {
                    "stage": name,
                    "title": stage_titles.get(name, name),
                    "content": content,
                    "rag_context": rag,
                    "status": "completed",
                    "duration_seconds": round(time.perf_counter() - started_at, 3),
                }
                if name == "database" and sql_content:
                    stage_payload["sql_content"] = sql_content
                print(f"[AgentPipeline] stage={name} status=completed duration={stage_payload['duration_seconds']}s")
                if name in {"backend", "frontend"} and files:
                    stage_payload["files"] = files
                if name in {"backend", "frontend"} and validation:
                    stage_payload["validation"] = validation
                if name == "review" and findings:
                    stage_payload["findings"] = findings
                if name == "review" and files:
                    stage_payload["files"] = files
                ctx["_completed_stages"].append(name)
                self._last_ctx = ctx  # expose for context persistence
                yield stage_payload
            except Exception as e:
                duration_seconds = round(time.perf_counter() - started_at, 3)
                print(f"[AgentPipeline] stage={name} status=failed duration={duration_seconds}s error={e}")
                yield {
                    "stage": name,
                    "title": stage_titles.get(name, name),
                    "content": f"Error: {e}",
                    "rag_context": "",
                    "status": "failed",
                    "duration_seconds": duration_seconds,
                }

    def _extract_api_routes(self, backend_code: str) -> str:
        """Extract API route definitions from generated backend output for Frontend Agent reference."""
        routes: list[str] = []

        def add(method: str, path: str):
            path = path.strip()
            if not path:
                return
            routes.append(f"{method.upper()} {path}")

        for m in re.finditer(r'@router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', backend_code):
            add(m.group(1), m.group(2))

        for m in re.finditer(r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\s*\(\s*["\']([^"\']+)["\']', backend_code):
            method = m.group(1).replace("Mapping", "").replace("Get", "GET").replace("Post", "POST").replace("Put", "PUT").replace("Delete", "DELETE").replace("Patch", "PATCH")
            add(method, m.group(2))

        for m in re.finditer(r'@\w+\.(Get|Post|Put|Delete|Patch)\s*\(\s*["\']([^"\']+)["\']', backend_code):
            add(m.group(1), m.group(2))

        for m in re.finditer(r'@(Get|Post|Put|Delete|Patch)\s*\(\s*["\']([^"\']+)["\']', backend_code):
            add(m.group(1), m.group(2))

        for m in re.finditer(r'\b(router|app|group)\.(GET|POST|PUT|DELETE|PATCH|get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', backend_code):
            add(m.group(2), m.group(3))

        seen = set()
        unique = []
        for route in routes:
            if route not in seen:
                seen.add(route)
                unique.append(route)
        summary = "\n".join(unique[:30])
        return summary or backend_code.split("===FILE")[0][:500]

    def _assemble_artifacts(self, ctx: dict) -> str:
        parts = []
        labels = [
            ("prd", "PRD", 3000),
            ("design", "概要设计", 3000),
            ("database", "数据库设计", 3000),
            ("backend", "后端代码", 4000),
            ("frontend", "前端代码", 4000),
        ]
        for key, label, limit in labels:
            if key in ctx:
                content = ctx[key]
                if len(content) > limit:
                    # Section-aware truncation for design docs
                    if key in ("prd", "design", "database"):
                        sections = re.split(r"\n(?=## )", content)
                        truncated = ""
                        for sec in sections:
                            if len(truncated) + len(sec) <= limit:
                                truncated += sec
                            else:
                                break
                        content = truncated if len(truncated) >= 200 else content[:limit]
                    else:
                        content = content[:limit]
                parts.append(f"=== {label} ===\n{content}")
        return "\n\n".join(parts)

    def _merge_repaired_files(self, ctx: dict, repaired_files: list[dict]) -> None:
        """Merge reviewer repaired files back into pipeline context for downstream stages."""
        for item in repaired_files:
            path = str(item.get("path", ""))
            module = "backend" if path.startswith("backend/") else "frontend" if path.startswith("frontend/") else ""
            if not module:
                continue

            files = ctx.setdefault("generated_files", {}).setdefault(module, [])
            replaced = False
            for existing in files:
                if existing.get("path") == path:
                    existing["code"] = item.get("code", "")
                    replaced = True
                    break
            if not replaced:
                files.append({"path": path, "code": item.get("code", "")})

            ctx[module] = self._format_files_artifact(files)

    def _format_files_artifact(self, files: list[dict]) -> str:
        return "\n\n".join(
            f"PATH：{item.get('path', '')}\nCODE：\n{str(item.get('code', '')).rstrip()}\n"
            for item in files
            if item.get("path")
        )

    def _project_summary(self, user_input: str, ctx: dict) -> str:
        parts = [f"用户需求: {user_input}"]
        if ctx.get("tech_stack"):
            parts.append(f"技术栈: {ctx['tech_stack']}")
        for key, label in [("design", "架构"), ("database", "数据库"), ("backend", "后端"), ("frontend", "前端")]:
            if key in ctx:
                parts.append(f"{label}: {ctx[key][:500]}")
        return "\n".join(parts)
