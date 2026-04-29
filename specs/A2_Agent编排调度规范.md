# A2 Agent编排调度规范

## 1. 总则

### 1.1 目标
定义Orchestrator如何调度各Agent，包括调度顺序、并行策略、状态流转、异常处理、重试与回退机制。

### 1.2 设计原则
| 原则 | 说明 |
|------|------|
| **DAG驱动** | 管线定义为有向无环图（DAG），节点为Agent，边为数据依赖 |
| **最大并行** | 无依赖关系的Agent尽可能并行执行，缩短管线总耗时 |
| **契约驱动** | 前后端以api_def.json为唯一契约并行开发，由Validate校验一致性 |
| **Orchestrator单点调度** | 所有调度决策由Orchestrator集中做出，Agent不自主启动 |

---

## 2. 管线DAG定义

### 2.1 完整DAG

```
用户输入
  │
  ▼
[PRD Agent] ───────────────────────────────────── S1 串行
  │ prd.docx
  ▼
[Design Agent] ────────────────────────────────── S2 串行
  │ design.docx + design.json
  ▼
[DB Agent] ────────────────────────────────────── S3 串行
  │ db.sql + db_model.json
  ▼
[API Agent] ───────────────────────────────────── S3' 串行
  │ api_def.json（唯一契约）
  ├──────────────────────────────┐
  ▼                              ▼
[Backend Agent]            [Frontend Agent] ───── S4 并行
  消费api_def.json作实现规范   消费api_def.json作调用依据
  │ backend_code                │ frontend_code
  │ routes.json(验证产物)       │
  ├──────────────────────────────┤
  ▼                              ▼
[Validate Agent] ──────────────────────────────── S5 串行
  │ 含契约一致性校验
  │ validated_code
  ▼
[DevOps Agent] ────────────────────────────────── S6 串行
```

**契约驱动并行原理**：

前端依赖的是API路由契约（"调哪个接口、传什么参数、返回什么格式"），而非后端代码实现。api_def.json由API Agent产出，是前后端共享的唯一契约：

```
                    api_def.json（契约）
                   ┌──────┴──────┐
                   ▼             ▼
            [Backend Agent]  [Frontend Agent]
            消费api_def.json  消费api_def.json
            作为实现规范      作为API调用依据
            产出routes.json   产出前端代码
            (验证产物)        (API调用与契约对齐)
                   │             │
                   └──────┬──────┘
                          ▼
                   [Validate Agent]
                   校验routes.json是否与api_def.json一致
                   校验前端API调用是否与api_def.json一致
```

### 2.2 阶段定义

| 阶段 | Agent | 调度模式 | 前置依赖 | 输入产物 |
|------|-------|---------|---------|---------|
| S1 | PRD | 串行 | 无 | 用户自然语言 |
| S2 | Design | 串行 | S1 | prd.docx |
| S3 | DB | 串行 | S2 | design.json |
| S3' | API | 串行 | S3 | db_model.json |
| S4 | Backend + Frontend | **完全并行** | S3' | api_def.json（共享契约） |
| S5 | Validate | 串行 | S4 | backend_code + frontend_code + routes.json + api_def.json |
| S6 | DevOps | 串行 | S5 | validated_code + db.sql |

### 2.3 并行可行性分析

| 并行对 | 分析 | 结论 |
|--------|------|------|
| DB → API | API强依赖db_model.json的字段定义，强行并行收益低 | **串行**：DB → API |
| Backend ↔ Frontend | 前端依赖的是api_def.json（API契约），而非routes.json（后端验证产物）。api_def.json在S3'已产出，前后端可基于同一契约并行开发 | **完全并行** |

**契约驱动并行的关键洞察**：

| 产物 | 产出者 | 内容 | 角色 |
|------|--------|------|------|
| api_def.json | API Agent | 接口路径、HTTP方法、参数、响应格式 | **唯一契约（Contract）** |
| routes.json | Backend Agent | Flask实际注册的路由 | **验证产物** |

前端需要的是"调哪个接口、传什么参数、返回什么格式"——全部在api_def.json中。routes.json只是后端实现的副产物，用于Validate阶段校验"后端是否按契约实现了所有路由"。

**最终调度策略**：

| 阶段 | 调度方式 | 说明 |
|------|---------|------|
| S3→S3' | **串行**：DB → API | API强依赖db_model.json，串行更清晰 |
| S4 | **完全并行**：Backend ↔ Frontend | 共享api_def.json契约，各自独立开发 |
| S5 | 串行：Validate → DevOps | 必须检查通过后才生成部署 |

---

## 3. 调度状态机

### 3.1 管线状态

```
PENDING → RUNNING → COMPLETED
                  → FAILED → RETRYING → RUNNING
                            → ROLLBACK → PENDING
                            → SKIPPED → COMPLETED(partial)
                            → MANUAL_INTERVENTION
```

### 3.2 Agent状态

```
IDLE → DISPATCHED → RUNNING → SUCCEEDED
                            → FAILED → RETRYING → RUNNING
                                      → ESCALATED(上报Orchestrator)
```

### 3.3 状态流转规则

| 当前状态 | 触发条件 | 目标状态 | 动作 |
|---------|---------|---------|------|
| PENDING | 前置阶段全部COMPLETED | RUNNING | 更新stage_status，调度Agent |
| RUNNING | Agent返回成功 | COMPLETED | 记录artifacts路径，解锁下游 |
| RUNNING | Agent返回失败 | FAILED | 进入异常处理流程 |
| FAILED | 重试次数 < MAX_RETRY | RETRYING | 重新调度Agent |
| FAILED | 重试次数 ≥ MAX_RETRY | MANUAL_INTERVENTION | 通知用户 |
| COMPLETED | — | — | 检查下游依赖是否就绪 |

---

## 4. 调度算法

### 4.1 调度决策流程

```
Orchestrator主循环:
  1. 检查pipeline状态
  2. 遍历所有Agent，找到: status=PENDING 且 所有前置Agent status=COMPLETED 的Agent
  3. 若找到多个可调度Agent → 按并行策略处理
  4. 若找到1个 → 串行调度
  5. 若无 → 等待或检查异常
  6. 所有Agent COMPLETED → 管线完成
```

### 4.2 调度配置

```json
{
  "pipeline": {
    "stages": [
      {
        "name": "prd",
        "agents": ["prd_agent"],
        "mode": "serial",
        "depends_on": []
      },
      {
        "name": "design",
        "agents": ["design_agent"],
        "mode": "serial",
        "depends_on": ["prd"]
      },
      {
        "name": "db",
        "agents": ["db_agent"],
        "mode": "serial",
        "depends_on": ["design"]
      },
      {
        "name": "api",
        "agents": ["api_agent"],
        "mode": "serial",
        "depends_on": ["db"],
        "note": "API强依赖db_model.json，串行"
      },
      {
        "name": "backend",
        "agents": ["backend_agent"],
        "mode": "parallel_with",
        "parallel_target": "frontend",
        "depends_on": ["api"],
        "inputs": ["design_json", "db_model_json", "api_def_json"],
        "note": "消费api_def.json作为实现规范，产出routes.json作为验证产物"
      },
      {
        "name": "frontend",
        "agents": ["frontend_agent"],
        "mode": "parallel_with",
        "parallel_target": "backend",
        "depends_on": ["api"],
        "inputs": ["design_json", "api_def_json"],
        "note": "消费api_def.json作为API调用依据，与Backend完全并行"
      },
      {
        "name": "validate",
        "agents": ["validate_agent"],
        "mode": "serial",
        "depends_on": ["backend", "frontend"],
        "inputs": ["backend_code", "frontend_code", "routes_json", "api_def_json"],
        "note": "含契约一致性校验：routes.json vs api_def.json，前端API调用 vs api_def.json"
      },
      {
        "name": "devops",
        "agents": ["devops_agent"],
        "mode": "serial",
        "depends_on": ["validate"]
      }
    ]
  }
}
```

---

## 5. 异常处理与回退

### 5.1 异常分级

| 级别 | 定义 | 示例 | 处理策略 |
|------|------|------|---------|
| **L1 可重试** | 临时性错误，重试可能成功 | LLM API超时、RAG检索超时、文件写入冲突 | 自动重试，MAX_RETRY=3 |
| **L2 可修复** | 生成结果不正确但可修正 | 代码静态检查失败、JSON格式错误、接口签名不一致 | 自动修复，MAX_FIX_ROUNDS=3 |
| **L3 需回退** | 当前阶段无法修复，需回退到上游 | 数据模型与接口定义冲突、架构决策矛盾 | 回退到上游Agent，携带错误信息 |
| **L4 需人工** | 系统无法自动解决 | 重试/修复/回退均失败、需求歧义无法自动判断 | 标记MANUAL_INTERVENTION，通知用户 |

### 5.2 回退策略

```
Agent执行失败
  │
  ├─ L1: 重试（同Agent，同输入）
  │     MAX_RETRY=3，间隔递增：1s → 3s → 5s
  │
  ├─ L2: 修复（同Agent，修正输入）
  │     基于错误信息构建修复Prompt
  │     MAX_FIX_ROUNDS=3
  │
  ├─ L3: 回退（上游Agent，携带错误信息）
  │     回退到最近的可回退节点
  │     保留下游已完成产物的标记（可能需要重新生成）
  │     MAX_ROLLBACK_DEPTH=2（最多回退2层）
  │
  └─ L4: 人工介入
        暂停管线，生成错误报告
        用户确认后继续或终止
```

### 5.3 回退范围

| 失败Agent | 可回退到 | 回退条件 |
|-----------|---------|---------|
| Backend | API Agent | 接口定义与代码无法衔接 |
| Backend | DB Agent | 数据模型与代码无法衔接 |
| Frontend | API Agent | api_def.json契约无法满足前端需求 |
| Validate | Backend/Frontend Agent | 代码修复超过熔断阈值或契约不一致 |
| DevOps | Validate Agent | 部署配置与验证结果冲突 |

**不可回退场景**：
- PRD和Design阶段不回退（用户输入和架构决策应由用户确认）
- 回退超过MAX_ROLLBACK_DEPTH=2时升级为L4人工介入

### 5.4 重试与回退的上下文管理

| 场景 | 上下文处理 |
|------|-----------|
| **L1重试** | 保持原有输入不变，直接重试 |
| **L2修复** | 在原输入中追加错误信息，构建修复Prompt |
| **L3回退** | 清除当前Agent及下游的artifacts记录，向上游Agent注入回退原因 |
| **L4人工** | 保存完整管线快照（pipeline_state.json），用户可检查后决定继续 |

---

## 6. 部分管线执行

### 6.1 断点续执行

支持从指定阶段重新开始，跳过已完成的阶段：

```json
{
  "resume_from": "api",
  "use_existing_artifacts": {
    "prd_doc": "path/to/prd.docx",
    "design_doc": "path/to/design.docx",
    "design_json": "path/to/design.json",
    "db_doc": "path/to/db.docx",
    "db_sql": "path/to/init.sql",
    "db_model_json": "path/to/db_model.json"
  }
}
```

### 6.2 单阶段执行

支持只执行某单个Agent，用于调试或增量生成：

```json
{
  "single_stage": "backend",
  "inputs": {
    "design_json": "path/to/design.json",
    "db_model_json": "path/to/db_model.json",
    "api_json": "path/to/api_def.json"
  }
}
```

### 6.3 条件跳过

用户可配置跳过特定阶段：

```json
{
  "skip_stages": ["devops"],
  "reason": "demo阶段不需要CI/CD配置"
}
```

---

## 7. 调度日志与可观测性

### 7.1 调度日志格式

```json
{
  "timestamp": "2026-04-29T23:50:00+08:00",
  "pipeline_id": "uuid",
  "event": "agent_dispatched|agent_succeeded|agent_failed|rollback|manual_intervention",
  "agent": "backend_agent",
  "stage": "backend",
  "details": {
    "attempt": 2,
    "error_type": "L2",
    "error_message": "Static analysis found 3 issues",
    "fix_round": 1
  }
}
```

### 7.2 管线进度报告

Orchestrator在每次状态变更后生成进度摘要：

```
管线进度: 6/9 阶段完成
├─ S1 PRD: ✅ 完成 (prd.docx)
├─ S2 Design: ✅ 完成 (design.docx, design.json)
├─ S3 DB: ✅ 完成 (db.docx, init.sql, db_model.json)
├─ S4 API: ✅ 完成 (api.docx, api_def.json)
├─ S5 Backend: 🔄 运行中 (4/7 模块完成)
├─ S5 Frontend: 🔄 运行中 (2/6 模块完成) ← 与Backend并行
├─ S6 Validate: ⏳ 等待
└─ S7 DevOps: ⏳ 等待
```

---

## 8. 超时控制

| 阶段 | 默认超时 | 说明 |
|------|---------|------|
| PRD | 5 min | 单次LLM调用+RAG检索 |
| Design | 8 min | 多次LLM调用+RAG检索 |
| DB | 10 min | 三阶段设计+SQL生成 |
| API | 8 min | 接口设计+JSON生成 |
| Backend | 30 min | 多模块逐文件生成+检查修复 |
| Frontend | 25 min | 多模块生成+检查修复 |
| Validate | 15 min | 三层检查+修复循环 |
| DevOps | 5 min | 配置文件生成 |

**超时处理**：超时视为L1异常，进入重试流程。

---

## 9. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.1 | 2026-04-29 | 修订S4为完全并行，引入契约驱动并行原理，Validate增加契约一致性校验 |
| v1.0 | 2026-04-29 | 初始版本，定义DAG调度、异常处理、回退策略 |
