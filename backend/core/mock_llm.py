"""Mock LLM Client — intercepts chat_sync calls and returns plausible responses for pipeline testing.

Activate via environment variable: MOCK_LLM=true
"""

import json
import re
import os
from typing import Callable


class MockLLMClient:
    """Drop-in replacement for LLMClient that returns canned responses matched by prompt keywords."""

    def __init__(self):
        self.model = "mock-model"
        self.stats: dict[str, int] = {}  # handler_name → call_count
        self._call_log: list[dict] = []  # last N calls
        self._max_log = 100
        self._registry: list[tuple[list[str], Callable[[str, int], str]]] = [
            # RAG module
            (["查询变体", "multi_query", "query variants", "改写", "检索查询变体", "检索视角"],
             self._handle_query_transform),
            (["假设性文档片段", "hypothetical", "HyDE", "HYDE"],
             self._handle_hyde),
            (["上位查询", "step_back", "step back", "抽象成一个更通用"],
             self._handle_step_back),
            (["检索结果排序", "rerank", "RERANK", "按相关度从高到低排序", "打分"],
             self._handle_rerank),
            (["检索质量评估", "SELF_RAG", "self_rag", "评估以下检索结果", "relevance"],
             self._handle_self_rag),
            (["实体关系抽取", "ENTITY_EXTRACTION", "extract entities", "实体及其关系"],
             self._handle_entity_extraction),
            (["上下文前缀", "CONTEXT_PREFIX", "context prefix", "contextual retrieval"],
             self._handle_context_prefix),

            # Pipeline
            (["压缩成一份用于软件开发线性流水线的需求摘要", "prepare_pipeline_input",
              "控制在 1200 字以内", "需求摘要:"],
             self._handle_pipeline_summary),
            (["提取技术栈", "extract_tech_stack", "只输出固定四行键值对",
              "language:{}", "framework_front:{}", "framework_back:{}", "database:{}"],
             self._handle_tech_stack),

            # Orchestrator
            (["开发流水线主编排器", "ORCHESTRATOR", "orchestrator",
              "可用的开发阶段", "输出一个 JSON 格式的执行计划"],
             self._handle_orchestrator_plan),

            # Backend Agent — plan (before PRD to avoid PRD keyword match in backend prompts)
            (["规划需要生成的后端模块", "后端模块文件列表",
              "List the complete backend project files",
              "后端项目文件", "Output one relative file path per line",
              "dependency order", "config -> database -> models -> schemas"],
             self._handle_backend_plan),

            # Backend Agent — code generation (before PRD handler)
            (["PATH：", "CODE：", "生成可运行的后端代码",
              "当前生成模块:", "后端开发工程师"],
             self._handle_backend_code),

            # Frontend Agent — plan (before PRD handler)
            (["规划需要生成的前端模块", "前端模块文件列表",
              "List the complete frontend project files",
              "前端项目文件", "package/config", "API client/types", "auth state"],
             self._handle_frontend_plan),

            # Frontend Agent — code generation (before PRD handler)
            (["生成完整可运行的前端代码", "前端开发工程师",
              "前端框架:", "前端语言:"],
             self._handle_frontend_code),

            # PRD Agent
            (["严格参考指定 PRD 模板生成 PRD 文档", "PRD 模板", "PRD_TEMPLATE",
              "产品需求文档", "PRD 模板是确定性资源"],
             self._handle_prd_generation),
            (["产品需求分析专家", "提炼生成 PRD 所需的需求思考",
              "PRD_RAG_THINK", "业务目标", "目标用户与使用场景", "核心功能清单"],
             self._handle_prd_think),
            (["没有完整保留指定模板结构", "PRD 模板:", "PRD_TEMPLATE_REPAIR",
              "严格按模板标题和表格结构重写"],
             self._handle_prd_repair),

            # Design Agent
            (["系统架构师", "DESIGN_SYSTEM", "概要设计文档",
              "技术栈约束", "系统架构概述", "模块划分与职责", "API 设计草案"],
             self._handle_design_generation),

            # Database Agent — SQL extraction (must be before design generation to avoid match)
            (["提取完整的 SQL 建库脚本", "SQL_EXTRACTION",
              "只输出纯 SQL 语句", "SQL 建库脚本:"],
             self._handle_sql_extraction),

            # Database Agent — design generation
            (["数据库架构师", "DATABASE_SYSTEM",
              "完整数据库设计方案", "ER 图描述", "DDL 语句",
              "索引设计建议", "初始数据", "数据安全设计",
              "DATABASE_GENERATION_PROMPT"],
             self._handle_database_generation),

            # Reviewer
            (["代码审查与安全审计", "REVIEWER_SYSTEM", "审查维度", "总体评价",
              "功能完整性检查", "安全漏洞扫描", "合规性检查"],
             self._handle_review),
            (["代码修复工程师", "根据审查发现修复指定文件",
              "只能修复当前文件", "输出完整的修复后文件内容",
              "审查发现（只修复以下问题）"],
             self._handle_repair),

            # Research Agent
            (["技术研究助手", "RESEARCH_SYSTEM", "多轮检索结果",
              "深度研究", "关键发现", "推荐的技术方案方向",
              "可复用的模板/模式"],
             self._handle_research_summary),

            # Base Agent generic generation (DesignAgent / DeployAgent via graph)
            (["资深", "开发工程师", "专精于网络安全平台开发",
              "system_prompt", "rag_context"],
             self._handle_generic_generation),
        ]

    # ── Routing ──────────────────────────────────────────────

    def _find_handler(self, prompt: str):
        """Find the first handler whose keywords all appear (case-insensitive) in the prompt."""
        prompt_lower = prompt.lower()
        for keywords, handler in self._registry:
            if any(kw.lower() in prompt_lower for kw in keywords):
                return handler
        return self._handle_fallback

    def chat_sync(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> str:
        """Sync chat — main entry point. Matches prompt keywords and delegates to handler."""
        # Extract the last user message
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = msg.get("content", "")
                break
        if not user_text:
            user_text = str(messages)

        handler = self._find_handler(user_text)
        handler_name = handler.__name__
        result = handler(user_text, max_tokens)

        # Track stats
        self.stats[handler_name] = self.stats.get(handler_name, 0) + 1
        self._call_log.append({
            "handler": handler_name,
            "chars": len(result),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "prompt_preview": user_text[:120],
        })
        if len(self._call_log) > self._max_log:
            self._call_log = self._call_log[-self._max_log:]

        print(f"[MOCK] {handler_name} → {len(result)} chars (temp={temperature}, max_tokens={max_tokens})")
        return result

    def get_stats(self) -> dict:
        """Return current mock call statistics."""
        return {
            "total_calls": sum(self.stats.values()),
            "handlers": self.stats,
            "recent_calls": self._call_log[-20:],
        }

    # ── RAG Handlers ─────────────────────────────────────────

    def _handle_query_transform(self, prompt: str, max_tokens: int) -> str:
        return (
            "PRD 模板 验收标准 安全合规要求\n"
            "系统架构 模块划分 API 设计 网络安全\n"
            "数据库模型 RBAC 审计日志 索引设计\n"
            "后端认证授权 安全中间件 最佳实践"
        )

    def _handle_hyde(self, prompt: str, max_tokens: int) -> str:
        return (
            "该系统采用微服务架构，前端使用 React + TypeScript 构建管理控制台，"
            "后端基于 FastAPI 提供 RESTful API。认证采用 JWT 双令牌模式，"
            "权限模型为 RBAC 三级（角色-权限-资源）。审计日志记录所有敏感操作，"
            "包括操作人、时间、IP、操作类型和结果。数据库使用 PostgreSQL，"
            "通过 SQLAlchemy ORM 进行数据访问。安全方面实现了 OWASP Top 10 防护，"
            "包括 XSS、CSRF、SQL 注入和 SSRF 防护。"
        )

    def _handle_step_back(self, prompt: str, max_tokens: int) -> str:
        return "网络安全平台 后端架构设计模式 数据库设计最佳实践 认证授权方案"

    def _handle_rerank(self, prompt: str, max_tokens: int) -> str:
        return json.dumps([
            {"index": 0, "score": 9.0, "reason": "Directly relevant to query"},
            {"index": 1, "score": 7.5, "reason": "Partially covers the topic"},
            {"index": 2, "score": 6.0, "reason": "Tangentially related"},
        ], ensure_ascii=False)

    def _handle_self_rag(self, prompt: str, max_tokens: int) -> str:
        return json.dumps({
            "relevance": "high",
            "per_doc": [
                {"index": 0, "relevance": "relevant", "reason": "Matches query domain"},
                {"index": 1, "relevance": "relevant", "reason": "Contains related patterns"},
            ],
        }, ensure_ascii=False)

    def _handle_entity_extraction(self, prompt: str, max_tokens: int) -> str:
        return json.dumps({
            "entities": [
                {"name": "用户管理", "type": "concept", "description": "用户注册、登录、权限分配功能"},
                {"name": "RBAC", "type": "technology", "description": "基于角色的访问控制模型"},
                {"name": "JWT", "type": "technology", "description": "JSON Web Token 认证机制"},
                {"name": "审计日志", "type": "concept", "description": "安全审计和合规日志记录"},
            ],
            "relations": [
                {"source": "用户管理", "target": "RBAC", "relation": "使用"},
                {"source": "用户管理", "target": "JWT", "relation": "认证方式"},
                {"source": "RBAC", "target": "审计日志", "relation": "审计对象"},
            ],
        }, ensure_ascii=False)

    def _handle_context_prefix(self, prompt: str, max_tokens: int) -> str:
        return "This is a technical specification document describing security platform architecture and design patterns."

    # ── Pipeline Handlers ─────────────────────────────────────

    def _handle_pipeline_summary(self, prompt: str, max_tokens: int) -> str:
        text = prompt.split("用户原始需求:")[-1] if "用户原始需求:" in prompt else prompt
        # Return a compressed version (or the original if already short)
        summary = text.strip()[:1200]
        return summary if len(summary) > 50 else "构建一个网络安全管理平台，支持用户管理、角色权限控制、设备监控、告警管理和审计日志功能。"

    def _handle_tech_stack(self, prompt: str, max_tokens: int) -> str:
        return "language:Python\nframework_front:React\nframework_back:FastAPI\ndatabase:PostgreSQL"

    def _handle_orchestrator_plan(self, prompt: str, max_tokens: int) -> str:
        return json.dumps({
            "stages": ["research", "prd", "design", "database", "backend", "frontend", "review", "deploy"],
            "summary": "全流程开发：从需求分析到部署配置的完整网络安全平台构建",
        }, ensure_ascii=False)

    # ── PRD Handlers ──────────────────────────────────────────

    def _handle_prd_think(self, prompt: str, max_tokens: int) -> str:
        return (
            "1. 业务目标\n"
            "- 构建网络安全管理平台，统一管理安全设备、用户权限和告警事件\n"
            "- 满足等保2.0三级合规要求\n\n"
            "2. 目标用户与使用场景\n"
            "- 安全运维人员：日常监控和告警处理\n"
            "- 安全管理员：策略配置和权限管理\n"
            "- 审计员：日志审计和合规检查\n\n"
            "3. 核心功能清单\n"
            "- 用户管理（CRUD + 角色分配）\n"
            "- 设备监控（状态采集 + 实时告警）\n"
            "- 告警管理（规则配置 + 通知分发）\n"
            "- 审计日志（操作记录 + 合规报告）\n"
            "- 权限管理（RBAC 三级模型）\n\n"
            "4. 数据对象与数据流\n"
            "- 用户 → 角色 → 权限（多对多关系）\n"
            "- 设备 → 监控数据 → 告警事件\n"
            "- 操作 → 审计日志 → 合规报告\n\n"
            "5. 权限、安全、合规要求\n"
            "- JWT 双令牌认证\n"
            "- RBAC 权限控制\n"
            "- OWASP Top 10 防护\n"
            "- 等保2.0 审计要求\n\n"
            "6. 非功能需求\n"
            "- 响应时间 < 500ms (P95)\n"
            "- 支持 1000+ 并发用户\n"
            "- 数据加密存储\n\n"
            "7. 风险、约束和待确认问题\n"
            "- 第三方系统集成接口待确认\n"
            "- 数据留存周期需与合规部门确认"
        )

    def _handle_prd_generation(self, prompt: str, max_tokens: int) -> str:
        return """# 网络安全管理平台 — 产品需求文档 (PRD)

## 1. 产品概述与背景
构建企业级网络安全管理平台，提供统一的用户管理、设备监控、告警处置和审计日志功能。

## 2. 目标用户与使用场景
- **安全运维人员**: 日常监控告警处理
- **安全管理员**: 策略配置和权限管理
- **审计员**: 日志审计和合规检查

## 3. 功能需求列表
| 优先级 | 功能模块 | 描述 |
| --- | --- | --- |
| P0 | 用户管理 | 用户 CRUD、角色分配、账号启停 |
| P0 | 认证授权 | JWT 登录、RBAC 权限控制、Token 刷新 |
| P1 | 设备监控 | 设备状态采集、实时指标展示 |
| P1 | 告警管理 | 告警规则配置、告警通知分发 |
| P2 | 审计日志 | 操作记录、日志查询、合规报告 |

## 4. 非功能需求
- 性能: P95 响应时间 < 500ms
- 可用性: 99.9% 可用
- 安全: OWASP Top 10 防护，等保2.0 三级合规
- 扩展: 支持水平扩展

## 5. 安全合规要求
- 认证: JWT 双令牌模式（access 15min / refresh 7d）
- 授权: RBAC 三级（角色-权限-资源）
- 审计: 操作日志完整记录（操作人、时间、IP、类型、结果）
- 数据: 密码 bcrypt 哈希、敏感字段加密存储
- 通信: HTTPS 强制、API 请求签名

## 6. 验收标准
- [ ] 所有 P0 功能可用
- [ ] 安全测试通过（OWASP Top 10）
- [ ] 性能测试达标
- [ ] 审计日志完整性验证通过
"""

    def _handle_prd_repair(self, prompt: str, max_tokens: int) -> str:
        return self._handle_prd_generation(prompt, max_tokens)

    # ── Design Handler ────────────────────────────────────────

    def _handle_design_generation(self, prompt: str, max_tokens: int) -> str:
        return """## 系统架构概述
采用前后端分离的微服务架构。前端 React SPA 通过 RESTful API 与后端 FastAPI 通信。
后端分为 API 网关层、业务服务层、数据访问层，使用 JWT 认证和 RBAC 授权。

## 系统架构
- 前端: React 18 + TypeScript + Tailwind CSS
- 后端: FastAPI (Python 3.11+)
- 数据库: PostgreSQL 15
- 缓存: Redis (可选)

## 模块划分
### 前端模块
- `auth/` — 认证状态管理、登录/登出、Token 刷新
- `pages/` — 仪表盘、用户管理、设备监控、告警管理、审计日志
- `components/` — 通用 UI 组件（表格、表单、图表）
- `api/` — API 客户端（axios 拦截器）
- `types/` — TypeScript 类型定义

### 后端模块
- `auth/` — JWT 认证中间件、RBAC 权限装饰器
- `users/` — 用户 CRUD、角色管理
- `devices/` — 设备注册、状态采集
- `alerts/` — 告警规则引擎、通知分发
- `audit/` — 审计日志记录、查询、导出
- `common/` — 异常处理、响应格式化、分页工具

## API 设计草案
| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /api/auth/login | 用户登录 |
| POST | /api/auth/refresh | Token 刷新 |
| GET | /api/users | 用户列表 |
| POST | /api/users | 创建用户 |
| PUT | /api/users/{id} | 更新用户 |
| DELETE | /api/users/{id} | 删除用户 |
| GET | /api/devices | 设备列表 |
| POST | /api/devices | 注册设备 |
| GET | /api/alerts | 告警列表 |
| GET | /api/audit/logs | 审计日志查询 |

## 安全架构设计
- 认证: JWT access/refresh 双令牌，access 15min 过期，refresh 7d
- 授权: RBAC 三级模型，角色绑定权限，权限绑定资源
- API 安全: 输入校验、速率限制、CORS 白名单
- 审计: 所有 CUD 操作记录审计日志
- 数据保护: 密码 bcrypt 哈希、敏感字段 AES-256 加密

## 部署架构
- 容器化部署 (Docker + docker-compose)
- Nginx 反向代理 + HTTPS 终止
- 数据库主从复制（生产环境）
- 日志收集 (ELK / Loki)
"""

    def _handle_sql_extraction(self, prompt: str, max_tokens: int) -> str:
        return (
            "-- Target: PostgreSQL\n"
            "-- Generated: 2026-05-11\n\n"
            "CREATE TABLE users (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    username VARCHAR(100) NOT NULL UNIQUE,\n"
            "    email VARCHAR(255) NOT NULL UNIQUE,\n"
            "    password_hash VARCHAR(255) NOT NULL,\n"
            "    is_active BOOLEAN DEFAULT true,\n"
            "    created_at TIMESTAMP DEFAULT NOW(),\n"
            "    updated_at TIMESTAMP DEFAULT NOW()\n"
            ");\n\n"
            "CREATE TABLE roles (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    name VARCHAR(50) NOT NULL UNIQUE,\n"
            "    description TEXT DEFAULT ''\n"
            ");\n\n"
            "CREATE TABLE permissions (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    resource VARCHAR(100) NOT NULL,\n"
            "    action VARCHAR(50) NOT NULL,\n"
            "    UNIQUE(resource, action)\n"
            ");\n\n"
            "CREATE TABLE user_roles (\n"
            "    user_id INT REFERENCES users(id) ON DELETE CASCADE,\n"
            "    role_id INT REFERENCES roles(id) ON DELETE CASCADE,\n"
            "    PRIMARY KEY (user_id, role_id)\n"
            ");\n\n"
            "CREATE TABLE role_permissions (\n"
            "    role_id INT REFERENCES roles(id) ON DELETE CASCADE,\n"
            "    permission_id INT REFERENCES permissions(id) ON DELETE CASCADE,\n"
            "    PRIMARY KEY (role_id, permission_id)\n"
            ");\n\n"
            "CREATE TABLE devices (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    name VARCHAR(200) NOT NULL,\n"
            "    ip VARCHAR(45) NOT NULL,\n"
            "    status VARCHAR(20) DEFAULT 'unknown',\n"
            "    created_at TIMESTAMP DEFAULT NOW()\n"
            ");\n\n"
            "CREATE TABLE alerts (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    device_id INT REFERENCES devices(id) ON DELETE CASCADE,\n"
            "    rule_name VARCHAR(200) NOT NULL,\n"
            "    message TEXT NOT NULL,\n"
            "    severity VARCHAR(20) DEFAULT 'medium',\n"
            "    created_at TIMESTAMP DEFAULT NOW()\n"
            ");\n\n"
            "CREATE TABLE audit_logs (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    user_id INT REFERENCES users(id),\n"
            "    action VARCHAR(100) NOT NULL,\n"
            "    target_type VARCHAR(50),\n"
            "    target_id INT,\n"
            "    detail JSONB DEFAULT '{}',\n"
            "    ip VARCHAR(45),\n"
            "    created_at TIMESTAMP DEFAULT NOW()\n"
            ");\n\n"
            "CREATE INDEX idx_alerts_device_created ON alerts(device_id, created_at);\n"
            "CREATE INDEX idx_audit_user_time ON audit_logs(user_id, created_at);\n"
            "CREATE INDEX idx_audit_action ON audit_logs(action);\n\n"
            "INSERT INTO roles (name, description) VALUES ('admin', 'System Admin');\n"
            "INSERT INTO roles (name, description) VALUES ('operator', 'Operator');\n"
            "INSERT INTO roles (name, description) VALUES ('auditor', 'Auditor');\n"
        )

    # ── Database Handler ──────────────────────────────────────

    def _handle_database_generation(self, prompt: str, max_tokens: int) -> str:
        return """## 1. 设计概述
### 1.1 目标数据库
PostgreSQL 15

### 1.2 设计目标
支持网络安全管理平台的核心业务：用户管理、RBAC 权限、设备监控、告警管理、审计日志。

## 2. ER 关系设计
### 2.1 核心实体
| 实体 | 说明 | 关键字段 | 关系 |
| --- | --- | --- | --- |
| User | 用户 | id, username, email, password_hash | 多对多 Role |
| Role | 角色 | id, name, description | 多对多 Permission |
| Permission | 权限 | id, resource, action | 多对多 Role |
| Device | 设备 | id, name, ip, status | 属于 Module |
| Module | 模块 | id, name, type | 一对多 Device |
| Alert | 告警 | id, device_id, rule, message, severity | 属于 Device |
| AuditLog | 审计日志 | id, user_id, action, target, detail, ip, timestamp | 属于 User |

### 2.2 实体关系说明
```mermaid
erDiagram
    User ||--o{ AuditLog : generates
    User }o--o{ Role : has
    Role }o--o{ Permission : binds
    Device ||--o{ Alert : triggers
    Module ||--o{ Device : contains
```

## 3. 表结构设计
### 3.2 DDL
```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    description TEXT DEFAULT ''
);

CREATE TABLE permissions (
    id SERIAL PRIMARY KEY,
    resource VARCHAR(100) NOT NULL,
    action VARCHAR(50) NOT NULL,
    UNIQUE(resource, action)
);

CREATE TABLE user_roles (
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    role_id INT REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE role_permissions (
    role_id INT REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INT REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE devices (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    ip VARCHAR(45) NOT NULL,
    status VARCHAR(20) DEFAULT 'unknown',
    module_id INT REFERENCES modules(id),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE modules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    type VARCHAR(50) DEFAULT 'other'
);

CREATE TABLE alerts (
    id SERIAL PRIMARY KEY,
    device_id INT REFERENCES devices(id) ON DELETE CASCADE,
    rule_name VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    severity VARCHAR(20) DEFAULT 'medium',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE audit_logs (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(50),
    target_id INT,
    detail JSONB DEFAULT '{}',
    ip VARCHAR(45),
    created_at TIMESTAMP DEFAULT NOW()
);
```

## 4. 索引设计
| 表名 | 索引名 | 字段 | 类型 | 理由 |
| --- | --- | --- | --- | --- |
| users | idx_users_email | email | UNIQUE | 登录查询 |
| alerts | idx_alerts_device_created | (device_id, created_at) | BTREE | 设备告警时间范围查询 |
| audit_logs | idx_audit_user_time | (user_id, created_at) | BTREE | 用户审计日志查询 |
| audit_logs | idx_audit_action | action | BTREE | 按操作类型过滤 |

## 5. 初始数据与种子数据
```sql
INSERT INTO roles (name, description) VALUES ('admin', '系统管理员'), ('operator', '运维人员'), ('auditor', '审计员');
INSERT INTO permissions (resource, action) VALUES ('users', 'read'), ('users', 'write'), ('devices', 'read'), ('alerts', 'read'), ('alerts', 'write');
```

## 6. 数据安全设计
### 6.1 敏感字段处理
- password_hash: bcrypt 哈希，不可逆
- email: 生产环境脱敏显示（u***@domain.com）

### 6.3 审计日志
audit_logs 表记录所有 CUD 操作，保留期 180 天，支持按用户、时间、操作类型查询。
"""

    # ── Backend Handlers ─────────────────────────────────────

    def _handle_backend_plan(self, prompt: str, max_tokens: int) -> str:
        return (
            "backend/requirements.txt\n"
            "backend/main.py\n"
            "backend/config.py\n"
            "backend/database.py\n"
            "backend/models/user.py\n"
            "backend/models/role.py\n"
            "backend/models/device.py\n"
            "backend/models/alert.py\n"
            "backend/models/audit_log.py\n"
            "backend/schemas/user.py\n"
            "backend/schemas/auth.py\n"
            "backend/schemas/device.py\n"
            "backend/schemas/alert.py\n"
            "backend/schemas/common.py\n"
            "backend/auth.py\n"
            "backend/routes/auth.py\n"
            "backend/routes/users.py\n"
            "backend/routes/devices.py\n"
            "backend/routes/alerts.py\n"
            "backend/services/auth_service.py\n"
            "backend/services/user_service.py\n"
            "backend/services/device_service.py\n"
            "backend/services/alert_service.py\n"
            "backend/middleware/auth.py\n"
        )

    def _handle_backend_code(self, prompt: str, max_tokens: int) -> str:
        # Extract file path from prompt — try multiple patterns
        file_match = re.search(
            r'(?:当前文件|current_file|file_path)[:\s]*(\S+)', prompt
        )
        file_path = "backend/main.py"
        if file_match:
            candidate = file_match.group(1).strip()
            # Only accept it if it looks like a file path
            if "/" in candidate or candidate.endswith((".py", ".ts", ".js", ".txt", ".json", ".yml", ".yaml", ".xml", ".cfg", ".ini")):
                file_path = candidate
        code = self._generate_backend_file(file_path)
        return f"```tool\n@write_file {file_path}\n{code}\n```"

    def _generate_backend_file(self, file_path: str) -> str:
        """Generate minimal runnable FastAPI backend code for a given file path."""
        basename = file_path.split("/")[-1]
        path_lower = file_path.lower()

        if basename == "requirements.txt":
            return "fastapi>=0.110.0\nuvicorn[standard]>=0.29.0\nsqlalchemy>=2.0.0\npydantic>=2.0.0\npydantic-settings>=2.0.0\npyjwt>=2.8.0\nbcrypt>=4.1.0\npsycopg2-binary>=2.9.0\n"

        if basename == "main.py":
            return (
                'from fastapi import FastAPI\n'
                'from fastapi.middleware.cors import CORSMiddleware\n'
                'from config import Settings\n'
                'from database import engine, Base\n'
                'from routes import auth, users, devices, alerts\n'
                '\n'
                'settings = Settings()\n'
                'app = FastAPI(title=settings.app_name, version="0.1.0")\n'
                'app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])\n'
                'Base.metadata.create_all(bind=engine)\n'
                'app.include_router(auth.router, prefix="/api/auth", tags=["auth"])\n'
                'app.include_router(users.router, prefix="/api/users", tags=["users"])\n'
                'app.include_router(devices.router, prefix="/api/devices", tags=["devices"])\n'
                'app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])\n'
                '\n'
                '@app.get("/api/health")\n'
                'def health(): return {"status": "ok"}\n'
            )

        if basename == "config.py":
            return (
                'from pydantic_settings import BaseSettings\n\n'
                'class Settings(BaseSettings):\n'
                '    app_name: str = "Security Platform"\n'
                '    database_url: str = "sqlite:///./app.db"\n'
                '    jwt_secret: str = "change-me-in-production"\n'
                '    jwt_algorithm: str = "HS256"\n'
                '    access_token_expire_minutes: int = 15\n'
                '    refresh_token_expire_days: int = 7\n'
                '    class Config:\n'
                '        env_file = ".env"\n'
            )

        if basename == "database.py":
            return (
                'from sqlalchemy import create_engine\n'
                'from sqlalchemy.orm import sessionmaker, DeclarativeBase\n'
                'from config import Settings\n\n'
                'settings = Settings()\n'
                'engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})\n'
                'SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)\n\n'
                'class Base(DeclarativeBase): pass\n\n'
                'def get_db():\n'
                '    db = SessionLocal()\n'
                '    try: yield db\n'
                '    finally: db.close()\n'
            )

        if basename == "auth.py":
            return (
                'from datetime import datetime, timedelta\n'
                'import jwt\n'
                'from fastapi import Depends, HTTPException, status\n'
                'from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials\n'
                'from config import Settings\n\n'
                'settings = Settings()\n'
                'security = HTTPBearer()\n\n'
                'def create_access_token(user_id: int) -> str:\n'
                '    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)\n'
                '    return jwt.encode({"sub": str(user_id), "exp": expire}, settings.jwt_secret, algorithm=settings.jwt_algorithm)\n\n'
                'def create_refresh_token(user_id: int) -> str:\n'
                '    expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)\n'
                '    return jwt.encode({"sub": str(user_id), "exp": expire, "type": "refresh"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)\n\n'
                'def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:\n'
                '    try:\n'
                '        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])\n'
                '        return int(payload["sub"])\n'
                '    except jwt.ExpiredSignatureError:\n'
                '        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")\n'
                '    except jwt.InvalidTokenError:\n'
                '        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")\n'
            )

        # Model files
        if "model" in path_lower:
            if "user" in path_lower:
                return (
                    'from sqlalchemy import Column, Integer, String, Boolean, DateTime, func\n'
                    'from database import Base\n\n'
                    'class User(Base):\n'
                    '    __tablename__ = "users"\n'
                    '    id = Column(Integer, primary_key=True, index=True)\n'
                    '    username = Column(String(100), unique=True, nullable=False)\n'
                    '    email = Column(String(255), unique=True, nullable=False)\n'
                    '    password_hash = Column(String(255), nullable=False)\n'
                    '    is_active = Column(Boolean, default=True)\n'
                    '    created_at = Column(DateTime, server_default=func.now())\n'
                    '    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())\n'
                )
            if "role" in path_lower:
                return (
                    'from sqlalchemy import Column, Integer, String, Text\n'
                    'from database import Base\n\n'
                    'class Role(Base):\n'
                    '    __tablename__ = "roles"\n'
                    '    id = Column(Integer, primary_key=True, index=True)\n'
                    '    name = Column(String(50), unique=True, nullable=False)\n'
                    '    description = Column(Text, default="")\n'
                )
            if "device" in path_lower:
                return (
                    'from sqlalchemy import Column, Integer, String, DateTime, func\n'
                    'from database import Base\n\n'
                    'class Device(Base):\n'
                    '    __tablename__ = "devices"\n'
                    '    id = Column(Integer, primary_key=True, index=True)\n'
                    '    name = Column(String(200), nullable=False)\n'
                    '    ip = Column(String(45), nullable=False)\n'
                    '    status = Column(String(20), default="unknown")\n'
                    '    created_at = Column(DateTime, server_default=func.now())\n'
                )
            if "alert" in path_lower:
                return (
                    'from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func\n'
                    'from database import Base\n\n'
                    'class Alert(Base):\n'
                    '    __tablename__ = "alerts"\n'
                    '    id = Column(Integer, primary_key=True, index=True)\n'
                    '    device_id = Column(Integer, ForeignKey("devices.id"))\n'
                    '    rule_name = Column(String(200), nullable=False)\n'
                    '    message = Column(Text, nullable=False)\n'
                    '    severity = Column(String(20), default="medium")\n'
                    '    created_at = Column(DateTime, server_default=func.now())\n'
                )
            if "audit" in path_lower:
                return (
                    'from sqlalchemy import Column, Integer, String, DateTime, func\n'
                    'from sqlalchemy.dialects.postgresql import JSONB\n'
                    'from database import Base\n\n'
                    'class AuditLog(Base):\n'
                    '    __tablename__ = "audit_logs"\n'
                    '    id = Column(Integer, primary_key=True, index=True)\n'
                    '    user_id = Column(Integer, nullable=False)\n'
                    '    action = Column(String(100), nullable=False)\n'
                    '    target_type = Column(String(50))\n'
                    '    target_id = Column(Integer)\n'
                    '    detail = Column(JSONB, default={})\n'
                    '    ip = Column(String(45))\n'
                    '    created_at = Column(DateTime, server_default=func.now())\n'
                )
            return f"from sqlalchemy import Column, Integer, String\nfrom database import Base\n\nclass Placeholder(Base):\n    __tablename__ = \"placeholder\"\n    id = Column(Integer, primary_key=True)\n"

        # Schema files
        if "schema" in path_lower:
            if "user" in path_lower:
                return (
                    'from pydantic import BaseModel, EmailStr\n\n'
                    'class UserCreate(BaseModel):\n'
                    '    username: str\n'
                    '    email: str\n'
                    '    password: str\n\n'
                    'class UserResponse(BaseModel):\n'
                    '    id: int\n'
                    '    username: str\n'
                    '    email: str\n'
                    '    is_active: bool\n'
                    '    class Config: from_attributes = True\n\n'
                    'class UserUpdate(BaseModel):\n'
                    '    username: str | None = None\n'
                    '    email: str | None = None\n'
                )
            if "auth" in path_lower:
                return (
                    'from pydantic import BaseModel\n\n'
                    'class LoginRequest(BaseModel):\n'
                    '    username: str\n'
                    '    password: str\n\n'
                    'class TokenResponse(BaseModel):\n'
                    '    access_token: str\n'
                    '    refresh_token: str\n'
                    '    token_type: str = "bearer"\n\n'
                    'class RefreshRequest(BaseModel):\n'
                    '    refresh_token: str\n'
                )
            if "device" in path_lower:
                return (
                    'from pydantic import BaseModel\n\n'
                    'class DeviceCreate(BaseModel):\n'
                    '    name: str\n'
                    '    ip: str\n\n'
                    'class DeviceResponse(BaseModel):\n'
                    '    id: int\n'
                    '    name: str\n'
                    '    ip: str\n'
                    '    status: str\n'
                    '    class Config: from_attributes = True\n'
                )
            if "alert" in path_lower:
                return (
                    'from pydantic import BaseModel\n\n'
                    'class AlertResponse(BaseModel):\n'
                    '    id: int\n'
                    '    device_id: int\n'
                    '    rule_name: str\n'
                    '    message: str\n'
                    '    severity: str\n'
                    '    class Config: from_attributes = True\n'
                )
            if "common" in path_lower:
                return (
                    'from pydantic import BaseModel\n\n'
                    'class PaginationParams(BaseModel):\n'
                    '    page: int = 1\n'
                    '    page_size: int = 20\n\n'
                    'class ErrorResponse(BaseModel):\n'
                    '    code: int\n'
                    '    message: str\n'
                    '    detail: str | None = None\n'
                )
            return f"from pydantic import BaseModel\n\nclass PlaceholderSchema(BaseModel):\n    pass\n"

        # Route files
        if "route" in path_lower:
            if "auth" in path_lower:
                return (
                    'from fastapi import APIRouter, Depends, HTTPException\n'
                    'from schemas.auth import LoginRequest, TokenResponse, RefreshRequest\n'
                    'from services.auth_service import AuthService\n'
                    'from database import get_db\n'
                    'from sqlalchemy.orm import Session\n\n'
                    'router = APIRouter()\n\n'
                    '@router.post("/login", response_model=TokenResponse)\n'
                    'def login(data: LoginRequest, db: Session = Depends(get_db)):\n'
                    '    service = AuthService(db)\n'
                    '    return service.login(data.username, data.password)\n\n'
                    '@router.post("/refresh", response_model=TokenResponse)\n'
                    'def refresh(data: RefreshRequest, db: Session = Depends(get_db)):\n'
                    '    service = AuthService(db)\n'
                    '    return service.refresh(data.refresh_token)\n'
                )
            if "user" in path_lower:
                return (
                    'from fastapi import APIRouter, Depends\n'
                    'from schemas.user import UserCreate, UserResponse, UserUpdate\n'
                    'from services.user_service import UserService\n'
                    'from auth import get_current_user\n'
                    'from database import get_db\n'
                    'from sqlalchemy.orm import Session\n\n'
                    'router = APIRouter()\n\n'
                    '@router.get("", response_model=list[UserResponse])\n'
                    'def list_users(db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):\n'
                    '    return UserService(db).list_users()\n\n'
                    '@router.post("", response_model=UserResponse)\n'
                    'def create_user(data: UserCreate, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):\n'
                    '    return UserService(db).create_user(data)\n\n'
                    '@router.put("/{id}", response_model=UserResponse)\n'
                    'def update_user(id: int, data: UserUpdate, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):\n'
                    '    return UserService(db).update_user(id, data)\n\n'
                    '@router.delete("/{id}")\n'
                    'def delete_user(id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):\n'
                    '    UserService(db).delete_user(id)\n'
                    '    return {"status": "ok"}\n'
                )
            if "device" in path_lower:
                return (
                    'from fastapi import APIRouter, Depends\n'
                    'from schemas.device import DeviceCreate, DeviceResponse\n'
                    'from services.device_service import DeviceService\n'
                    'from auth import get_current_user\n'
                    'from database import get_db\n'
                    'from sqlalchemy.orm import Session\n\n'
                    'router = APIRouter()\n\n'
                    '@router.get("", response_model=list[DeviceResponse])\n'
                    'def list_devices(db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):\n'
                    '    return DeviceService(db).list_devices()\n\n'
                    '@router.post("", response_model=DeviceResponse)\n'
                    'def create_device(data: DeviceCreate, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):\n'
                    '    return DeviceService(db).create_device(data)\n'
                )
            if "alert" in path_lower:
                return (
                    'from fastapi import APIRouter, Depends\n'
                    'from schemas.alert import AlertResponse\n'
                    'from services.alert_service import AlertService\n'
                    'from auth import get_current_user\n'
                    'from database import get_db\n'
                    'from sqlalchemy.orm import Session\n\n'
                    'router = APIRouter()\n\n'
                    '@router.get("", response_model=list[AlertResponse])\n'
                    'def list_alerts(db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):\n'
                    '    return AlertService(db).list_alerts()\n'
                )

        # Service files
        if "service" in path_lower:
            svc_name = basename.replace(".py", "").replace("_service", "")
            svc_class = "".join(w.capitalize() for w in svc_name.split("_")) + "Service"
            return (
                f'from sqlalchemy.orm import Session\n\n'
                f'class {svc_class}:\n'
                f'    def __init__(self, db: Session):\n'
                f'        self.db = db\n'
            )

        # Middleware files
        if "middleware" in path_lower and "auth" in path_lower:
            return (
                'from fastapi import Request, HTTPException\n'
                'from auth import get_current_user\n\n'
                'async def auth_middleware(request: Request, call_next):\n'
                '    if request.url.path.startswith("/api/auth/"):\n'
                '        return await call_next(request)\n'
                '    try:\n'
                '        await get_current_user(request)\n'
                '    except HTTPException:\n'
                '        raise\n'
                '    return await call_next(request)\n'
            )

        # Default fallback
        return f"# {basename}\n# Auto-generated placeholder module\n\n"

    # ── Frontend Handlers ────────────────────────────────────

    def _handle_frontend_plan(self, prompt: str, max_tokens: int) -> str:
        return (
            "frontend/package.json\n"
            "frontend/tsconfig.json\n"
            "frontend/vite.config.ts\n"
            "frontend/index.html\n"
            "frontend/src/main.tsx\n"
            "frontend/src/App.tsx\n"
            "frontend/src/api/client.ts\n"
            "frontend/src/types/api.ts\n"
            "frontend/src/types/user.ts\n"
            "frontend/src/types/device.ts\n"
            "frontend/src/types/alert.ts\n"
            "frontend/src/auth/AuthProvider.tsx\n"
            "frontend/src/routes/AppRoutes.tsx\n"
            "frontend/src/pages/Dashboard.tsx\n"
            "frontend/src/pages/Login.tsx\n"
            "frontend/src/pages/Users.tsx\n"
            "frontend/src/pages/Devices.tsx\n"
            "frontend/src/pages/Alerts.tsx\n"
            "frontend/src/components/Layout.tsx\n"
            "frontend/src/components/ProtectedRoute.tsx\n"
        )

    def _handle_frontend_code(self, prompt: str, max_tokens: int) -> str:
        file_match = re.search(r'(?:当前文件|current_file|file_path)[:\s]*(\S+)', prompt)
        file_path = "frontend/src/App.tsx"
        if file_match:
            candidate = file_match.group(1).strip()
            if "/" in candidate or "." in candidate:
                file_path = candidate
        code = self._generate_frontend_file(file_path)
        return f"```tool\n@write_file {file_path}\n{code}\n```"

    def _generate_frontend_file(self, file_path: str) -> str:
        basename = file_path.split("/")[-1]
        path_lower = file_path.lower()

        if basename == "package.json":
            return '{\n  "name": "security-platform-frontend",\n  "version": "0.1.0",\n  "dependencies": {\n    "react": "^18.2.0",\n    "react-dom": "^18.2.0",\n    "react-router-dom": "^6.20.0",\n    "axios": "^1.6.0"\n  },\n  "devDependencies": {\n    "@types/react": "^18.2.0",\n    "typescript": "^5.3.0",\n    "vite": "^5.0.0",\n    "@vitejs/plugin-react": "^4.2.0"\n  }\n}\n'

        if basename == "tsconfig.json":
            return '{\n  "compilerOptions": {\n    "target": "ES2020",\n    "module": "ESNext",\n    "moduleResolution": "bundler",\n    "jsx": "react-jsx",\n    "strict": true,\n    "esModuleInterop": true,\n    "skipLibCheck": true,\n    "forceConsistentCasingInFileNames": true\n  },\n  "include": ["src"]\n}\n'

        if basename == "vite.config.ts":
            return 'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\nexport default defineConfig({\n  plugins: [react()],\n  server: { port: 3000, proxy: { "/api": "http://localhost:8000" } }\n});\n'

        if basename == "index.html":
            return '<!DOCTYPE html>\n<html lang="zh-CN">\n<head><meta charset="UTF-8" /><title>Security Platform</title></head>\n<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>\n</html>\n'

        if basename == "main.tsx":
            return (
                'import React from "react";\nimport ReactDOM from "react-dom/client";\n'
                'import { BrowserRouter } from "react-router-dom";\nimport App from "./App";\n'
                'import { AuthProvider } from "./auth/AuthProvider";\n\n'
                'ReactDOM.createRoot(document.getElementById("root")!).render(\n'
                '  <React.StrictMode>\n    <BrowserRouter>\n'
                '      <AuthProvider>\n        <App />\n      </AuthProvider>\n'
                '    </BrowserRouter>\n  </React.StrictMode>\n);\n'
            )

        if basename == "App.tsx":
            return (
                'import AppRoutes from "./routes/AppRoutes";\n'
                'import Layout from "./components/Layout";\n\n'
                'export default function App() {\n'
                '  return <Layout><AppRoutes /></Layout>;\n}\n'
            )

        # Types
        if "types" in path_lower and "api" in path_lower:
            return (
                'export interface ApiResponse<T> { code: number; message: string; data: T; }\n'
                'export interface PaginationParams { page: number; page_size: number; }\n'
                'export interface PaginatedResponse<T> { items: T[]; total: number; page: number; page_size: number; }\n'
            )
        if "types" in path_lower and "user" in path_lower:
            return (
                'export interface User { id: number; username: string; email: string; is_active: boolean; }\n'
                'export interface UserCreate { username: string; email: string; password: string; }\n'
                'export interface UserUpdate { username?: string; email?: string; }\n'
            )
        if "types" in path_lower and "device" in path_lower:
            return 'export interface Device { id: number; name: string; ip: string; status: string; }\n'
        if "types" in path_lower and "alert" in path_lower:
            return 'export interface Alert { id: number; device_id: number; rule_name: string; message: string; severity: string; }\n'

        # API client
        if "client" in path_lower and "api" in path_lower:
            return (
                'import axios from "axios";\n\n'
                'const api = axios.create({ baseURL: "/api", timeout: 10000 });\n\n'
                'api.interceptors.request.use((config) => {\n'
                '  const token = localStorage.getItem("access_token");\n'
                '  if (token) config.headers.Authorization = `Bearer ${token}`;\n'
                '  return config;\n});\n\n'
                'api.interceptors.response.use(\n'
                '  (res) => res,\n'
                '  async (err) => {\n'
                '    if (err.response?.status === 401) {\n'
                '      localStorage.removeItem("access_token");\n'
                '      window.location.href = "/login";\n'
                '    }\n'
                '    return Promise.reject(err);\n'
                '  }\n);\n\n'
                'export default api;\n'
            )

        # Auth
        if "auth" in path_lower:
            return (
                'import React, { createContext, useContext, useState, useCallback } from "react";\n'
                'import api from "../api/client";\n\n'
                'interface AuthState { user: { id: number; username: string } | null; token: string | null; }\n'
                'const AuthContext = createContext<{ state: AuthState; login: (u: string, p: string) => Promise<void>; logout: () => void } | null>(null);\n\n'
                'export function AuthProvider({ children }: { children: React.ReactNode }) {\n'
                '  const [state, setState] = useState<AuthState>({ user: null, token: localStorage.getItem("access_token") });\n'
                '  const login = useCallback(async (username: string, password: string) => {\n'
                '    const res = await api.post("/auth/login", { username, password });\n'
                '    localStorage.setItem("access_token", res.data.access_token);\n'
                '    setState({ user: { id: 1, username }, token: res.data.access_token });\n'
                '  }, []);\n'
                '  const logout = useCallback(() => {\n'
                '    localStorage.removeItem("access_token");\n'
                '    setState({ user: null, token: null });\n'
                '  }, []);\n'
                '  return <AuthContext.Provider value={{ state, login, logout }}>{children}</AuthContext.Provider>;\n'
                '}\n\n'
                'export function useAuth() { const ctx = useContext(AuthContext); if (!ctx) throw new Error("No AuthProvider"); return ctx; }\n'
            )

        # Routes
        if "route" in path_lower:
            return (
                'import { Routes, Route, Navigate } from "react-router-dom";\n'
                'import Dashboard from "../pages/Dashboard";\n'
                'import Login from "../pages/Login";\n'
                'import Users from "../pages/Users";\n'
                'import Devices from "../pages/Devices";\n'
                'import Alerts from "../pages/Alerts";\n'
                'import ProtectedRoute from "../components/ProtectedRoute";\n\n'
                'export default function AppRoutes() {\n'
                '  return (<Routes>\n'
                '    <Route path="/login" element={<Login />} />\n'
                '    <Route path="/" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />\n'
                '    <Route path="/users" element={<ProtectedRoute><Users /></ProtectedRoute>} />\n'
                '    <Route path="/devices" element={<ProtectedRoute><Devices /></ProtectedRoute>} />\n'
                '    <Route path="/alerts" element={<ProtectedRoute><Alerts /></ProtectedRoute>} />\n'
                '    <Route path="*" element={<Navigate to="/" />} />\n'
                '  </Routes>);\n}\n'
            )

        # Pages
        if "page" in path_lower:
            page_name = basename.replace(".tsx", "").replace(".ts", "")
            if "login" in path_lower:
                return (
                    'import { useState, FormEvent } from "react";\n'
                    'import { useAuth } from "../auth/AuthProvider";\n'
                    'import { useNavigate } from "react-router-dom";\n\n'
                    'export default function Login() {\n'
                    '  const { login } = useAuth();\n'
                    '  const navigate = useNavigate();\n'
                    '  const [username, setUsername] = useState("");\n'
                    '  const [password, setPassword] = useState("");\n'
                    '  const [error, setError] = useState("");\n'
                    '  async function handleSubmit(e: FormEvent) {\n'
                    '    e.preventDefault();\n'
                    '    try { await login(username, password); navigate("/"); }\n'
                    '    catch { setError("Login failed"); }\n'
                    '  }\n'
                    '  return (<form onSubmit={handleSubmit}>\n'
                    '    <h1>Login</h1>\n'
                    '    {error && <p style={{color:"red"}}>{error}</p>}\n'
                    '    <input value={username} onChange={e => setUsername(e.target.value)} placeholder="Username" />\n'
                    '    <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Password" />\n'
                    '    <button type="submit">Login</button>\n'
                    '  </form>);\n}\n'
                )
            return (
                f'export default function {page_name}() {{\n'
                f'  return (<div><h1>{page_name}</h1><p>Page content</p></div>);\n'
                f'}}\n'
            )

        # Components
        if "component" in path_lower:
            if "layout" in path_lower:
                return (
                    'import { Outlet } from "react-router-dom";\n\n'
                    'export default function Layout({ children }: { children: React.ReactNode }) {\n'
                    '  return (<div style={{ maxWidth: 1200, margin: "0 auto", padding: 20 }}>{children}</div>);\n}\n'
                )
            if "protected" in path_lower:
                return (
                    'import { Navigate } from "react-router-dom";\n'
                    'import { useAuth } from "../auth/AuthProvider";\n\n'
                    'export default function ProtectedRoute({ children }: { children: React.ReactNode }) {\n'
                    '  const { state } = useAuth();\n'
                    '  if (!state.token) return <Navigate to="/login" />;\n'
                    '  return <>{children}</>;\n}\n'
                )
            return f'export default function {basename.replace(".tsx","")}({{ children }}) {{\n  return <div>{{children}}</div>;\n}}\n'

        return f"// {basename} - Auto-generated placeholder\n\n"

    # ── Review Handlers ──────────────────────────────────────

    def _handle_review(self, prompt: str, max_tokens: int) -> str:
        return """## 审查报告

### 总体评价: 8/10
整体架构设计合理，安全措施到位。后端代码格式正确，前端组件结构清晰。发现若干可修复问题。

### 功能完整性检查
- 用户管理模块完整 ✓
- 设备监控模块完整 ✓
- 告警管理模块完整 ✓
- 审计日志模块完整 ✓

### 代码质量评估
- 命名规范: 符合 Python/TypeScript 最佳实践
- 模块结构: 分层清晰（路由→服务→模型）
- 可维护性: 良好

### 前后端接口一致性
- API 路由定义与前端 client 调用匹配 ✓
- 请求/响应类型定义一致 ✓

### 安全漏洞扫描
- SQL 注入: 使用 ORM 参数化查询，无风险 ✓
- XSS: 前端使用 React 默认转义 ✓
- CSRF: 需在 POST 请求中添加 CSRF Token
- 认证: JWT 双令牌模式实现正确 ✓
- 密码存储: 使用 bcrypt 哈希 ✓

### 合规性检查
- 等保2.0: 审计日志完整记录 ✓
- 满足等保三级审计要求 ✓

### 改进建议
1. [高] 后端 services 应添加事务管理
2. [中] 前端应添加全局错误边界组件
3. [低] 添加 API 文档 (OpenAPI/Swagger)

```json
{
  "findings": [
    {
      "module": "backend",
      "file_path": "backend/routes/auth.py",
      "severity": "medium",
      "issue": "登录接口缺少速率限制保护",
      "fix_instruction": "添加速率限制中间件，限制 /auth/login 每 IP 每分钟最多 5 次请求",
      "repairable": true
    },
    {
      "module": "frontend",
      "file_path": "frontend/src/api/client.ts",
      "severity": "high",
      "issue": "Token 存储在 localStorage 存在 XSS 泄露风险",
      "fix_instruction": "改用 httpOnly cookie 存储 access_token，或使用内存存储 + refresh token 机制",
      "repairable": true
    },
    {
      "module": "database",
      "file_path": "database_design.md",
      "severity": "low",
      "issue": "audit_logs 表缺少索引",
      "fix_instruction": "为 audit_logs 表添加 (user_id, created_at) 复合索引",
      "repairable": false
    }
  ]
}
```"""

    def _handle_repair(self, prompt: str, max_tokens: int) -> str:
        file_match = re.search(r'(?:当前文件:|PATH：|PATH:)\s*(\S+)', prompt)
        file_path = file_match.group(1) if file_match else "backend/routes/auth.py"
        file_path = file_path.strip()

        # Return slightly improved version of the original code
        if "auth" in file_path.lower() and "route" in file_path.lower():
            code = (
                'from fastapi import APIRouter, Depends, HTTPException, Request\n'
                'from slowapi import Limiter\n'
                'from slowapi.util import get_remote_address\n'
                'from schemas.auth import LoginRequest, TokenResponse, RefreshRequest\n'
                'from services.auth_service import AuthService\n'
                'from database import get_db\n'
                'from sqlalchemy.orm import Session\n\n'
                'router = APIRouter()\n'
                'limiter = Limiter(key_func=get_remote_address)\n\n'
                '@router.post("/login", response_model=TokenResponse)\n'
                '@limiter.limit("5/minute")\n'
                'def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):\n'
                '    service = AuthService(db)\n'
                '    return service.login(data.username, data.password)\n\n'
                '@router.post("/refresh", response_model=TokenResponse)\n'
                'def refresh(data: RefreshRequest, db: Session = Depends(get_db)):\n'
                '    service = AuthService(db)\n'
                '    return service.refresh(data.refresh_token)\n'
            )
        elif "client" in file_path.lower():
            code = (
                'import axios from "axios";\n\n'
                'const api = axios.create({ baseURL: "/api", timeout: 10000, withCredentials: true });\n\n'
                'let accessToken: string | null = null;\n'
                'export function setAccessToken(token: string | null) { accessToken = token; }\n\n'
                'api.interceptors.request.use((config) => {\n'
                '  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;\n'
                '  return config;\n});\n\n'
                'api.interceptors.response.use(\n'
                '  (res) => res,\n'
                '  async (err) => {\n'
                '    if (err.response?.status === 401 && !err.config._retry) {\n'
                '      err.config._retry = true;\n'
                '      try {\n'
                '        const res = await axios.post("/api/auth/refresh", {}, { withCredentials: true });\n'
                '        setAccessToken(res.data.access_token);\n'
                '        err.config.headers.Authorization = `Bearer ${res.data.access_token}`;\n'
                '        return api(err.config);\n'
                '      } catch {\n'
                '        setAccessToken(null);\n'
                '        window.location.href = "/login";\n'
                '      }\n'
                '    }\n'
                '    return Promise.reject(err);\n'
                '  }\n);\n\n'
                'export default api;\n'
            )
        else:
            code = f"// Repaired: {file_path}\n// Fix applied: see review findings above\n\n"

        return f"PATH：{file_path}\nCODE：\n{code}"

    # ── Research / Generic Handlers ──────────────────────────

    def _handle_research_summary(self, prompt: str, max_tokens: int) -> str:
        return (
            "1. 关键发现\n"
            "- 网络安全管理平台需实现 JWT 认证和 RBAC 权限控制\n"
            "- 审计日志需完整记录操作人、时间、IP、操作类型和结果\n"
            "- OWASP Top 10 防护是合规的必要条件\n"
            "- FastAPI + React 是目前安全平台的主流技术栈\n\n"
            "2. 推荐的技术方案方向\n"
            "- 后端采用 FastAPI + SQLAlchemy + PostgreSQL\n"
            "- 前端采用 React 18 + TypeScript + Tailwind CSS\n"
            "- 认证采用 JWT 双令牌模式\n\n"
            "3. 需要注意的风险或合规要点\n"
            "- 等保2.0 要求审计日志保留 180 天\n"
            "- GDPR 要求用户数据可删除\n"
            "- 密码必须使用 bcrypt/argon2 哈希\n\n"
            "4. 可复用的模板/模式\n"
            "- RBAC 数据库设计模板\n"
            "- FastAPI 安全中间件模板\n"
            "- Docker 安全部署配置"
        )

    def _handle_generic_generation(self, prompt: str, max_tokens: int) -> str:
        # Used by DesignAgent / DeployAgent via BaseAgent graph
        return "## 通用生成输出\n\n这是 Mock LLM 的通用回退响应。请检查 prompt 关键词匹配。"

    def _handle_fallback(self, prompt: str, max_tokens: int) -> str:
        snippet = prompt[:200].replace("\n", " ")
        return f'{{"mock_fallback": true, "prompt_preview": "{snippet}", "note": "No handler matched this prompt. Add keywords to MockLLMClient._registry."}}'


# ── Singleton ──────────────────────────────────────────────────

_mock_llm: MockLLMClient | None = None


def get_mock_llm() -> MockLLMClient:
    global _mock_llm
    if _mock_llm is None:
        _mock_llm = MockLLMClient()
    return _mock_llm
