# A3 Agent间通信规范

## 1. 总则

### 1.1 目标
定义Agent之间以及Agent与Orchestrator之间的通信机制、消息格式、数据传递方式，确保管线中信息流转可靠、可追溯、无歧义。

### 1.2 设计原则
| 原则 | 说明 |
|------|------|
| **中心化路由** | 所有Agent间通信经由Orchestrator中转，Agent间不直接通信 |
| **契约化传递** | 数据通过文件系统（JSON/文档）传递，不走内存管道，确保持久化和可追溯 |
| **结构化消息** | 所有通信消息采用统一的JSON格式，包含元数据、载荷、状态 |
| **异步非阻塞** | Agent执行期间不阻塞Orchestrator，通过回调/轮询回报状态 |
| **幂等性** | 相同输入多次调用同一Agent产生相同结果，支持安全重试 |

---

## 2. 通信拓扑

### 2.1 星型拓扑

```
                    Orchestrator
                   ┌─────┼─────┐
                   │     │     │
              ┌────┤     │     ├────┐
              │    │     │     │    │
          PRD Agent  Design Agent  DB Agent  ...
              │                      │
              └──────────────────────┘
                  (不直接通信)
```

| 通信类型 | 方向 | 机制 |
|---------|------|------|
| 调度指令 | Orchestrator → Agent | 任务下发 |
| 状态回报 | Agent → Orchestrator | 回调通知 |
| 数据请求 | Agent → Orchestrator | 请求上游产物 |
| 数据响应 | Orchestrator → Agent | 返回产物路径 |
| 异常上报 | Agent → Orchestrator | 错误升级 |
| 回退通知 | Orchestrator → Agent | 携带回退原因 |

### 2.2 禁止的通信模式

| 禁止模式 | 原因 | 替代方案 |
|---------|------|---------|
| Agent→Agent直连 | 绕过Orchestrator，状态不可追踪 | 经Orchestrator中转 |
| 共享内存变量 | 并行Agent存在竞态条件 | 通过文件系统传递 |
| 全局可变状态 | 难以回滚和追溯 | 每个Agent维护自己的上下文清单文件 |
| Agent修改上游产物 | 违反上游优先原则 | 上报Orchestrator，由Orchestrator决策回退 |

---

## 3. 消息格式

### 3.1 通用消息信封

所有通信消息使用统一的信封格式：

```json
{
  "message_id": "uuid",
  "timestamp": "2026-04-30T00:00:00+08:00",
  "pipeline_id": "uuid",
  "source": "orchestrator|prd_agent|design_agent|...",
  "target": "orchestrator|prd_agent|design_agent|...",
  "type": "dispatch|result|error|data_request|data_response|rollback|heartbeat",
  "payload": {}
}
```

### 3.2 消息类型定义

#### 3.2.1 调度指令（dispatch）

Orchestrator → Agent，启动一个Agent执行任务：

```json
{
  "message_id": "uuid",
  "timestamp": "...",
  "pipeline_id": "uuid",
  "source": "orchestrator",
  "target": "backend_agent",
  "type": "dispatch",
  "payload": {
    "task_id": "uuid",
    "stage": "backend",
    "inputs": {
      "design_json": "path/to/design.json",
      "db_model_json": "path/to/db_model.json",
      "api_def_json": "path/to/api_def.json"
    },
    "config": {
      "max_retry": 3,
      "timeout": 1800,
      "mode": "full|resume|single"
    },
    "context": {
      "pipeline_state": "path/to/pipeline_state.json",
      "decision_summary": "path/to/decision_summary.json"
    }
  }
}
```

#### 3.2.2 执行结果（result）

Agent → Orchestrator，报告任务执行完成：

```json
{
  "message_id": "uuid",
  "timestamp": "...",
  "pipeline_id": "uuid",
  "source": "backend_agent",
  "target": "orchestrator",
  "type": "result",
  "payload": {
    "task_id": "uuid",
    "stage": "backend",
    "status": "succeeded|failed|partial",
    "artifacts": {
      "backend_code_dir": "path/to/backend/",
      "routes_json": "path/to/routes.json"
    },
    "metrics": {
      "files_generated": 18,
      "duration_seconds": 920,
      "llm_calls": 22,
      "fix_rounds": 1
    },
    "warnings": []
  }
}
```

#### 3.2.3 错误上报（error）

Agent → Orchestrator，报告执行失败：

```json
{
  "message_id": "uuid",
  "timestamp": "...",
  "pipeline_id": "uuid",
  "source": "backend_agent",
  "target": "orchestrator",
  "type": "error",
  "payload": {
    "task_id": "uuid",
    "stage": "backend",
    "error_level": "L1|L2|L3|L4",
    "error_code": "STATIC_ANALYSIS_FAILED|LLM_TIMEOUT|CONTRACT_MISMATCH|...",
    "error_message": "Layer1 static analysis found 5 issues after 3 fix rounds",
    "retry_count": 3,
    "fix_rounds": 3,
    "context_snapshot": "path/to/error_context.json",
    "suggestion": "rollback_to_api_agent|manual_intervention"
  }
}
```

#### 3.2.4 数据请求（data_request）

Agent → Orchestrator，请求获取上游产物（仅在特殊场景，如回退后重新执行时）：

```json
{
  "message_id": "uuid",
  "timestamp": "...",
  "pipeline_id": "uuid",
  "source": "frontend_agent",
  "target": "orchestrator",
  "type": "data_request",
  "payload": {
    "request_id": "uuid",
    "requested_artifacts": ["api_def_json", "design_json"],
    "reason": "resume_from_partial"
  }
}
```

#### 3.2.5 数据响应（data_response）

Orchestrator → Agent，返回请求的产物路径：

```json
{
  "message_id": "uuid",
  "timestamp": "...",
  "pipeline_id": "uuid",
  "source": "orchestrator",
  "target": "frontend_agent",
  "type": "data_response",
  "payload": {
    "request_id": "uuid",
    "artifacts": {
      "api_def_json": "path/to/api_def.json",
      "design_json": "path/to/design.json"
    }
  }
}
```

#### 3.2.6 回退通知（rollback）

Orchestrator → Agent，通知上游Agent需要重新执行：

```json
{
  "message_id": "uuid",
  "timestamp": "...",
  "pipeline_id": "uuid",
  "source": "orchestrator",
  "target": "api_agent",
  "type": "rollback",
  "payload": {
    "rollback_from": "backend_agent",
    "reason": "接口定义与代码无法衔接：IUserManager.list_users缺少role_id参数",
    "original_inputs": {
      "design_json": "path/to/design.json",
      "db_model_json": "path/to/db_model.json"
    },
    "additional_context": "Backend生成UserService时发现需要role_id参数用于权限过滤，但api_def.json中list_users未定义此参数",
    "affected_downstream": ["backend", "frontend"]
  }
}
```

#### 3.2.7 心跳（heartbeat）

Agent → Orchestrator，长时间运行的Agent定期报告进度：

```json
{
  "message_id": "uuid",
  "timestamp": "...",
  "pipeline_id": "uuid",
  "source": "backend_agent",
  "target": "orchestrator",
  "type": "heartbeat",
  "payload": {
    "task_id": "uuid",
    "stage": "backend",
    "progress": {
      "current_step": "generating_user_service",
      "steps_completed": 4,
      "steps_total": 7,
      "percentage": 57
    },
    "status": "running",
    "eta_seconds": 480
  }
}
```

---

## 4. 数据传递机制

### 4.1 文件系统传递（主通道）

所有产物和中间数据通过文件系统传递，Orchestrator只传递**路径引用**，不传递文件内容。

| 数据类型 | 文件格式 | 传递方式 |
|---------|---------|---------|
| 文档产物 | .docx | 文件路径 |
| 结构化数据 | .json | 文件路径 |
| SQL脚本 | .sql | 文件路径 |
| 代码文件 | .py / .vue / .js | 目录路径 |
| 上下文清单 | .json | 文件路径 |
| 检查报告 | .json | 文件路径 |

**目录规范**：
```
output/{pipeline_id}/
├── artifacts/                  # 最终交付物
│   ├── docs/                   # 文档
│   │   ├── prd.docx
│   │   ├── design.docx
│   │   ├── db.docx
│   │   └── api.docx
│   ├── database/               # 数据库产物
│   │   ├── init.sql
│   │   └── db_model.json
│   ├── api/                    # 接口产物
│   │   └── api_def.json
│   ├── backend/                # 后端代码
│   │   └── ...
│   ├── frontend/               # 前端代码
│   │   └── ...
│   └── devops/                 # 部署配置
│       └── ...
├── intermediate/               # 中间产物
│   ├── design.json
│   ├── routes.json
│   └── ...
├── state/                      # 管线状态
│   ├── pipeline_state.json
│   ├── decision_summary.json
│   └── agent_contexts/         # 各Agent上下文清单
│       ├── backend_context.json
│       └── frontend_context.json
├── logs/                       # 日志
│   ├── pipeline.log
│   └── agent_logs/
│       ├── backend_agent.log
│       └── ...
└── errors/                     # 错误快照
    └── ...
```

### 4.2 为什么选择文件系统而非内存管道

| 维度 | 文件系统 | 内存管道（如Redis/RabbitMQ） |
|------|---------|---------------------------|
| **持久化** | 天然持久化，进程崩溃不丢失 | 需额外配置持久化 |
| **调试** | 直接查看文件内容 | 需工具查看队列消息 |
| **回滚** | 文件可备份回滚 | 消息消费后难以回溯 |
| **跨进程** | 无额外依赖 | 需部署消息中间件 |
| **大文件** | 路径引用，无大小限制 | 消息体大小受限 |
| **性能** | 略慢（磁盘IO） | 更快（内存） |
| **适用场景** | 本项目：生成频率低，数据量大 | 高频小消息场景 |

本项目特点：生成频率低（分钟级）、数据量大（代码文件）、需要持久化和可追溯 → **文件系统是最优选择**。

---

## 5. 通信流程

### 5.1 正常执行流程

```
Orchestrator                          Agent
    │                                   │
    │──── dispatch(task) ──────────────→│  1. 下发任务
    │                                   │
    │                                   │  2. Agent读取inputs路径
    │                                   │     加载JSON/文档
    │                                   │
    │←─── heartbeat(progress) ─────────│  3. 长任务定期回报（可选）
    │                                   │
    │                                   │  4. Agent执行，写入artifacts
    │                                   │
    │←─── result(artifacts) ───────────│  5. 完成回报
    │                                   │
    │  6. 更新pipeline_state.json       │
    │  7. 检查下游依赖就绪              │
    │  8. 调度下一个Agent              │
```

### 5.2 异常处理流程

```
Orchestrator                          Agent
    │                                   │
    │──── dispatch(task) ──────────────→│
    │                                   │
    │←─── error(L1/L2) ───────────────│  1. 上报错误
    │                                   │
    │  2. 判断异常级别                  │
    │                                   │
    │──── dispatch(same_task) ─────────→│  L1: 重试（同输入）
    │──── dispatch(fixed_task) ────────→│  L2: 修复（追加错误信息）
    │←─── result / error ─────────────│
    │                                   │
    │  3. 超过重试/修复上限              │
    │                                   │
    │──── rollback(upstream_agent) ────→│  L3: 回退到上游
    │                                   │     上游Agent携带回退原因重新执行
    │                                   │
    │  4. 回退也失败                    │
    │                                   │
    │  5. 标记MANUAL_INTERVENTION       │  L4: 人工介入
    │  6. 生成错误报告                  │
```

### 5.3 并行Agent通信流程

S4阶段Backend和Frontend并行执行：

```
Orchestrator                    Backend Agent        Frontend Agent
    │                               │                     │
    │── dispatch(backend_task) ────→│                     │
    │── dispatch(frontend_task) ─────────────────────────→│
    │                               │                     │
    │                               │  各自独立执行        │
    │                               │  消费api_def.json   │ 消费api_def.json
    │                               │                     │
    │←── heartbeat ────────────────│                     │
    │←──────────────────────────────────── heartbeat ───│
    │                               │                     │
    │←── result(backend_done) ─────│                     │
    │←──────────────────────────────────── result(f_dne) │
    │                               │                     │
    │  两个都完成后调度Validate       │                     │
```

**关键约束**：并行Agent之间无任何通信，各自独立读写自己的输出目录。

---

## 6. 错误码定义

### 6.1 通用错误码

| 错误码 | 级别 | 说明 | 处理策略 |
|--------|------|------|---------|
| `LLM_TIMEOUT` | L1 | LLM调用超时 | 重试 |
| `LLM_RATE_LIMIT` | L1 | LLM API限流 | 延迟重试 |
| `RAG_RETRIEVAL_FAILED` | L1 | RAG检索失败 | 重试（可跳过RAG降级执行） |
| `FILE_WRITE_FAILED` | L1 | 文件写入失败 | 重试 |
| `STATIC_ANALYSIS_FAILED` | L2 | 静态检查未通过 | 修复循环 |
| `JSON_FORMAT_ERROR` | L2 | 生成的JSON格式错误 | 修复循环 |
| `CONTRACT_MISMATCH` | L2 | 契约一致性校验失败 | 修复循环 |
| `CODE_GENERATION_INCOMPLETE` | L2 | 代码生成不完整 | 修复循环 |
| `RUNTIME_IMPORT_ERROR` | L2 | 运行时导入失败 | 修复循环 |
| `RUNTIME_ROUTE_MISSING` | L2 | 路由注册缺失 | 修复循环 |
| `DB_MODEL_CONFLICT` | L3 | 数据模型与代码冲突 | 回退到DB Agent |
| `API_DEF_CONFLICT` | L3 | 接口定义与实现冲突 | 回退到API Agent |
| `ARCHITECTURE_CONFLICT` | L3 | 架构决策矛盾 | 回退到Design Agent |
| `RETRY_EXHAUSTED` | L4 | 重试次数耗尽 | 人工介入 |
| `FIX_ROUNDS_EXCEEDED` | L4 | 修复轮次超过熔断阈值 | 人工介入 |
| `ROLLBACK_DEPTH_EXCEEDED` | L4 | 回退层级超限 | 人工介入 |
| `USER_INPUT_AMBIGUOUS` | L4 | 用户输入歧义 | 人工介入 |

---

## 7. 上下文传递规范

### 7.1 上下文传递链

每个Agent在执行完成后，除了产出artifacts，还需更新决策摘要，供下游Agent消费：

```
PRD Agent → 更新 decision_summary.json (需求约束)
Design Agent → 更新 decision_summary.json (架构决策 + 技术选型)
DB Agent → 更新 decision_summary.json (数据库约定)
API Agent → 更新 decision_summary.json (接口约定)
Backend Agent → 读取 decision_summary.json，产出 routes.json
Frontend Agent → 读取 decision_summary.json，消费 api_def.json
Validate Agent → 读取 decision_summary.json，校验一致性
DevOps Agent → 读取 decision_summary.json，生成配置
```

### 7.2 决策摘要格式

```json
{
  "version": 2,
  "last_updated_by": "api_agent",
  "last_updated_at": "2026-04-30T00:00:00+08:00",
  "decisions": {
    "architecture": {
      "pattern": "ABC三层架构",
      "layers": ["interface", "abstract", "device"],
      "updated_by": "design_agent"
    },
    "tech_stack": {
      "backend": "Flask + Python",
      "database": "MySQL 8.0",
      "orm": "SQLAlchemy",
      "validation": "Marshmallow",
      "websocket": "Flask-SocketIO",
      "auth": "JWT (PyJWT)",
      "frontend": "Vue 3 + Element Plus + Pinia",
      "updated_by": "design_agent"
    },
    "response_format": {
      "structure": {"code": "int", "data": "any", "message": "str"},
      "updated_by": "api_agent"
    },
    "exception_handling": {
      "class": "ServiceException",
      "usage": "ServiceException(code, message)",
      "updated_by": "api_agent"
    },
    "pagination": {
      "parser": "BaseQueryParser",
      "params": {"page": "int", "size": "int"},
      "response": {"items": "list", "total": "int", "page": "int", "size": "int"},
      "updated_by": "api_agent"
    },
    "audit": {
      "method": "decorator",
      "decorator": "@audit_log(action='xxx')",
      "updated_by": "design_agent"
    },
    "device_identification": {
      "method": "Flask Config + Factory",
      "function": "get_device_type()",
      "updated_by": "design_agent"
    },
    "database_conventions": {
      "engine": "InnoDB",
      "charset": "utf8mb4",
      "pk_type": "BIGINT AUTO_INCREMENT",
      "timestamp_columns": "created_at, updated_at",
      "soft_delete": "is_deleted TINYINT DEFAULT 0",
      "updated_by": "db_agent"
    }
  }
}
```

**更新规则**：
- 每个Agent只能新增或更新自己职责范围内的决策项
- 不得删除或修改其他Agent写入的决策项
- 通过 `updated_by` 字段追踪决策来源
- 下游Agent必须遵守上游Agent已做出的决策

### 7.3 上下文清单格式（Agent内部）

各Agent维护自己的上下文清单文件，不跨Agent共享（A1中已定义的JSON格式）：

```
state/agent_contexts/{agent_name}_context.json
```

| Agent | 上下文清单内容 |
|-------|--------------|
| Backend | completed_files, pending_files, module_routes, dependency_signatures, decision_summary引用 |
| Frontend | completed_files, component_registry, composables_registry, api_endpoints_used, decision_summary引用 |
| Validate | checked_files, issues_found, fix_history |

---

## 8. 通信安全与一致性

### 8.1 写入隔离

| 规则 | 说明 |
|------|------|
| **目录隔离** | 每个Agent只写入自己的输出目录，不得写入其他Agent的输出目录 |
| **原子写入** | 文件写入使用"写临时文件→重命名"模式，避免读到半写文件 |
| **版本标记** | 每个产物文件包含生成版本号和生成时间，避免消费过期产物 |

### 8.2 读取一致性

| 规则 | 说明 |
|------|------|
| **路径引用** | Orchestrator下发的inputs中只包含产物路径，Agent自行读取 |
| **只读上游** | Agent对上游产物只有读权限，不得修改 |
| **版本校验** | Agent读取产物时校验版本号，确保消费的是最新版本 |

### 8.3 并行安全

| 规则 | 说明 |
|------|------|
| **无共享写** | 并行Agent写入不同目录，无写冲突 |
| **共享只读** | 并行Agent共享读取api_def.json等只读产物 |
| **完成后通知** | Agent完成后通过result消息通知Orchestrator，不直接通知并行Agent |

---

## 9. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，定义星型拓扑、消息格式、文件系统传递、错误码、上下文传递 |
