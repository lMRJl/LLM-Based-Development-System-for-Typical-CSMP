"""Backend Agent - multi-turn backend code generation with PATH/CODE parsing."""

import ast
import re

from backend.agents.base import BaseAgent
from backend.core.prompts import BACKEND_SYSTEM


MAX_BACKEND_FILES = 80

KNOWN_EXTENSIONLESS = {
    "Dockerfile", "Makefile", "Procfile", "Vagrantfile",
    ".env", ".env.example", ".gitignore", ".dockerignore",
}

BACKEND_CONSTRAINTS = """
代码质量要求:
- 严格遵循指定的后端语言、框架和数据库，不要自行切换技术栈。
- 所有生成的模块必须可独立运行，包含必要的 import、类型注解、异常处理和日志。
- 强制实现以下安全平台通用能力:
  JWT 认证（access/refresh token 双令牌模式）、RBAC 权限控制（角色-权限-资源三级）、
  审计日志（操作人、时间、IP、操作类型、结果、详情）、安全响应头、
  输入校验、统一异常处理（全局 exception handler，返回 {code, message, detail} 结构）。
- 敏感配置通过环境变量读取，不硬编码。
- 日志使用标准库，区分 INFO/WARN/ERROR 级别。
- HTTP 状态码严格遵循 REST 语义。
- 与已完成模块保持接口一致: 相同的 import 路径、类名、函数签名、字段类型。
- 不引入需求中未指定的重量级依赖。
- 密码使用安全哈希存储，不存明文；Token/密钥不在日志中输出。
"""


class BackendAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="backend",
            system_prompt=BACKEND_SYSTEM,
            tools=["write_file", "read_file", "list_directory"],
            use_rag=True,
        )

    def generate(
        self,
        user_input: str,
        design_content: str = "",
        database_content: str = "",
        tech_stack: dict | None = None,
        output_dir: str = "",
    ):
        """Generate backend files using tool-based file writing. Yields per-file for streaming."""
        self._output_dir = output_dir

        tech = self._normalize_tech_stack(tech_stack or {}, design_content or user_input)
        rag_context = self._retrieve_context(
            design_content or user_input,
            language=tech["language"],
            framework_back=tech["framework_back"],
            database=tech["database"],
        )

        # Step 1: Module planning
        plan = self._plan_modules(
            design_content=design_content,
            database_content=database_content,
            rag_context=rag_context,
            language=tech["language"],
            framework_back=tech["framework_back"],
        )
        print(f"[BackendAgent] Module plan: {plan}")

        # Step 2: Generate each file using write_file tool — yield per file
        generated: list[tuple[str, str]] = []
        total = len(plan)
        for idx, path in enumerate(plan):
            finish_code_context = self._build_finish_code_context(generated)
            base_prompt = BACKEND_SYSTEM.format(
                language=tech["language"],
                framework_back=tech["framework_back"],
                database=tech["database"],
                design_content=self._truncate_for_prompt(design_content, 3000) or "No design document was provided.",
                database_content=self._truncate_for_prompt(database_content, 3000) or "No database design was provided. Fill required persistence details from the design document.",
                rag_context=self._truncate_for_prompt(rag_context, 1500) or "No relevant RAG context.",
                finish_code_context=finish_code_context or "No completed modules yet.",
                current_file=path,
                file_type_hint=self._file_type_hint(path, tech["language"]),
                comment_style=self._comment_style(tech["language"]),
            )
            prompt = base_prompt + "\n" + BACKEND_CONSTRAINTS

            code = ""
            MAX_RETRIES = 2
            for attempt in range(MAX_RETRIES + 1):
                if attempt > 0:
                    prompt = base_prompt + "\n" + BACKEND_CONSTRAINTS + (
                        f"\n\n上次输出未包含有效的 write_file 工具调用。"
                        f"请严格使用 write_file 工具写入 {path}。"
                    )

                response = self.llm.chat_sync(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=self._token_budget(path),
                )

                # Extract tool call from response
                tool_calls = self._extract_tool_calls(response)
                code = self._process_tool_calls(tool_calls, path)

                if code:
                    break
                print(f"[BackendAgent] retry {attempt+1}/{MAX_RETRIES} for {path}: no valid write_file tool call")

            if not code:
                code = f"# GENERATION_ERROR: LLM did not produce a valid write_file tool call for {path}\n"
                print(f"[BackendAgent] FAILED to generate {path} after {MAX_RETRIES+1} attempts")

            generated.append((path, code))
            print(f"[BackendAgent] generated {path} ({len(code)} chars)")

            yield {"type": "file", "path": path, "code": code, "index": idx, "total": total}

        # Step 3: Assemble and validate — yield final result
        content = self._format_backend_artifact(generated)
        validation = self._validate_generated_files(generated, tech["language"], tech["framework_back"])
        yield {
            "type": "done",
            "backend_code": content,
            "rag_context": rag_context,
            "files": [{"path": p, "code": c} for p, c in generated],
            "tech_stack": tech,
            "validation": validation,
        }

    def _plan_modules(
        self,
        design_content: str,
        database_content: str,
        rag_context: str,
        language: str,
        framework_back: str,
    ) -> list[str]:
        """LLM plans backend modules and returns file list."""
        prompt = f"""你是一个资深{language}后端开发工程师。分析设计文档和数据库设计，规划需要生成的后端模块。

输出每个后端模块的文件路径，每行一个，按依赖顺序排列。
不要输出编号、解释或 Markdown。

设计文档:
{self._truncate_for_prompt(design_content, 3000) or "无"}

数据库设计:
{self._truncate_for_prompt(database_content, 3000) or "无"}

后端模块文件列表（每行一个路径，最多{MAX_BACKEND_FILES}个）:"""

        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048,
        )
        files = self._parse_file_plan(response)
        files = self._validate_file_plan(files, language, framework_back)
        return files if files else self._fallback_files(language, framework_back)

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
                # Prepend output_dir to write to projects/{id}/ directory
                full_path = _os.path.join(output_dir, file_path) if output_dir else file_path
                from backend.core.tools import write_file
                result = write_file(full_path, content)
                print(f"[BackendAgent] Tool write_file: {full_path} ({len(content)} chars)")
                if (not code) or (file_path == expected_path) or \
                   file_path.endswith(expected_path.split("/")[-1]):
                    code = content
        return code

    def _retrieve_context(self, design_reference: str, language: str, framework_back: str, database: str) -> str:
        if not self.use_rag:
            return ""
        functional = self._extract_domain_keywords(design_reference)
        query = (
            f"{language} {framework_back} {database} {functional} "
            f"backend implementation security middleware "
            f"JWT RBAC audit logging API database {design_reference[:600]}"
        )
        result = self.rag.retrieve(
            query=query,
            top_k=5,
            kb_types=["template", "schema", "compliance"],
            use_query_transform=True,
            use_rerank=True,
            use_self_rag=True,
            use_graph=False,
        )
        return result.get("context_string", "")

    def _plan_files(
        self,
        design_content: str,
        database_content: str,
        rag_context: str,
        language: str,
        framework_back: str,
        database: str,
    ) -> list[str]:
        prompt = f"""You are a senior {language} backend engineer.
List the complete backend project files to generate.

Rules:
- Output one relative file path per line.
- Do not output numbering, explanations, Markdown, or code fences.
- Use dependency order: config -> database -> models -> schemas -> services -> routes/controllers -> middleware/auth -> app entry -> tests.
- Include dependency/build files for the selected framework.
- Keep paths relative and do not use absolute paths or parent traversal.
- Prefer placing application code under backend/.
- Generate at most {MAX_BACKEND_FILES} files.

Backend framework:
{framework_back}

Target database:
{database}

Design document:
{design_content or "None"}

Database design:
{database_content or "None"}

RAG context:
{rag_context or "None"}

File list:
"""
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048,
        )
        files = self._parse_file_plan(response)
        files = self._validate_file_plan(files, language, framework_back)
        if files:
            return files
        return self._fallback_files(language, framework_back)

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

    def _validate_file_plan(self, files: list[str], language: str, framework_back: str) -> list[str]:
        valid: list[str] = []
        seen = set()
        self._truncated_count = 0
        for path in files:
            sanitized = self._sanitize_generated_path(path)
            if not sanitized or sanitized in seen:
                continue
            seen.add(sanitized)
            if len(valid) >= MAX_BACKEND_FILES:
                self._truncated_count += 1
                continue
            valid.append(sanitized)

        if self._truncated_count > 0:
            print(f"[BackendAgent] WARNING: {self._truncated_count} files dropped "
                  f"from plan (MAX_BACKEND_FILES={MAX_BACKEND_FILES})")

        for required in self._required_files(language, framework_back):
            if required not in seen and len(valid) < MAX_BACKEND_FILES:
                valid.insert(0, required)
                seen.add(required)
        return valid[:MAX_BACKEND_FILES]

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

    def _truncate_for_prompt(self, text: str, max_chars: int) -> str:
        """Truncate text on section boundaries, keeping head and tail."""
        text = text or ""
        if len(text) <= max_chars:
            return text

        # Try to split on Markdown headings (## / ###) to preserve section integrity
        sections = re.split(r"\n(?=## )", text)
        if len(sections) <= 1:
            # No headings found — fall back to character-based
            head = int(max_chars * 0.6)
            tail = max_chars - head
            return text[:head] + "\n...(truncated)...\n" + text[-tail:]

        # Greedy: take full sections from the start until budget is exhausted
        parts_head: list[str] = []
        used = 0
        for sec in sections:
            if used + len(sec) <= max_chars:
                parts_head.append(sec)
                used += len(sec)
            else:
                break

        return "\n".join(parts_head)

    def _extract_domain_keywords(self, text: str) -> str:
        """Extract domain-specific nouns for better RAG retrieval precision."""
        words = re.findall(r'\b[A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{2,}){0,2}\b', text[:500])
        stops = {
            "the", "this", "that", "with", "from", "have", "will", "shall", "must",
            "should", "each", "every", "other", "some", "such", "when", "where",
        }
        domain = [w for w in words if w.lower() not in stops]
        return " ".join(domain[:5])

    def _extract_signatures(self, generated: list[tuple[str, str]]) -> str:
        """Extract class/function/type signatures for cross-file consistency."""
        lines_out: list[str] = []
        for path, code in generated:
            sigs: list[str] = []
            for line in code.split("\n"):
                stripped = line.strip()
                if stripped.startswith(("class ", "def ", "async def ")):
                    sigs.append(stripped.rstrip(":").rstrip())
                elif re.match(r"^\w+\s*:\s*\w+", stripped) and "=" not in stripped:
                    sigs.append(stripped)
                elif stripped.startswith(("type ", "func ")):
                    sigs.append(stripped.rstrip("{"))
                elif re.match(r"^(export\s+)?(interface|type|class|function|const)\s", stripped):
                    sigs.append(stripped.rstrip("{"))
                elif re.match(r"^\s*(public|private|protected)\s+(static\s+)?\w+\s+\w+\(", stripped):
                    sigs.append(stripped.rstrip("{").strip())
            if sigs:
                lines_out.append(f"// {path}\n" + "\n".join(sigs[:30]))
        return "\n".join(lines_out[:200])

    def _file_type_hint(self, file_path: str, language: str) -> str:
        """Return contextual guidance based on the type of file being generated."""
        path_lower = file_path.lower()
        lang_lower = language.lower()

        comment = "#" if lang_lower == "python" else "//"

        hints: list[tuple[str, str]] = []

        # Entry points
        if any(kw in path_lower for kw in ["main.py", "app.py", "application.java", "main.go",
                                              "index.js", "index.ts", "server.", "app.ts"]):
            hints.append(("入口文件", (
                f"这是应用入口。需要: 1) 按正确顺序注册所有中间件 "
                f"2) 引入并注册所有已完成模块中的路由/控制器 "
                f"3) 配置 CORS、异常处理器、生命周期钩子 "
                f"4) 读取环境变量配置 5) 启动数据库连接池"
            )))

        # Models / Entities
        if any(kw in path_lower for kw in ["model", "entity", "domain"]):
            hints.append(("数据模型", (
                f"这是 ORM 模型/实体定义。需要: 1) 定义所有字段及类型、约束 "
                f"2) 正确定义外键与关系（一对多/多对多） "
                f"3) 如有敏感字段（密码/token）标注但不在此处做哈希（由 service 处理） "
                f"4) 使用 {comment} noqa 标记有意为之的 lint 例外"
            )))

        # Schemas / DTOs
        if any(kw in path_lower for kw in ["schema", "dto", "request", "response"]):
            hints.append(("数据校验/Schema", (
                f"这是请求/响应 Schema 或 DTO。需要: 1) 所有字段有类型注解和校验规则 "
                f"2) 密码字段不在响应 Schema 中 "
                f"3) 分页查询参数有默认值和最大值限制 "
                f"4) 敏感操作的请求体需要确认字段（如删除需要输入密码）"
            )))

        # Services
        if any(kw in path_lower for kw in ["service", "usecase", "manager"]):
            hints.append(("业务服务层", (
                f"这是业务逻辑层。需要: 1) 方法有明确的事务边界 "
                f"2) 关键操作写入审计日志（操作人、时间、IP、操作类型、结果） "
                f"3) 异常统一抛出自定义业务异常（不要泄露内部错误细节） "
                f"4) 密码/Token 等敏感数据不记录到日志"
            )))

        # Routes / Controllers
        if any(kw in path_lower for kw in ["route", "controller", "handler", "endpoint", "view"]):
            hints.append(("路由/控制器", (
                f"这是 API 路由/控制器。需要: 1) 每个端点有明确的 HTTP 方法和路径 "
                f"2) 请求参数校验在进入 service 之前完成 "
                f"3) 返回正确的 HTTP 状态码（200/201/400/401/403/404/409/500） "
                f"4) 需要认证的端点加上认证装饰器/中间件 "
                f"5) 需要特定权限的端点加上权限检查"
            )))

        # Auth / Middleware
        if any(kw in path_lower for kw in ["auth", "middleware", "guard", "interceptor"]):
            hints.append(("认证/中间件", (
                f"这是安全中间件/认证模块。需要: 1) JWT 验证逻辑（过期检查、签名验证） "
                f"2) 从请求中提取用户信息并注入上下文 "
                f"3) 对公开路径（如 /auth/login）放行 "
                f"4) Token 刷新机制 "
                f"5) 认证失败返回 401，权限不足返回 403"
            )))

        # Config
        if any(kw in path_lower for kw in ["config", "setting", "env", "application.yml",
                                              "application.properties", ".env"]):
            hints.append(("配置模块", (
                f"这是配置模块。需要: 1) 所有敏感值通过环境变量读取，提供合理默认值 "
                f"2) 数据库连接池参数可配置 "
                f"3) JWT 过期时间、密钥等可配置 "
                f"4) 日志级别可配置"
            )))

        # Database / Session
        if any(kw in path_lower for kw in ["database", "session", "db.", "connection"]):
            hints.append(("数据库/会话管理", (
                f"这是数据库会话管理。需要: 1) 连接池配置 "
                f"2) 会话生命周期管理（请求开始创建、请求结束关闭） "
                f"3) 事务管理工具函数"
            )))

        if not hints:
            return ""

        return "文件类型指引:\n" + "\n".join(f"- [{label}] {guidance}" for label, guidance in hints)

    def _comment_style(self, language: str) -> str:
        """Return the comment syntax for the target language."""
        return "#" if language.lower() == "python" else "//"

    def _format_backend_artifact(self, generated: list[tuple[str, str]]) -> str:
        return "\n\n".join(
            f"PATH：{path}\nCODE：\n{code.rstrip()}\n" for path, code in generated
        )

    def _build_finish_code_context(self, generated: list[tuple[str, str]]) -> str:
        if not generated:
            return ""

        parts: list[str] = []

        # 1. Full file list (paths only)
        parts.append(f"Generated files ({len(generated)}): {', '.join(path for path, _ in generated)}")

        # 2. Interface summary extracted from ALL files
        signatures = self._extract_signatures(generated)
        if signatures:
            parts.append(f"Module interfaces:\n{signatures}")

        # 3. Recent file code (last 10 files, 2000 chars each)
        parts.append("Recent module code:")
        for path, code in generated[-10:]:
            parts.append(f"PATH：{path}\nCODE：\n{code[:2000]}")

        return "\n\n".join(parts)

    def _normalize_tech_stack(self, tech_stack: dict, design_reference: str) -> dict[str, str]:
        language = self._normalize_stack_value(tech_stack.get("language"))
        framework_back = self._normalize_stack_value(tech_stack.get("framework_back"))
        database = self._normalize_stack_value(tech_stack.get("database")) or "Unspecified"

        framework_language = self._language_for_framework(framework_back)

        # Only use framework inference when language is NOT explicitly specified
        if not language:
            if framework_language:
                language = framework_language
            else:
                language = self._select_language(design_reference)
        elif framework_language and language.lower() != framework_language.lower():
            print(f"[BackendAgent] Language mismatch: user={language}, "
                  f"framework={framework_back}→{framework_language}. "
                  f"Keeping user-specified language: {language}")

        if not framework_back:
            framework_back = self._default_framework_for_language(framework_language or language)

        return {
            "language": language,
            "framework_back": framework_back,
            "database": database,
        }

    def _select_language(self, design_content: str) -> str:
        text = (design_content or "").lower()
        candidates = [
            ("Java", ["spring boot", "spring", "java"]),
            ("Go", ["golang", " gin ", " go "]),
            ("TypeScript", ["nestjs", "node.js", "nodejs", "express", "typescript backend"]),
            ("C#", [".net", "asp.net", "c#"]),
            ("Rust", ["rust", "axum"]),
            ("Python", ["fastapi", "python", "django", "flask"]),
        ]
        for language, keywords in candidates:
            if any(keyword in text for keyword in keywords):
                return language
        return "Python"

    def _language_for_framework(self, framework: str) -> str:
        text = (framework or "").lower()
        pairs = [
            ("Python", ["fastapi", "django", "flask"]),
            ("Java", ["spring"]),
            ("Go", ["gin", "go-zero", "fiber"]),
            ("TypeScript", ["nestjs", "express", "koa", "node"]),
            ("C#", [".net", "asp.net"]),
            ("Rust", ["axum", "actix"]),
        ]
        for language, keywords in pairs:
            if any(keyword in text for keyword in keywords):
                return language
        return ""

    def _default_framework_for_language(self, language: str) -> str:
        defaults = {
            "Python": "FastAPI",
            "Java": "Spring Boot",
            "Go": "Gin",
            "TypeScript": "NestJS",
            "C#": "ASP.NET Core",
            "Rust": "Axum",
        }
        return defaults.get(language, "FastAPI")

    def _normalize_stack_value(self, value) -> str:
        text = str(value or "").strip()
        empty_values = {"", "{}", "[]", "无", "未指定", "null", "none", "None", "Unspecified"}
        return "" if text in empty_values else text

    def _required_files(self, language: str, framework_back: str) -> list[str]:
        text = f"{language} {framework_back}".lower()
        if "spring" in text or "java" in text:
            return ["backend/pom.xml", "backend/src/main/resources/application.yml"]
        if "nestjs" in text:
            return ["backend/package.json", "backend/tsconfig.json"]
        if "express" in text or "koa" in text:
            return ["backend/package.json"]
        if "node" in text and "typescript" in text:
            return ["backend/package.json", "backend/tsconfig.json"]
        if "node" in text:
            return ["backend/package.json"]
        if "go" in text or "gin" in text or "fiber" in text or "echo" in text:
            return ["backend/go.mod"]
        if ".net" in text or "c#" in text or "asp" in text:
            return ["backend/backend.csproj"]
        if "rust" in text or "axum" in text or "actix" in text:
            return ["backend/Cargo.toml"]
        if "django" in text:
            return ["backend/requirements.txt", "backend/manage.py"]
        if "flask" in text:
            return ["backend/requirements.txt", "backend/app.py"]
        return ["backend/requirements.txt", "backend/main.py"]

    def _fallback_files(self, language: str, framework_back: str) -> list[str]:
        text = f"{language} {framework_back}".lower()
        if "spring" in text or "java" in text:
            return [
                "backend/pom.xml",
                "backend/src/main/java/com/example/security/Application.java",
                "backend/src/main/java/com/example/security/config/SecurityConfig.java",
                "backend/src/main/java/com/example/security/controller/AuthController.java",
                "backend/src/main/java/com/example/security/service/AuthService.java",
                "backend/src/main/resources/application.yml",
            ]
        if "nestjs" in text:
            return ["backend/package.json", "backend/tsconfig.json", "backend/src/main.ts", "backend/src/app.module.ts"]
        if "express" in text or "koa" in text:
            return ["backend/package.json", "backend/src/index.js", "backend/src/routes/index.js"]
        if "go" in text:
            return ["backend/go.mod", "backend/main.go", "backend/internal/routes/routes.go"]
        return [
            "backend/requirements.txt",
            "backend/main.py",
            "backend/config.py",
            "backend/database.py",
            "backend/models.py",
            "backend/schemas.py",
            "backend/auth.py",
            "backend/routes.py",
        ]

    def _validate_generated_files(
        self,
        generated: list[tuple[str, str]],
        language: str,
        framework_back: str,
    ) -> dict:
        paths = {path for path, _ in generated}
        issues = []

        for required in self._required_files(language, framework_back):
            if required not in paths:
                issues.append(f"Missing expected file: {required}")

        lang_lower = language.lower()

        for path, code in generated:
            if not code.strip():
                issues.append(f"{path}: empty file")
                continue
            if "GENERATION_ERROR" in code:
                issues.append(f"{path}: PATH/CODE format not followed by LLM")
                continue

            # Python: AST parse
            if lang_lower == "python" and path.endswith(".py"):
                try:
                    ast.parse(code, filename=path)
                except SyntaxError as exc:
                    issues.append(f"{path}: Python syntax error at line {exc.lineno}: {exc.msg}")

            # Go: package declaration + balanced braces
            elif lang_lower == "go" and path.endswith(".go"):
                if not code.strip().startswith("package "):
                    issues.append(f"{path}: Go file missing package declaration")
                if code.count("{") != code.count("}"):
                    issues.append(f"{path}: Go file has unbalanced braces")

            # TypeScript / JavaScript: balanced braces and parens
            elif lang_lower in ("typescript", "javascript") and path.endswith((".ts", ".tsx", ".js", ".jsx")):
                if code.count("{") != code.count("}"):
                    issues.append(f"{path}: unbalanced braces")
                if code.count("(") != code.count(")"):
                    issues.append(f"{path}: unbalanced parentheses")

        return {
            "ok": not issues,
            "issues": issues,
            "file_count": len(generated),
        }

    def _token_budget(self, file_path: str) -> int:
        large_markers = ["main", "service", "router", "routes", "models", "auth", "middleware", "controller"]
        return 24576 if any(marker in file_path.lower() for marker in large_markers) else 16384
