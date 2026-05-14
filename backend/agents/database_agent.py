"""Database Agent - template-based database design generation."""

import re
from pathlib import Path

from backend.agents.base import BaseAgent
from backend.core.prompts import DATABASE_SYSTEM, NO_PREAMBLE


DEFAULT_DB_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "db_templates"
    / "database_design_template.md"
)


DATABASE_GENERATION_PROMPT = """你是一个数据库架构师，专精于网络安全平台的数据建模。请根据设计文档、目标数据库和 RAG 检索结果，严格参考数据库设计模板输出完整数据库设计方案。

硬性要求:
- 必须保留模板中的所有 Markdown 标题和表格结构。
- DDL 必须匹配目标数据库语法；如果目标数据库未指定，默认使用 SQLite，并说明原因。
- 表结构必须覆盖设计文档中的核心业务对象、认证授权、审计日志、安全告警/配置等必要数据。
- DDL 按外键依赖顺序输出。
- 所有表必须包含主键；重要查询字段必须给出索引建议。
- 涉及密码、Token、密钥、PII 等敏感数据时必须说明存储和脱敏策略。
- 信息缺失处写"待确认"，不要编造业务事实。
- 只输出完整 Markdown 文档，不要解释生成过程。

目标数据库:
{database}

设计文档:
{design_content}

RAG 检索结果:
{rag_context}

数据库设计模板:
{template}
"""


SQL_EXTRACTION_PROMPT = """你是一个数据库开发工程师。请从以下数据库设计文档中提取完整的 SQL 建库脚本。

硬性要求:
- 目标数据库: {database}
- 只输出纯 SQL 语句，不要 Markdown 标记或解释文字。
- 按外键依赖顺序输出 CREATE TABLE 语句。
- 包含所有索引定义。
- 包含种子数据 INSERT 语句。
- 在文件头部添加注释说明目标数据库和生成日期。
- 如果设计文档中的 DDL 与目标数据库语法不兼容，修正为兼容语法。
- SQL 注释使用 -- 格式。

数据库设计文档:
{design_doc}

SQL 建库脚本:"""


class DatabaseAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="database",
            system_prompt=DATABASE_SYSTEM,
            tools=["write_file"],
            use_rag=True,
        )

    def generate(self, user_input: str, design_content: str = "", database: str = "") -> dict:
        target_database = self._normalize_database(database)
        design_reference = design_content or user_input
        rag_context = self._retrieve_context(design_reference, target_database)
        template = self._load_template()
        template = self._truncate_template(template)

        # ── Call 1: Generate database design document ──
        prompt = DATABASE_GENERATION_PROMPT.format(
            database=target_database,
            design_content=design_reference,
            rag_context=rag_context or "无相关 RAG 检索结果。",
            template=template,
        )
        prompt += NO_PREAMBLE

        database_content = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=8192,
        )
        print(f"[DatabaseAgent] Design doc generated ({len(database_content)} chars)")

        # Validate DDL in design doc
        ddl_issues = self._validate_ddl(database_content)
        if ddl_issues:
            print(f"[DatabaseAgent] WARNING: DDL validation found issues: {ddl_issues}")

        # ── Call 2: Extract SQL from design document ──
        sql_prompt = SQL_EXTRACTION_PROMPT.format(
            database=target_database,
            design_doc=database_content[:8000],
        )
        sql_prompt += NO_PREAMBLE

        sql_content = self.llm.chat_sync(
            messages=[{"role": "user", "content": sql_prompt}],
            temperature=0.2,
            max_tokens=4096,
        )
        # Strip any markdown fences
        sql_content = sql_content.strip()
        if sql_content.startswith("```"):
            lines = sql_content.split("\n")
            sql_content = "\n".join(lines[1:])
        if sql_content.endswith("```"):
            sql_content = sql_content[:-3].strip()
        print(f"[DatabaseAgent] SQL extracted ({len(sql_content)} chars)")

        # Validate SQL output
        sql_issues = self._validate_ddl(sql_content)
        if sql_issues:
            print(f"[DatabaseAgent] WARNING: SQL validation found issues: {sql_issues}")

        return {
            "database_content": database_content,
            "sql_content": sql_content,
            "rag_context": rag_context,
            "template_used": template,
            "target_database": target_database,
        }

    def _retrieve_context(self, design_reference: str, database: str) -> str:
        if not self.use_rag:
            return ""
        query = (
            f"{database} database schema design RBAC audit log security compliance "
            f"DDL indexes seed data {design_reference[:1000]}"
        )
        result = self.rag.retrieve(
            query=query,
            top_k=5,
            kb_types=["schema", "compliance", "template"],
            use_query_transform=True,
            use_rerank=True,
            use_self_rag=True,
            use_graph=False,
        )
        return result.get("context_string", "")

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
        return truncated if len(truncated) >= 200 else template[:max_chars]

    @staticmethod
    def _validate_ddl(content: str) -> list[str]:
        """Basic DDL validation: check for CREATE TABLE completeness and PRIMARY KEYs."""
        issues = []

        # Extract CREATE TABLE blocks from markdown
        ddl_blocks = re.findall(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\S+\s*\(.+?\);",
            content, re.DOTALL | re.IGNORECASE,
        )

        if not ddl_blocks:
            issues.append("No CREATE TABLE statements found in generated DDL")
            return issues

        for stmt in ddl_blocks:
            # Normalize whitespace for parsing
            normalized = re.sub(r"\s+", " ", stmt).strip()

            # Check balanced parentheses inside the CREATE TABLE body
            body_match = re.search(r"\((.+)\)", normalized, re.DOTALL)
            if body_match:
                body = body_match.group(1)
                if body.count("(") != body.count(")"):
                    issues.append(f"Unbalanced parentheses in: {normalized[:80]}...")

            # Check PRIMARY KEY presence
            if not re.search(r"PRIMARY\s+KEY", normalized, re.IGNORECASE):
                issues.append(f"No PRIMARY KEY defined in: {normalized[:80]}...")

            # Check for common syntax errors
            if re.search(r",\s*\)", normalized):
                issues.append(f"Trailing comma before closing paren in: {normalized[:80]}...")

        return issues

    def _load_template(self) -> str:
        if not DEFAULT_DB_TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"Database design template not found: {DEFAULT_DB_TEMPLATE_PATH}")
        template = DEFAULT_DB_TEMPLATE_PATH.read_text(encoding="utf-8")
        if len(template.strip()) < 200 or "## 3. 表结构设计" not in template:
            raise ValueError("Database design template is missing required structure.")
        return template

    # Common database name abbreviations and aliases
    _DB_ALIAS_MAP: dict[str, str] = {
        "pg": "PostgreSQL", "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
        "mssql": "SQL Server", "sql server": "SQL Server", "sqlserver": "SQL Server",
        "maria": "MariaDB", "mariadb": "MariaDB",
        "mysql": "MySQL",
        "sqlite": "SQLite", "sqlite3": "SQLite",
        "oracle": "Oracle", "oracle db": "Oracle",
        "mongo": "MongoDB", "mongodb": "MongoDB",
    }

    def _normalize_database(self, database: str) -> str:
        text = str(database or "").strip()
        if text in {"", "{}", "[]", "无", "未指定", "null", "None", "Unspecified"}:
            return "SQLite"
        return self._DB_ALIAS_MAP.get(text.lower(), text)
