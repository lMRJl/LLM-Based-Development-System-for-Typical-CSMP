# A1 Agent职责划分规范

## 1. 总则

### 1.1 目标
定义自动化管线中所有Agent的角色、职责边界、输入输出契约，确保各Agent各司其职、互不越界。

### 1.2 设计原则
| 原则 | 说明 |
|------|------|
| **单一职责** | 每个Agent只负责一个管线阶段，不跨阶段操作 |
| **输入契约** | Agent只消费上游交付的JSON/文档，不直接读取上游内部状态 |
| **输出契约** | Agent必须产出规范化的JSON+文档，供下游消费 |
| **Skill委托** | 代码生成/检查/修复等重复性工作委托给Skill，Agent负责编排 |
| **结构化状态注入** | Agent不依赖LLM自身记忆维持跨调用一致性，通过四层结构（JSON精确状态 + 决策摘要 + 近窗口全文 + 任务Prompt）注入LLM，结合LLM上下文窗口实现稳定记忆 |
| **Orchestrator仲裁** | Agent间不直接通信，所有跨Agent协调由Orchestrator完成 |

---

## 2. Agent清单与职责矩阵

### 2.1 Orchestrator Agent（编排Agent）

| 属性 | 说明 |
|------|------|
| **角色** | 管线总调度，全局状态管理者 |
| **职责** | 接收用户自然语言输入，按管线顺序调度各Agent，追踪管线状态，处理异常路由 |
| **输入** | 用户自然语言文本 |
| **输出** | 全部交付物（由各Agent产出，Orchestrator汇总） |
| **RAG交互** | 不直接使用RAG，由各Agent按需使用 |
| **Skill调用** | 不直接调用Skill，由各Agent按需调用 |
| **异常处理** | Agent失败时决定重试/跳过/回退/人工介入 |

**核心状态追踪**：

> 以下为Orchestrator核心状态的简化视图。完整Schema定义见D1 §7 pipeline_state.json，包含artifacts路径映射、断路器状态、重试计数、降级状态、版本追踪（artifact_versions/version_snapshots）等完整字段。

```json
{
  "pipeline_id": "uuid",
  "current_stage": "prd|design|db|api|backend|frontend|validate|devops",
  "stage_status": {
    "prd": "pending|running|completed|failed",
    "design": "pending|running|completed|failed",
    "db": "pending|running|completed|failed",
    "api": "pending|running|completed|failed",
    "backend": "pending|running|completed|failed",
    "frontend": "pending|running|completed|failed",
    "validate": "pending|running|completed|failed",
    "devops": "pending|running|completed|failed"
  },
  "artifacts": {
    "prd_doc": "path/to/prd.docx",
    "design_doc": "path/to/design.docx",
    "db_doc": "path/to/db.docx",
    "db_sql": "path/to/init.sql",
    "db_model_json": "path/to/db_model.json",
    "api_doc": "path/to/api.docx",
    "api_json": "path/to/api_def.json",
    "backend_code_dir": "path/to/backend/",
    "frontend_code_dir": "path/to/frontend/",
    "routes_json": "path/to/routes.json"
  },
  "errors": []
}
```

---

### 2.2 PRD Agent（需求文档Agent）

| 属性 | 说明 |
|------|------|
| **角色** | 需求文档生成器 |
| **职责** | 从自然语言中提取意图，通过RAG知识链扩展，填充行业标准PRD模板，输出Word文档 |
| **输入** | 用户自然语言文本 |
| **输出** | PRD文档(.docx) |
| **RAG角色** | 广度扩展（关键词→知识链展开），如"登录"→"数据库"→"DB标准"，"登录"→"输入"→"防攻击" |
| **Skill调用** | 无 |
| **不可做** | 不做架构决策，不做技术选型，不生成代码 |

**处理流程**：
```
自然语言 → LLM意图提取（输出[kw1,kw2,kw3]格式）
         → Advanced RAG知识链扩展
         → Agent结构化填充（行业标准模板 + Prompt控制格式）
         → Word文档输出
```

**意图提取格式**：
```
输入：自然语言
输出：[关键词1, 关键词2, 关键词3]
```

---

### 2.3 Design Agent（概要设计Agent）

| 属性 | 说明 |
|------|------|
| **角色** | 概要设计文档生成器 |
| **职责** | 分析PRD文档，提取需求并结构化，进行架构决策，RAG定向补充，填充概要设计模板 |
| **输入** | PRD文档(.docx) |
| **输出** | 概要设计文档(.docx) + 结构化JSON（供下游消费） |
| **RAG角色** | 深度补充（定向检索：架构模式/技术选型/安全/性能） |
| **Skill调用** | 无 |
| **不可做** | 不修改PRD内容，不做数据库表设计，不生成代码 |

**结构化JSON内容**：
```json
{
  "system_overview": {},
  "architecture": {
    "pattern": "ABC三层架构",
    "layers": ["interface", "abstract", "device"],
    "device_identification": "Flask Config + Factory",
    "audit": "Decorator"
  },
  "modules": [
    {
      "name": "user_management",
      "features": ["login", "role", "permission"],
      "dependencies": []
    }
  ],
  "tech_stack": {
    "backend": "Flask + Python",
    "database": "MySQL 8.0",
    "orm": "SQLAlchemy",
    "validation": "Marshmallow",
    "websocket": "Flask-SocketIO",
    "auth": "JWT (PyJWT)"
  }
}
```

---

### 2.4 DB Agent（数据库设计Agent）

| 属性 | 说明 |
|------|------|
| **角色** | 数据库设计文档与SQL生成器 |
| **职责** | 基于概要设计，按三阶段标准方法（概念→逻辑→物理）设计数据库 |
| **输入** | 概要设计文档(.docx) + 结构化JSON |
| **输出** | 数据库设计文档(.docx) + 建库SQL(.sql) + 数据模型JSON |
| **RAG角色** | 定向补充（MySQL 8.0特性 + 安全设备领域模型） |
| **Skill调用** | 无 |
| **不可做** | 不修改概要设计，不生成API接口，不生成代码 |

**三阶段设计法**：
```
概念设计(ER图) → 逻辑设计(关系模式 + 3NF) → 物理设计(表结构 + 索引 + 存储引擎)
```

**数据模型JSON格式**：
```json
{
  "tables": [
    {
      "name": "users",
      "columns": [
        {"name": "id", "type": "BIGINT", "pk": true, "auto_increment": true},
        {"name": "username", "type": "VARCHAR(64)", "nullable": false, "unique": true}
      ],
      "indexes": [{"name": "idx_username", "columns": ["username"]}],
      "engine": "InnoDB",
      "charset": "utf8mb4"
    }
  ],
  "relations": [
    {"from": "alerts", "to": "devices", "type": "many_to_one", "fk": "device_id"}
  ]
}
```

---

### 2.5 API Agent（接口定义Agent）

| 属性 | 说明 |
|------|------|
| **角色** | 接口定义文档与JSON生成器 |
| **职责** | 基于概要设计和数据模型，设计ABC三层接口，生成设备扩展接口 |
| **输入** | 概要设计JSON + 数据模型JSON |
| **输出** | 接口文档(.docx) + 接口定义JSON |
| **RAG角色** | 定向补充（RESTful最佳实践 + 安全设备接口模式） |
| **Skill调用** | 无 |
| **不可做** | 不修改数据库设计，不生成业务代码实现 |

**接口三层架构**：
```
ABC接口层（Interface） → 抽象实现层（AbstractClass） → 设备扩展层（DeviceImpl）
```

**接口定义JSON格式**：
```json
{
  "interfaces": [
    {
      "name": "IUserManager",
      "module": "user_management",
      "methods": [
        {
          "name": "list_users",
          "http": "GET",
          "path": "/api/v1/users",
          "params": {"page": "int", "size": "int", "keyword": "str"},
          "response": {"code": "int", "data": "list", "total": "int"},
          "abstract": true
        }
      ]
    }
  ],
  "websocket": [
    {
      "namespace": "/ws/monitor",
      "events": ["system_metrics", "network_status"]
    }
  ]
}
```

**关键约束**：
- 统一分页/过滤/排序规范（BaseQueryParser）
- WebSocket用于监控/告警实时推送（Flask-SocketIO）
- 审计：装饰器替代AOP
- 设备识别：Flask Config方案
- OpenAPI兼容延后（demo阶段非必须）

---

### 2.6 Backend Agent（后端代码生成Agent）

| 属性 | 说明 |
|------|------|
| **角色** | 后端Flask项目代码生成编排器 |
| **职责** | 从概要设计提取架构和功能模块，按模块逐文件调用Skill生成代码，记录API路由 |
| **输入** | 概要设计JSON + 数据模型JSON + 接口定义JSON |
| **输出** | 后端项目代码目录 + routes.json |
| **RAG角色** | 代码修复阶段使用（代码模式库 + Bug-Fix对库） |
| **Skill调用** | backend-code-gen（代码生成）、code-validate-repair（代码检查修复） |
| **不可做** | 不修改接口定义，不生成前端代码，不做数据库设计 |

**生成顺序**：
```
基础设施层 → 用户管理 → 告警管理 → 日志管理 → 系统监控 → 网络状态监控 → 路由注册
```

**防截断策略**：
- 单文件粒度生成，每次Prompt只生成1个文件
- 超长文件拆函数段

**结构化状态注入（四层结构）**：

Agent不依赖LLM自身的上下文记忆，而是通过四层结构主动注入状态，结合LLM的上下文窗口实现稳定的跨调用一致性：

| 层 | 职责 | 保留的信息 | Token占比 | 损失容忍度 |
|---|------|-----------|----------|-----------|
| **JSON精确状态** | 描述已生成的结构化事实 | 已完成文件、签名、类型、路由、待生成列表 | ~15% | 零容忍，必须精确 |
| **决策摘要** | 全局架构约定，贯穿始终 | 响应格式、异常类、分页规范、审计方式、设备识别方案 | ~10% | 零容忍，但信息量小 |
| **近窗口全文** | 最近依赖文件的完整代码 | 当前文件的直接依赖（Model/Interface/同模块近邻） | ~40% | 可容忍，按依赖相关性选择 |
| **任务Prompt** | 当前生成目标与约束 | 目标文件、功能点、输出格式、衔接约束、禁止项 | ~35% | 零容忍，必须完整 |

**近窗口选择策略**：
按依赖相关性选择，而非简单取"最近N个文件"：
```
当前文件：app/services/user.py
直接依赖：app/models/user.py（Model）、app/api/user_interface.py（ABC接口）
同模块近邻：app/services/role.py（同模块刚生成）
→ 近窗口 = [user_model, user_interface, role_service]
```

**三明治式Prompt结构**：

基于LLM注意力分布特性（首位效应 + 近因效应，中间位置注意力最弱），采用Prompt头尾包裹上下文的结构：

```
┌─────────────────────────────────┐
│ Prompt-Head（任务声明，锚定方向）  │  ← 首位效应：锚定生成方向
├─────────────────────────────────┤
│ JSON精确状态                     │  ← 带着目标定向阅读
│ 决策摘要                         │  ← 带着目标定向阅读
│ 近窗口全文                       │  ← 带着目标定向阅读
├─────────────────────────────────┤
│ Prompt-Tail（约束强化，收束生成）  │  ← 近因效应：收束输出格式
└─────────────────────────────────┘
```

| 维度 | Prompt-Head | Prompt-Tail |
|------|-------------|-------------|
| **粒度** | 粗粒度，"做什么" | 细粒度，"怎么做" |
| **内容** | 目标文件 + 功能点 | 输出格式 + 衔接约束 + 禁止项 |
| **作用** | 方向锚定（LLM先知道目标，再带着目标读上下文） | 结果收束（防止跑偏，强化格式要求） |
| **可省略** | 不可省略 | 复杂任务必须，简单任务可简化 |

**完整注入示例**：
```
=== 生成任务 ===
生成 app/services/user.py
目标：实现用户管理服务，继承IUserManager接口
方法：list_users / create_user / update_user / delete_user

=== 项目状态 ===
{
  "completed_files": ["app/__init__.py", "app/models/user.py", "app/models/role.py"],
  "current_module": "user_management",
  "pending": ["app/services/user.py", "app/api/user.py"],
  "dependency_signatures": {
    "UserModel": "class User(db.Model): id, username, role_id",
    "IUserManager": "list_users(page, size) → dict"
  }
}

=== 架构决策 ===
- 响应格式：{"code": int, "data": any, "message": str}
- 异常类：ServiceException(code, message)
- 分页：BaseQueryParser → page/size，返回 {items, total, page, size}
- 审计装饰器：@audit_log(action="xxx")
- 设备识别：current_device = get_device_type()

=== 依赖文件 ===
[app/models/user.py 完整代码]
[app/api/user_interface.py 完整代码]

=== 输出约束 ===
- 严格继承IUserManager的所有抽象方法
- 每个方法添加@audit_log装饰器
- 使用ServiceException处理业务异常
- 输出格式：```python:app/services/user.py
- 不要重复导入已在依赖中定义的类
```

**上下文清单（Agent维护，不依赖LLM记忆）**：
```json
{
  "project_structure": {},
  "completed_files": ["app/__init__.py", "app/models/user.py"],
  "pending_files": ["app/api/user.py", "app/services/user.py"],
  "module_routes": {
    "user_management": ["/api/v1/users", "/api/v1/users/<id>"]
  },
  "dependency_signatures": {
    "UserModel": "class User(db.Model): id, username, role",
    "IUserManager": "list_users(page, size) → dict"
  },
  "decision_summary": {
    "response_format": "{code, data, message}",
    "exception_class": "ServiceException",
    "pagination": "BaseQueryParser",
    "audit_decorator": "@audit_log",
    "device_identification": "get_device_type()"
  }
}
```

---

### 2.7 Frontend Agent（前端代码生成Agent）

| 属性 | 说明 |
|------|------|
| **角色** | 前端Vue3项目代码生成编排器 |
| **职责** | 按模块生成Vue3前端代码，基于api_def.json契约生成API调用，与后端完全并行 |
| **输入** | 概要设计JSON + 接口定义JSON（api_def.json，唯一契约） |
| **输出** | 前端项目代码目录 |
| **RAG角色** | UI模板检索（安全平台布局/交互模式/特有组件） |
| **Skill调用** | frontend-code-gen（代码生成）、code-validate-repair（代码检查修复） |
| **不可做** | 不修改后端代码，不修改API路由，不做数据库设计 |

**模块对应**：
```
login / user / alert / log / monitor / network → 6个业务模块
```

**生成顺序**：
```
基础设施 → 公共组件 → 布局 → 业务模块(6个) → 仪表盘
```

**前端上下文清单**：
```json
{
  "completed_files": [],
  "component_registry": {"UserTable": "src/components/UserTable.vue"},
  "composables_registry": {"useAuth": "src/composables/useAuth.js"},
  "api_endpoints_used": {"/api/v1/users": "user module"},
  "decision_summary": {
    "ui_framework": "Element Plus",
    "state_management": "Pinia",
    "chart_library": "ECharts",
    "realtime": "Socket.IO Client",
    "api_request": "axios with baseURL from env"
  }
}
```

**前端同样采用三明治式Prompt结构**：
```
Prompt-Head（生成哪个Vue组件，功能点）→ JSON状态 + 决策摘要 + 依赖组件代码 + api_def.json契约 → Prompt-Tail（输出格式：```vue:文件路径，组件命名约束，API调用需与api_def.json一致）
```

**契约驱动**：前端以api_def.json为唯一API调用依据，与Backend完全并行开发。不依赖routes.json（后端验证产物），由Validate阶段校验契约一致性。

---

### 2.8 Validate Agent（代码检查Agent）

| 属性 | 说明 |
|------|------|
| **角色** | 代码质量守门员 |
| **职责** | 对前后端生成代码执行三层检查+契约一致性校验，触发修复流程 |
| **输入** | 后端代码目录 + 前端代码目录 + routes.json + api_def.json |
| **输出** | 检查报告 + 修复后的代码 |
| **RAG角色** | 代码修复（代码模式库 + Bug-Fix对库检索修复范例） |
| **Skill调用** | code-validate-repair（代码检查与修复） |
| **不可做** | 不做业务逻辑修改，不修改接口定义，不修改数据库设计 |

**三层检查体系**：
```
Layer1 静态分析（ast + ruff + 自定义校验） → 自动修复
Layer2 LLM代码审查（逻辑/框架/安全/接口一致性） → 定向修复
Layer2.5 契约一致性校验（前后端 vs api_def.json） → 定向修复
Layer3 运行时验证（import → app创建 → DB初始化 → 路由注册 → API端点测试） → 定向修复
```

**契约一致性校验（Layer2.5）**：

前后端基于api_def.json并行开发后，需校验双端是否都对齐了契约：
```
1. 遍历routes.json，检查每条路由是否在api_def.json中有对应定义
2. 遍历前端代码中的API调用，检查每次调用是否与api_def.json中的路径/方法/参数一致
3. 不一致 → 标记为L2错误，定向修复对应端
```

**熔断机制**：
- MAX_FIX_ROUNDS = 3
- 超限标记 `requires_manual_intervention: true`

---

### 2.9 DevOps Agent（部署配置Agent）

| 属性 | 说明 |
|------|------|
| **角色** | CI/CD与部署配置生成器 |
| **职责** | 基于前后端代码和数据库SQL，生成容器化与CI/CD配置 |
| **输入** | project_dir + platform(github/gitlab/jenkins) + deploy_target(docker-compose/k8s) + env_config + 建库SQL |
| **输出** | Dockerfile×2 + docker-compose.yml + nginx.conf + CI流水线 + 辅助脚本 + .env.example |
| **RAG角色** | 无 |
| **Skill调用** | devops-gen |
| **不可做** | 不修改应用代码，不修改数据库设计 |

---

## 3. 职责交叉与冲突解决

### 3.1 交叉场景

| 交叉场景 | 主责Agent | 协作方式 |
|----------|-----------|----------|
| PRD中的技术需求影响架构选型 | PRD Agent记录需求，Design Agent做决策 | Orchestrator传递PRD文档给Design Agent |
| 数据库设计需要调整接口定义 | DB Agent输出数据模型，API Agent消费 | 通过JSON中间产物，不直接通信 |
| 后端代码发现接口定义不合理 | Backend Agent上报Orchestrator | Orchestrator决定是否回退到API Agent |
| 前端需要新增API端点 | Frontend Agent上报Orchestrator | Orchestrator决定是否回退到API/Backend Agent |
| 代码修复需修改数据库Schema | Validate Agent上报Orchestrator | Orchestrator决定是否回退到DB Agent |

### 3.2 冲突解决原则
1. **上游优先**：下游Agent不得直接修改上游产物，必须通过Orchestrator回退
2. **JSON为准**：当文档与JSON不一致时，以JSON中间产物为权威来源
3. **Orchestrator仲裁**：所有跨Agent的决策冲突由Orchestrator裁决

---

## 4. Agent与Skill的职责边界

| 维度 | Agent | Skill |
|------|-------|-------|
| **定位** | 编排者，负责流程控制和决策 | 执行者，负责具体工作的实现 |
| **状态** | 维护管线状态和上下文清单 | 无状态，输入→输出 |
| **RAG** | 按需调用RAG获取知识 | 不直接访问RAG |
| **通信** | 与Orchestrator通信 | 被Agent调用，不主动通信 |
| **错误处理** | 决定重试/回退/跳过策略 | 返回执行结果，含错误信息 |

---

## 5. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.3 | 2026-04-30 | A+D系列交叉审查修复：核心状态JSON添加D1 Schema引用（#X-2）、"网络状态"统一为"网络状态监控"（#A-4） |
| v1.2 | 2026-04-29 | 引入契约驱动并行，Frontend输入改为api_def.json，Validate增加Layer2.5契约校验 |
| v1.1 | 2026-04-29 | 修订"无状态生成"为"结构化状态注入"，增加四层结构与三明治式Prompt |
| v1.0 | 2026-04-29 | 初始版本，定义9个Agent角色与职责 |
