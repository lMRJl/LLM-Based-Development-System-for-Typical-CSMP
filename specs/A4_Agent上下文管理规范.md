# A4 Agent上下文管理规范

## 1. 总则

### 1.1 目标
定义管线中上下文的创建、更新、传递、快照、回滚与生命周期管理，确保Agent跨调用状态一致、Token高效利用、支持精确回滚。

### 1.2 核心挑战
| 挑战 | 来源 | 影响 |
|------|------|------|
| Token窗口有限 | LLM硬限制（8K/16K/128K） | 输入过长导致截断或注意力衰减 |
| 跨调用状态断裂 | LLM无跨调用记忆 | 生成第N个文件时丢失前N-1个文件的决策 |
| 管线级状态一致性 | 多Agent并行/串行 | 决策摘要被覆盖、版本冲突 |
| 近窗口选择代价 | 全文注入vs摘要注入 | 全文太长超限，摘要太短丢失细节 |
| 回滚与快照 | 异常回退场景 | 需要恢复到之前的状态 |

### 1.3 设计原则
| 原则 | 说明 |
|------|------|
| **Token预算制** | 按LLM窗口大小分档分配Token预算，不超限注入 |
| **分级衰减** | 近窗口按依赖距离分级注入，距离越远信息越精简 |
| **快照版本链** | 每步完成后保存上下文快照，支持精确回滚 |
| **Append-Only+合并** | 并行Agent各写独立快照，Orchestrator在阶段完成后合并 |
| **生命周期管理** | 上下文经历创建→更新→冻结→归档四个阶段 |

---

## 2. Token预算三档制

### 2.1 档位定义

不同LLM上下文窗口差异大，系统定义三档Token预算，适配不同模型：

| 档位 | 窗口大小 | 代表模型 | JSON精确状态 | 决策摘要 | 近窗口全文 | 任务Prompt |
|------|---------|---------|-------------|---------|-----------|-----------|
| **紧凑** | ~8K | GPT-3.5等 | 10% | 5% | 30% | 55% |
| **标准** | ~32K | GPT-4等 | 15% | 10% | 40% | 35% |
| **宽裕** | ~128K | Claude-3等 | 15% | 10% | 40% | 35% |

### 2.2 各档位策略差异

| 维度 | 紧凑(8K) | 标准(32K) | 宽裕(128K) |
|------|----------|----------|-----------|
| JSON精确状态 | 精简字段名，压缩格式 | 标准格式 | 标准格式 |
| 决策摘要 | 仅核心5条决策 | 全部决策 | 全部决策+解释说明 |
| 近窗口 | 仅传签名，不传全文 | 1-2个依赖全文 | 3-5个依赖全文 |
| Prompt-Head | 1行目标声明 | 2-3行目标+功能点 | 完整目标+功能点+背景 |
| Prompt-Tail | 仅输出格式 | 格式+3条关键约束 | 格式+完整约束+禁止项 |

### 2.3 档位切换

```json
{
  "token_budget": {
    "tier": "standard",
    "window_size": 32768,
    "allocation": {
      "json_state": 4915,
      "decision_summary": 3277,
      "near_window": 13107,
      "prompt": 11469
    }
  }
}
```

- 档位通过管线配置指定，运行期间不变
- 当近窗口内容超过预算时，按分级衰减策略自动裁剪（见第3节）
- 各Agent可根据自身需要微调分配比例，但总量不超过档位上限

---

## 3. 近窗口分级衰减

### 3.1 依赖距离定义

按当前生成文件与已生成文件的依赖关系，定义4级距离：

| 距离 | 定义 | 注入策略 | 示例 |
|------|------|---------|------|
| **D0** | 当前文件的直接依赖 | 全文注入 | UserService依赖UserModel → UserModel全文 |
| **D1** | 同模块近邻（同层协作） | 签名+关键方法体 | RoleService之于UserService |
| **D2** | 跨模块依赖 | 仅签名 | LogService之于UserService |
| **D3+** | 间接/弱依赖 | 不注入，仅JSON中记录存在 | Config之于UserService |

### 3.2 衰减规则

```
D0: 全文（完整代码）
D1: class签名 + public方法签名 + 关键方法体（含业务逻辑的方法）
D2: class签名 + public方法签名（仅名称和参数类型）
D3: 仅在JSON状态中记录: {"exists": "LogService", "location": "app/services/log.py"}
```

### 3.3 D1关键方法体判定规则

| 判定条件 | 说明 |
|---------|------|
| 方法包含业务逻辑 | 有数据库查询/计算/条件分支 → 传方法体 |
| 方法仅是简单代理 | 只调用另一个方法 → 仅传签名 |
| 方法是CRUD标准操作 | list/get/create/update/delete → 仅传签名（模式已知） |
| 方法含特殊处理 | 权限检查/审计/设备适配 → 传方法体 |

### 3.4 Token超限时的自动裁剪

当按距离分级后仍超Token预算时，按以下优先级裁剪：

```
1. 裁剪D2的签名（保留class名，去掉方法签名）
2. 裁剪D1的方法体（仅保留签名）
3. 裁剪D0的注释和空行
4. 裁剪D0的非核心方法（保留与当前任务直接相关的方法）
5. 裁剪决策摘要中的解释说明（仅保留结论）
```

每步裁剪后重新计算Token，直到不超限。

---

## 4. 上下文快照与版本链

### 4.1 快照机制

每个Agent在完成一个原子步骤后保存上下文快照，形成版本链：

```
backend_context_dv1.json  ← 生成 app/__init__.py 后
backend_context_dv2.json  ← 生成 app/models/user.py 后
backend_context_dv3.json  ← 生成 app/services/user.py 后
...
```

> 版本链命名遵循D2 §4.2定义的`_dv{N}`后缀规则（`{产物名去掉.json}_dv{N}.json`），与D3 §5.1目录结构中的`state/agent_contexts/`路径一致。

### 4.2 快照内容

```json
{
  "version": 3,
  "agent": "backend_agent",
  "pipeline_id": "uuid",
  "timestamp": "2026-04-30T00:05:00+08:00",
  "trigger": "file_completed",
  "trigger_detail": "app/services/user.py",
  "state": {
    "completed_files": [
      "app/__init__.py",
      "app/models/user.py",
      "app/services/user.py"
    ],
    "pending_files": [
      "app/api/user.py",
      "app/services/role.py"
    ],
    "current_module": "user_management",
    "module_progress": {
      "user_management": "in_progress",
      "alert_management": "pending"
    }
  },
  "signatures": {
    "UserModel": "class User(db.Model): id:BIGINT, username:VARCHAR(64), role_id:BIGINT",
    "IUserManager": "list_users(page,size)→dict, create_user(data)→dict, ...",
    "UserService": "list_users(page,size)→dict, create_user(data)→dict, ..."
  },
  "routes": {
    "user_management": ["/api/v1/users", "/api/v1/users/<id>"]
  },
  "decision_summary_ref": "path/to/decision_summary.json",
  "token_usage": {
    "total_input": 18500,
    "total_output": 12300,
    "budget_tier": "standard"
  }
}
```

### 4.3 版本链操作

| 操作 | 说明 |
|------|------|
| **追加** | 每步完成后新建dv(N+1)，不修改dv(N) |
| **回滚** | 恢复到指定版本dv(K)，删除dv(K+1)到dv(N) |
| **比较** | diff dv(K)和dv(K+1)，定位状态变化 |
| **压缩** | 归档时只保留首版和末版，中间版压缩为diff |

### 4.4 回滚流程

```
1. Orchestrator决定回滚到Agent的某步
2. 读取目标版本快照 dv(K)
3. 删除 dv(K+1) 之后的所有快照文件
4. 删除 dv(K) 之后生成的代码文件（根据completed_files差集）
5. 用 dv(K) 的state重建Agent的上下文清单
6. 重新调度Agent从dv(K)状态继续
```

---

## 5. 决策摘要的Append-Only + 快照合并

### 5.1 串行阶段的决策追加

串行阶段（S1-S3'）的Agent按顺序执行，直接追加到共享的decision_summary.json：

```
PRD Agent → 追加需求约束
Design Agent → 追加架构决策+技术选型
DB Agent → 追加数据库约定
API Agent → 追加接口约定
```

### 5.2 并行阶段的独立快照

S4阶段Backend和Frontend并行执行，各自维护独立决策快照：

```
decision_summary_backend_dv1.json  ← Backend Agent追加后端特定决策
decision_summary_frontend_dv1.json ← Frontend Agent追加前端特定决策
```

### 5.3 合并规则

Orchestrator在S4完成后，将并行快照合并为统一版本：

```
1. 读取 decision_summary.json（S3'完成时的版本）作为基线
2. 读取两个并行快照
3. 合并规则：
   a. 不同key → 直接合并（后端决策和前端决策通常不重叠）
   b. 相同key不同值 → 冲突，由Orchestrator仲裁
   c. 相同key相同值 → 无冲突，直接取
4. 写入合并后的 decision_summary.json（版本+1）
5. 删除并行快照文件
```

### 5.4 合并冲突处理

| 冲突场景 | 示例 | 仲裁策略 |
|---------|------|---------|
| 响应格式不同 | Backend用{code,data,msg}，Frontend期望{status,result} | 以api_def.json契约为准 |
| 字段命名不同 | Backend用user_id，Frontend用userId | 以db_model.json为准（snake_case） |
| 自定义决策 | Backend新增缓存策略，Frontend新增分页大小 | 均保留，标记来源 |

---

## 6. 上下文生命周期管理

### 6.1 四阶段生命周期

```
创建 → 更新 → 冻结 → 归档
```

| 阶段 | 触发条件 | 可执行操作 | 说明 |
|------|---------|-----------|------|
| **创建** | Agent被dispatch | 初始化上下文清单 | 从pipeline_state和decision_summary加载初始状态 |
| **更新** | Agent完成一个原子步骤 | 追加快照、更新状态 | 版本链递增，每步一个快照 |
| **冻结** | Agent返回result | 只读，不可修改 | 标记frozen_at时间戳，任何写入均被拒绝 |
| **归档** | 管线全部完成 | 压缩中间版本 | 保留首版+末版+所有diff，删除中间快照文件 |

### 6.2 冻结后的访问

| 访问类型 | 是否允许 | 说明 |
|---------|---------|------|
| 读取 | 允许 | 下游Agent可读取冻结的上下文 |
| 修改 | 拒绝 | 必须通过Orchestrator回退流程解冻 |
| 追加快照 | 拒绝 | 解冻后从冻结版本继续 |
| 删除 | 仅归档阶段 | 生命周期结束前不可删除 |

### 6.3 解冻（回退场景）

```
1. Orchestrator决定回退到某Agent
2. 将该Agent的上下文从"冻结"改为"更新"状态
3. 清除冻结时间戳之后的所有快照
4. 重新调度Agent
```

### 6.4 归档压缩

管线完成后，对上下文版本链进行压缩：

| 数据 | 归档策略 |
|------|---------|
| 首版快照 | 保留（完整初始状态） |
| 末版快照 | 保留（完整最终状态） |
| 中间版本 | 仅保留diff（v(N)到v(N+1)的变化增量） |
| 并行快照 | 合并后删除独立快照 |
| 错误快照 | 保留（用于问题分析） |

---

## 7. 上下文注入组装流程

### 7.1 完整组装步骤

```
Step1: 读取管线配置，确定Token档位
Step2: 读取当前Agent的上下文清单（最新快照）
Step3: 读取decision_summary.json
Step4: 确定当前生成目标文件，计算依赖距离
Step5: 按依赖距离分级，选择近窗口文件
Step6: 估算各部分Token占用
Step7: 若超预算，按裁剪优先级降级
Step8: 组装三明治式Prompt：
       Prompt-Head → JSON → 决策摘要 → 近窗口 → Prompt-Tail
Step9: 注入LLM，执行生成
```

### 7.2 Token估算方法

| 部分 | 估算方式 |
|------|---------|
| JSON精确状态 | len(json.dumps(state)) ÷ 4（中英混合约4字符/token） |
| 决策摘要 | len(summary_text) ÷ 4 |
| 近窗口全文 | len(file_content) ÷ 4（代码约3字符/token） |
| Prompt | len(prompt_text) ÷ 4 |

预留10%安全余量，实际注入量不超过预算的90%。

---

## 8. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.1 | 2026-04-30 | A+D系列交叉审查修复：版本链命名从_v{N}统一为_dv{N}与D2/D3对齐（#A-1） |
| v1.0 | 2026-04-30 | 初始版本，定义Token三档制、分级衰减、快照版本链、决策摘要合并、生命周期管理 |
