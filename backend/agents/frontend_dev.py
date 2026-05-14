"""Frontend Agent - multi-turn frontend code generation with PATH/CODE parsing."""

import json
import re

from backend.agents.base import BaseAgent
from backend.core.prompts import FRONTEND_SYSTEM


MAX_FRONTEND_FILES = 50

KNOWN_EXTENSIONLESS = {
    "Dockerfile", "Makefile", ".env", ".env.example", ".gitignore", ".dockerignore",
}

FRONTEND_CONSTRAINTS = """
代码质量要求:
- 严格遵循指定的前端框架和语言，不要自行切换技术栈。
- 以后端 API 路由清单为准生成 API 客户端方法和类型定义，不要自创路由。
- 强制实现以下安全能力:
  XSS 防护（React 默认转义 + dangerouslySetInnerHTML 禁用 + DOMPurify 用于富文本）、
  CSRF Token（从 meta 标签或 cookie 读取，自动附加到 POST/PUT/DELETE 请求）、
  输入校验（表单提交前校验 + 后端错误响应展示）、
  认证状态管理（Token 存储 + 自动刷新 + 401 跳转登录页 + 403 展示无权限页面）、
  权限按钮控制（根据用户角色/权限隐藏或禁用按钮）。
- 所有 API 调用的请求体和响应体必须使用 TypeScript interface 明确类型定义。
- 敏感信息（Token、密码）不存储在 localStorage（使用 httpOnly cookie 或内存）。
- 组件遵循单一职责，容器组件负责数据获取，展示组件负责渲染。
- 错误边界捕获未处理的渲染异常并展示降级 UI。
- 不引入需求中未指定的重量级依赖（状态管理库、图表库等），除非设计文档明确要求。
"""


class FrontendAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="frontend",
            system_prompt=FRONTEND_SYSTEM,
            tools=["write_file", "read_file", "list_directory"],
            use_rag=True,
        )
        self._truncated_count = 0

    def generate(
        self,
        user_input: str,
        design_content: str = "",
        backend_api_summary: str = "",
        tech_stack: dict | None = None,
        output_dir: str = "",
    ):
        """Generate frontend files using tool-based file writing. Yields per-file for streaming."""
        self._output_dir = output_dir
        tech = self._normalize_tech_stack(tech_stack or {})
        rag_context = self._retrieve_context(
            design_content or user_input,
            framework_front=tech["framework_front"],
            frontend_language=tech["language"],
        )

        # Step 1: Module planning
        plan = self._plan_modules(
            design_content=design_content,
            backend_api_summary=backend_api_summary,
            rag_context=rag_context,
            frontend_language=tech["language"],
            framework_front=tech["framework_front"],
        )
        print(f"[FrontendAgent] Module plan: {plan}")

        # Step 2: Generate each file using write_file tool — yield per file
        total = len(plan)
        router_info = self._router_info(tech["framework_front"])
        comment_style = self._comment_style(tech["language"])

        generated: list[tuple[str, str]] = []
        for idx, path in enumerate(plan):
            finish_code_context = self._build_finish_code_context(generated)
            base_prompt = FRONTEND_SYSTEM.format(
                frontend_language=tech["language"],
                framework_front=tech["framework_front"],
                router_info=router_info,
                design_content=self._truncate_for_prompt(design_content, 3000) or "No design document was provided.",
                backend_api_summary=backend_api_summary or "No backend API summary was provided.",
                rag_context=self._truncate_for_prompt(rag_context, 1500) or "No relevant RAG context.",
                finish_code_context=finish_code_context or "No completed modules yet.",
                current_file=path,
                file_type_hint=self._file_type_hint(path, tech["framework_front"]),
                comment_style=comment_style,
            )
            prompt = base_prompt + "\n" + FRONTEND_CONSTRAINTS

            code = ""
            MAX_RETRIES = 2
            for attempt in range(MAX_RETRIES + 1):
                if attempt > 0:
                    prompt = base_prompt + "\n" + FRONTEND_CONSTRAINTS + (
                        f"\n\n上次输出未包含有效的 write_file 工具调用。"
                        f"请严格使用 write_file 工具写入 {path}。"
                    )

                response = self.llm.chat_sync(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=self._token_budget(path),
                )

                tool_calls = self._extract_tool_calls(response)
                code = self._process_tool_calls(tool_calls, path)

                if code:
                    break
                print(f"[FrontendAgent] retry {attempt+1}/{MAX_RETRIES} for {path}: no valid write_file tool call")

            if not code:
                code = f"// GENERATION_ERROR: LLM did not produce a valid write_file tool call for {path}\n"
                print(f"[FrontendAgent] FAILED to generate {path} after {MAX_RETRIES+1} attempts")

            generated.append((path, code))
            print(f"[FrontendAgent] generated {path} ({len(code)} chars)")

            yield {"type": "file", "path": path, "code": code, "index": idx, "total": total}

        content = self._format_frontend_artifact(generated)
        validation = self._validate_generated_files(generated, tech["framework_front"])
        yield {
            "type": "done",
            "frontend_code": content,
            "rag_context": rag_context,
            "files": [{"path": path, "code": code} for path, code in generated],
            "validation": validation,
            "tech_stack": tech,
        }

    def _retrieve_context(self, design_reference: str, framework_front: str, frontend_language: str) -> str:
        if not self.use_rag:
            return ""
        functional = self._extract_domain_keywords(design_reference)
        query = (
            f"{framework_front} {frontend_language} {functional} "
            f"frontend security auth routing API client "
            f"XSS CSRF permission controls {design_reference[:600]}"
        )
        result = self.rag.retrieve(
            query=query,
            top_k=5,
            kb_types=["template", "compliance", "pattern"],
            use_query_transform=True,
            use_rerank=True,
            use_self_rag=True,
            use_graph=False,
        )
        return result.get("context_string", "")

    @staticmethod
    def _router_info(framework_front: str) -> str:
        fw = framework_front.lower()
        if "next" in fw:
            return "Next.js App Router (文件系统路由)"
        if "vue" in fw:
            return "vue-router v4+"
        if "angular" in fw:
            return "Angular Router"
        return "react-router-dom v6+"

    @staticmethod
    def _comment_style(language: str) -> str:
        return "//"

    @staticmethod
    def _truncate_for_prompt(text: str, max_chars: int) -> str:
        text = text or ""
        if len(text) <= max_chars:
            return text
        sections = re.split(r"\n(?=## )", text)
        if len(sections) <= 1:
            head = int(max_chars * 0.6)
            return text[:head] + "\n...(truncated)...\n" + text[-(max_chars - head):]
        parts = []
        used = 0
        for sec in sections:
            if used + len(sec) <= max_chars:
                parts.append(sec)
                used += len(sec)
            else:
                break
        return "\n".join(parts)

    @staticmethod
    def _extract_domain_keywords(text: str) -> str:
        words = re.findall(
            r'[一-鿿]{2,6}(?:系统|平台|管理|监控|分析|日志|告警|认证|授权|审计|报表|通知|流程|配置|检测|扫描|防护|面板|页面|仪表)?',
            text[:500]
        )
        stops = {"系统", "平台", "管理", "可以", "需要", "一个", "这个", "所有", "进行", "使用",
                 "我们", "他们", "什么", "怎么", "如何", "是否", "已经", "没有"}
        seen = set()
        unique = []
        for w in words:
            if w.lower() not in stops and w not in seen:
                seen.add(w)
                unique.append(w)
        return " ".join(unique[:5])

    def _file_type_hint(self, file_path: str, framework_front: str) -> str:
        path_lower = file_path.lower()
        hints = []

        if any(kw in path_lower for kw in ["app.tsx", "app.jsx", "main.tsx", "main.jsx", "_app.", "layout.tsx"]):
            hints.append(("入口文件",
                "这是应用入口。需要: 1) 包裹全局 Provider（Auth/Router）2) 导入全局样式"
                "3) 渲染根路由组件"))

        if any(kw in path_lower for kw in ["api", "client", "http", "axios", "fetch"]):
            hints.append(("API 客户端",
                "这是 API 客户端。需要: 1) 统一拦截器注入认证 Token 2) 401 自动跳转登录"
                "3) 请求/响应类型定义 4) 超时和错误处理"))

        if any(kw in path_lower for kw in ["type", "interface"]):
            hints.append(("类型定义",
                "这是共享类型定义。所有 API 请求体和响应体 interface 定义在此。"
                "确保与后端 API 契约的字段名完全一致。"))

        if any(kw in path_lower for kw in ["auth", "login", "permission", "role"]):
            hints.append(("认证/权限",
                "这是认证和权限模块。需要: 1) Token 存储和自动刷新"
                "2) 登录/登出流程 3) 路由守卫 4) 权限按钮控制"))

        if any(kw in path_lower for kw in ["route", "router", "approuter"]):
            hints.append(("路由配置",
                "这是路由配置。需要: 1) 所有页面路径定义 2) 懒加载"
                "3) 需要认证的路由包裹 AuthGuard 4) 404 处理"))

        if any(kw in path_lower for kw in ["page", "view", "screen", "dashboard"]):
            hints.append(("页面组件",
                "这是页面级组件。需要: 1) 数据获取逻辑 2) 加载/空/错误状态处理"
                "3) 响应式布局 4) 权限检查"))

        if any(kw in path_lower for kw in ["component", "widget", "shared", "common"]):
            hints.append(("通用组件",
                "这是可复用组件。需要: 1) Props 类型定义 2) 无副作用（纯展示）"
                "3) 可访问性（aria 属性、键盘导航）"))

        if any(kw in path_lower for kw in ["hook", "usecustom", "use-"]):
            hints.append(("自定义 Hook",
                "这是自定义 Hook。需要: 1) 清晰的返回值类型 2) 处理 cleanup"
                "3) 错误边界考虑"))

        if any(kw in path_lower for kw in ["package.json", "tsconfig", "vite.config", "next.config"]):
            hints.append(("项目配置",
                "这是项目配置文件。需要: 1) 所有依赖的精确版本"
                "2) TypeScript 严格模式 3) 构建和开发脚本"))

        if not hints:
            return ""
        return "文件类型指引:\n" + "\n".join(f"- [{label}] {guidance}" for label, guidance in hints)

    def _extract_signatures(self, generated: list[tuple[str, str]]) -> str:
        lines_out = []
        for path, code in generated:
            sigs = []
            for line in code.split("\n"):
                stripped = line.strip()
                if re.match(r"^(export\s+)?(interface|type|enum)\s+\w+", stripped):
                    sigs.append(stripped.rstrip("{"))
                elif re.match(r"^(export\s+)?(const|let|var)\s+\w+\s*[:=]", stripped):
                    sigs.append(stripped)
                elif re.match(r"^(export\s+)?(function|class)\s+\w+", stripped):
                    sigs.append(stripped.rstrip("{"))
                elif re.match(r"^(export\s+)?(async\s+)?\w+\s*\(.*\)\s*[:{]", stripped):
                    sigs.append(stripped.rstrip("{"))
            if sigs:
                lines_out.append(f"// {path}\n" + "\n".join(sigs[:30]))
        return "\n".join(lines_out[:200])

    def _plan_modules(
        self,
        design_content: str,
        backend_api_summary: str,
        rag_context: str,
        frontend_language: str,
        framework_front: str,
    ) -> list[str]:
        """LLM plans frontend modules and returns file list."""
        prompt = f"""你是一个资深{framework_front} + {frontend_language}前端开发工程师。分析设计文档和后端API契约，规划需要生成的前端模块。

输出每个模块的文件路径，每行一个，按依赖顺序排列。
不要输出编号、解释或 Markdown。

设计文档:
{self._truncate_for_prompt(design_content, 3000) or "无"}

后端 API 契约:
{backend_api_summary or "无"}

模块文件列表（每行一个路径，最多{MAX_FRONTEND_FILES}个）:"""

        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048,
        )
        files = self._parse_file_plan(response)
        files = self._validate_file_plan(files, framework_front)
        return files if files else self._fallback_files(framework_front)

    def _process_tool_calls(self, tool_calls: list[dict], expected_path: str) -> str:
        """Execute write_file tool calls and return the code for the expected path."""
        import os as _os
        code = ""
        output_dir = getattr(self, "_output_dir", "")
        for call in tool_calls:
            if call.get("name") != "write_file":
                continue
            args = call.get("arguments", {})
            file_path = args.get("file_path", "")
            content = args.get("content", "")
            if file_path and content:
                full_path = _os.path.join(output_dir, file_path) if output_dir else file_path
                from backend.core.tools import write_file
                result = write_file(full_path, content)
                print(f"[FrontendAgent] Tool write_file: {full_path} ({len(content)} chars)")
                if (not code) or (file_path == expected_path) or \
                   file_path.endswith(expected_path.split("/")[-1]):
                    code = content
        return code

    def _parse_file_plan(self, response: str) -> list[str]:
        files: list[str] = []
        seen = set()
        for raw_line in response.replace("\r\n", "\n").split("\n"):
            line = raw_line.strip().strip("`").strip()
            line = re.sub(r"^\s*[-*]\s+", "", line)
            line = re.sub(r"^\s*\d+[\.)]\s+", "", line)
            line = line.replace("\\", "/").lstrip("/")
            if not line:
                continue
            basename = line.split("/")[-1]
            if basename not in KNOWN_EXTENSIONLESS and (" " in line or "." not in line):
                continue
            if line in seen:
                continue
            seen.add(line)
            files.append(line)
        return files

    def _validate_file_plan(self, files: list[str], framework_front: str) -> list[str]:
        valid: list[str] = []
        seen = set()
        self._truncated_count = 0
        for path in files:
            sanitized = self._sanitize_generated_path(path)
            if not sanitized or sanitized in seen:
                continue
            seen.add(sanitized)
            if len(valid) >= MAX_FRONTEND_FILES:
                self._truncated_count += 1
                continue
            valid.append(sanitized)

        if self._truncated_count > 0:
            print(f"[FrontendAgent] WARNING: {self._truncated_count} files dropped "
                  f"from plan (MAX_FRONTEND_FILES={MAX_FRONTEND_FILES})")

        for required in self._required_files(framework_front):
            if required not in seen and len(valid) < MAX_FRONTEND_FILES:
                valid.insert(0, required)
                seen.add(required)
        return valid[:MAX_FRONTEND_FILES]

    def _sanitize_generated_path(self, path: str, default_path: str | None = None) -> str:
        import os
        normalized = (path or "").strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = re.sub(r"/{2,}", "/", normalized)
        normalized = normalized.lstrip("/")
        if not normalized:
            return default_path or ""
        if normalized.startswith("..") or normalized.startswith("~"):
            return default_path or ""
        if re.match(r"^[a-zA-Z]:", normalized):
            return default_path or ""
        if not re.match(r"^[A-Za-z0-9_./@+\-]+$", normalized):
            return default_path or ""
        if os.path.isabs(normalized) or ".." in normalized.split("/"):
            return default_path or ""
        return normalized

    def _format_frontend_artifact(self, generated: list[tuple[str, str]]) -> str:
        return "\n\n".join(
            f"PATH：{path}\nCODE：\n{code.rstrip()}\n" for path, code in generated
        )

    def _build_finish_code_context(self, generated: list[tuple[str, str]]) -> str:
        if not generated:
            return ""

        parts: list[str] = []
        parts.append(f"Generated files ({len(generated)}): {', '.join(path for path, _ in generated)}")

        signatures = self._extract_signatures(generated)
        if signatures:
            parts.append(f"Module interfaces:\n{signatures}")

        parts.append("Recent module code:")
        for path, code in generated[-10:]:
            parts.append(f"PATH：{path}\nCODE：\n{code[:2000]}")

        return "\n\n".join(parts)

    def _normalize_tech_stack(self, tech_stack: dict) -> dict[str, str]:
        framework_front = self._stack_value(tech_stack.get("framework_front")) or "React"
        language = self._frontend_language(tech_stack.get("language"), framework_front)
        return {
            "language": language,
            "framework_front": framework_front,
        }

    def _frontend_language(self, language_value, framework_front: str) -> str:
        language = self._stack_value(language_value)
        if language and language.lower() in {"javascript", "typescript"}:
            return language
        if framework_front.lower() in {"react", "vue", "angular", "next.js", "nextjs", "svelte"}:
            return "TypeScript"
        return language or "TypeScript"

    def _stack_value(self, value) -> str:
        text = str(value or "").strip()
        empty_values = {"", "{}", "[]", "无", "未指定", "null", "none", "None", "Unspecified"}
        return "" if text in empty_values else text

    def _required_files(self, framework_front: str) -> list[str]:
        text = framework_front.lower()
        if "next" in text:
            return ["frontend/package.json", "frontend/tsconfig.json", "frontend/next.config.js"]
        if "vue" in text:
            return ["frontend/package.json", "frontend/tsconfig.json", "frontend/vite.config.ts"]
        if "angular" in text:
            return ["frontend/package.json", "frontend/tsconfig.json", "frontend/angular.json"]
        if "svelte" in text:
            return ["frontend/package.json", "frontend/vite.config.ts"]
        if "nuxt" in text:
            return ["frontend/package.json", "frontend/nuxt.config.ts"]
        return ["frontend/package.json", "frontend/tsconfig.json", "frontend/vite.config.ts"]

    def _fallback_files(self, framework_front: str) -> list[str]:
        text = framework_front.lower()
        if "next" in text:
            return [
                "frontend/package.json", "frontend/tsconfig.json", "frontend/next.config.js",
                "frontend/app/layout.tsx", "frontend/app/page.tsx",
                "frontend/src/api/client.ts", "frontend/src/types/api.ts",
            ]
        if "vue" in text:
            return [
                "frontend/package.json", "frontend/tsconfig.json", "frontend/vite.config.ts",
                "frontend/src/main.ts", "frontend/src/App.vue",
                "frontend/src/api/client.ts", "frontend/src/types/api.ts",
                "frontend/src/router/index.ts", "frontend/src/stores/auth.ts",
            ]
        if "angular" in text:
            return [
                "frontend/package.json", "frontend/tsconfig.json", "frontend/angular.json",
                "frontend/src/main.ts", "frontend/src/app/app.component.ts",
                "frontend/src/app/app-routing.module.ts",
                "frontend/src/app/services/api.service.ts", "frontend/src/app/types/api.ts",
            ]
        return [
            "frontend/package.json", "frontend/tsconfig.json", "frontend/vite.config.ts",
            "frontend/src/main.tsx", "frontend/src/App.tsx",
            "frontend/src/api/client.ts", "frontend/src/types/api.ts",
            "frontend/src/auth/AuthProvider.tsx", "frontend/src/routes/AppRoutes.tsx",
        ]

    def _validate_generated_files(self, generated: list[tuple[str, str]], framework_front: str) -> dict:
        paths = {path for path, _ in generated}
        issues = []
        for required in self._required_files(framework_front):
            if required not in paths:
                issues.append(f"Missing expected file: {required}")

        for path, code in generated:
            if not code.strip():
                issues.append(f"{path}: empty file")
                continue
            if "GENERATION_ERROR" in code:
                issues.append(f"{path}: PATH/CODE format not followed by LLM")
                continue
            if path.endswith((".ts", ".tsx", ".js", ".jsx")):
                # Basic structural checks
                if code.count("{") != code.count("}"):
                    issues.append(f"{path}: unbalanced braces")
                if code.count("(") != code.count(")"):
                    issues.append(f"{path}: unbalanced parentheses")
                # TypeScript type safety
                if path.endswith((".ts", ".tsx")) and re.search(r'\bany\b', code) and "eslint-disable" not in code:
                    issues.append(f"{path}: contains TypeScript 'any' type — review type safety")
                # Component file should have an export
                if any(kw in path.lower() for kw in ["component", "page", "app.tsx", "layout"]):
                    if not re.search(r"export\s+(default\s+)?(function|class|const)", code):
                        issues.append(f"{path}: component file missing exported component")

        return {
            "ok": not issues,
            "issues": issues,
            "file_count": len(generated),
        }

    def _token_budget(self, file_path: str) -> int:
        large_markers = ["app", "page", "layout", "client", "provider", "routes", "store",
                        "dashboard", "component", "hook", "middleware", "guard", "service"]
        return 24576 if any(marker in file_path.lower() for marker in large_markers) else 16384
