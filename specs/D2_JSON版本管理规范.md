# D2 JSON版本管理规范

## 1. 总则

### 1.1 目标
定义管线中所有JSON中间产物的版本号策略、变更检测与兼容性规则、版本链管理、回滚与恢复机制、并行阶段版本合并流程，确保JSON产物在全生命周期内可追溯、可回滚、可比对。

### 1.2 定位

| 维度 | 说明 |
|------|------|
| **与D1的关系** | D1定义了10个JSON产物的Schema（字段、类型、约束）；D2定义这些产物的版本生命周期管理——如何演进、如何兼容、如何回滚 |
| **与A4的关系** | A4定义了上下文快照的版本链（追加/回滚/比较/压缩）；D2将版本管理从上下文扩展到所有JSON产物，并定义跨产物版本协调 |
| **与A5的关系** | A5定义了回退时的产物清理规则；D2定义回退触发的版本操作（版本回退、变更记录、兼容性检查） |
| **与A3的关系** | A3定义了Agent间消息传递；D2定义消息中如何携带版本信息、版本不一致时的处理 |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **版本即真相** | 每个JSON产物通过版本号精确标识其状态，版本号是产物演进的唯一权威标识 |
| **向前兼容优先** | Schema变更以新增字段为主，避免破坏下游消费者 |
| **变更可追溯** | 每次版本变更记录原因、发起者、影响范围 |
| **版本链不可篡改** | 已生成的版本记录只可追加，不可修改或删除（归档压缩除外） |
| **最小回滚代价** | 版本粒度与回滚粒度对齐，避免回滚时产生不必要的产物废弃 |

---

## 2. 版本号体系

### 2.1 双层版本号

每个JSON产物维护两层版本号：

| 版本层 | 字段名 | 含义 | 变更时机 |
|--------|--------|------|---------|
| **Schema版本** | `version` | JSON结构的Schema版本（D1定义） | Schema字段定义变更时 |
| **数据版本** | `_data_version` | 同一Schema下数据内容的迭代版本 | 内容修改/追加时 |

**区别**：

```
Schema版本（version）：
  - design.json的version=1 → Schema结构定义不变
  - Schema变更（如新增必填字段）→ version=2
  - 由D1规范统一定义和升级

数据版本（_data_version）：
  - design.json的version=1, _data_version=3 → Schema v1下的第3次数据修改
  - 每次内容修改+1，即使Schema不变
  - 由本规范定义管理规则
```

### 2.2 版本号规则

| 维度 | Schema版本(version) | 数据版本(_data_version) |
|------|---------------------|------------------------|
| 起始值 | D1定义（当前均为1） | 1 |
| 递增规则 | 仅Schema不兼容变更时+1 | 每次数据修改+1 |
| 范围 | 正整数，通常不变 | 正整数，频繁递增 |
| 存储 | JSON顶层`version`字段 | JSON顶层`_data_version`字段 |
| 回滚 | 不可回滚（Schema不可逆） | 可回滚到任意历史版本 |

### 2.3 各产物版本特征

| 产物 | Schema版本 | 数据版本 | 版本演进特点 |
|------|-----------|---------|-------------|
| design.json | 固定1 | 低频递增 | 架构确定后极少变更 |
| db_model.json | 固定1 | 低频递增 | 数据模型确定后极少变更 |
| api_def.json | 固定1 | 中频递增 | 契约可能因回退而修订，版本变更需同步通知前后端 |
| routes.json | 固定1 | 低频递增 | 后端代码变化时同步更新 |
| decision_summary.json | 固定1 | 高频递增 | Append-Only，每次追加+1 |
| pipeline_state.json | 固定1 | 高频递增 | 每次状态变更+1 |
| {agent}_context.json | 固定1 | 高频递增 | 每个快照对应一个版本 |
| validation_report.json | 固定1 | 低频递增 | 每次检查产出 |
| error_context.json | 固定1 | 不递增 | 一次性产物，无版本链 |
| frontend_context.json | 固定1 | 高频递增 | 同{agent}_context.json |

### 2.4 _data_version字段定义

在D1定义的各JSON产物Schema中，补充以下可选字段：

```json
{
  "_data_version": {
    "type": "integer",
    "minimum": 1,
    "default": 1,
    "description": "数据内容版本号，每次修改+1"
  },
  "_data_version_log": {
    "type": "array",
    "description": "版本变更日志（仅管线级状态产物和决策摘要保留，其他产物不保留）",
    "items": {
      "type": "object",
      "required": ["version", "timestamp", "agent", "change_type"],
      "properties": {
        "version": {
          "type": "integer",
          "description": "变更后的数据版本号"
        },
        "timestamp": {
          "type": "string",
          "format": "date-time"
        },
        "agent": {
          "type": "string",
          "description": "发起变更的Agent名"
        },
        "change_type": {
          "type": "string",
          "enum": ["initial", "append", "modify", "rollback", "merge", "degradation"],
          "description": "变更类型"
        },
        "change_detail": {
          "type": "string",
          "description": "变更说明"
        },
        "previous_version": {
          "type": "integer",
          "description": "变更前的版本号（rollback时用于追溯）"
        }
      }
    }
  }
}
```

---

## 3. 变更检测与兼容性

### 3.1 变更类型分类

| 变更类型 | 代号 | 示例 | 兼容性 | 版本动作 |
|---------|------|------|--------|---------|
| 新增可选字段 | `ADD_OPT` | design.json新增`display_name`字段 | 完全兼容 | _data_version+1 |
| 新增必填字段（有默认值） | `ADD_REQ_D` | api_def.json的method新增`audit_action`（默认空） | 向后兼容 | _data_version+1 |
| 新增必填字段（无默认值） | `ADD_REQ_N` | db_model.json的columns新增`nullable`（无默认值） | 不兼容 | version+1（需D1升级） |
| 修改字段语义 | `MOD_SEM` | `version`从"数据版本"改为"Schema版本" | 不兼容 | version+1（需D1升级） |
| 修改字段值 | `MOD_VAL` | api_def.json中某接口的路径从`/users`改为`/users/` | 视影响而定 | _data_version+1 |
| 删除字段 | `DEL_FLD` | 删除某废弃字段 | 禁止 | 保留为deprecated |
| 追加数组元素 | `APP_ARR` | decision_summary.json追加决策项 | 完全兼容 | _data_version+1 |
| 修改数组元素 | `MOD_ARR` | api_def.json修改某接口的参数 | 视影响而定 | _data_version+1 |

### 3.2 兼容性判定流程

```
JSON产物发生变更
  │
  ├─ 变更仅涉及新增可选字段或有默认值的必填字段
  │    → 兼容，旧消费者忽略新字段即可
  │    → _data_version+1
  │    → 下游Agent无需重新执行
  │
  ├─ 变更涉及修改字段值或修改数组元素
  │    → 评估影响范围：
  │       a. 仅影响发起方Agent → 兼容，_data_version+1
  │       b. 影响共享契约（api_def.json） → 需通知所有消费者
  │          → 触发A5的并行回退协调（4.3节）
  │
  ├─ 变更涉及新增无默认值必填字段或修改字段语义
  │    → 不兼容，需升级Schema版本
  │    → 由D1规范升级流程处理
  │    → 旧数据需迁移脚本
  │
  └─ 变更涉及删除字段
       → 禁止，保留为deprecated
       → deprecated字段标记：{"_deprecated": true, "_deprecated_since": "v3", "_deprecated_note": "使用xxx替代"}
```

### 3.3 契约变更的特殊规则

api_def.json作为前后端唯一契约，其变更需特殊处理：

| 变更场景 | 兼容性 | 处理方式 |
|---------|--------|---------|
| 新增接口（不影响已有接口） | 兼容 | _data_version+1，Frontend可选择性调用 |
| 新增WebSocket命名空间 | 兼容 | _data_version+1，Frontend可选择性连接 |
| 修改已有接口路径 | 不兼容 | 触发并行回退协调（A5 4.3），Backend和Frontend均需重新生成 |
| 修改已有接口的HTTP方法 | 不兼容 | 同上 |
| 修改已有接口的参数（新增可选参数） | 兼容 | _data_version+1，Backend需支持但Frontend不强制传 |
| 修改已有接口的参数（新增必填参数） | 不兼容 | 同修改路径 |
| 修改已有接口的响应结构 | 视情况 | 若仅新增字段则兼容；若删除/修改字段则不兼容 |
| 删除接口 | 不兼容 | 触发并行回退协调 |

### 3.4 变更传播矩阵

定义各产物变更时需要通知的下游消费者：

| 产物变更 | 必须通知 | 可选通知 | 不通知 |
|---------|---------|---------|--------|
| design.json | DB Agent, API Agent | Backend/Frontend（通过decision_summary间接获取） | — |
| db_model.json | API Agent | Backend Agent | — |
| api_def.json | Backend Agent, Frontend Agent, Validate Agent | — | — |
| routes.json | Validate Agent | — | 其他Agent |
| decision_summary.json | 所有下游Agent | — | — |
| pipeline_state.json | Orchestrator | 用户（进度查询） | Agent |

---

## 4. 版本链管理

### 4.1 版本链结构

每个JSON产物在管线执行期间维护一条版本链：

```
design_dv1.json  ← Design Agent首次产出
design_dv2.json  ← 回退后重新产出
design_dv3.json  ← 二次修订
...
```

版本链文件命名规则：

```
{产物名去掉.json}_dv{N}.json

示例：
design_dv1.json
design_dv2.json
api_def_dv1.json
api_def_dv2.json
decision_summary_dv1.json
decision_summary_dv2.json
```

### 4.2 版本链存储路径

```
output/{pipeline_id}/
├── artifacts/                          # 最终产物（当前版本）
│   ├── design.json                     # → 符号链接到versions/下最新版本
│   ├── db_model.json
│   ├── api_def.json
│   ├── routes.json
│   └── ...
├── versions/                           # 版本链存储
│   ├── design/
│   │   ├── design_dv1.json
│   │   ├── design_dv2.json
│   │   └── design_dv3.json
│   ├── db_model/
│   │   └── db_model_dv1.json
│   ├── api_def/
│   │   ├── api_def_dv1.json
│   │   └── api_def_dv2.json
│   ├── decision_summary/
│   │   ├── decision_summary_dv1.json
│   │   ├── decision_summary_dv2.json
│   │   └── decision_summary_dv3.json
│   └── ...
└── state/
    └── agent_contexts/                 # 上下文版本链（A4定义）
        ├── backend_context_dv1.json
        ├── backend_context_dv2.json
        └── ...
```

**关键设计**：
- `artifacts/`下的文件始终指向当前生效版本（通过文件复制或符号链接）
- `versions/`下保留完整版本链
- 下游Agent读取`artifacts/`下的当前版本，不关心版本链细节

### 4.3 版本链操作

| 操作 | 说明 | 执行者 |
|------|------|--------|
| **追加（Append）** | Agent产出新版本，写入versions/并更新artifacts/ | 产出Agent |
| **回滚（Rollback）** | 指定回滚到dv(K)，更新artifacts/指向dv(K)，不删除后续版本 | Orchestrator |
| **比较（Diff）** | 对比dv(K)和dv(N)的字段级差异 | Orchestrator/调试工具 |
| **压缩（Compact）** | 归档时保留首版+末版+diff，删除中间版文件 | Orchestrator（归档阶段） |

### 4.4 追加操作流程

```
Agent完成JSON产物生成/修改
  │
  ├─ 1. 读取当前_data_version（从artifacts/下的文件）
  │     若不存在 → _data_version=0（将从1开始）
  │
  ├─ 2. 新_data_version = 当前_data_version + 1
  │
  ├─ 3. 写入新版本文件：
  │     versions/{产物名}/{产物名去掉.json}_dv{新版本号}.json
  │     内容包含 _data_version 和 _data_version_log
  │
  ├─ 4. 更新artifacts/下的当前版本：
  │     复制新版本文件到 artifacts/{产物名}.json
  │
  └─ 5. 通知下游消费者（通过A3 result消息携带版本信息）
```

### 4.5 回滚操作流程

```
Orchestrator决定回滚某产物到版本dv(K)
  │
  ├─ 1. 验证dv(K)存在于versions/目录
  │
  ├─ 2. 更新artifacts/下的当前版本：
  │     复制 versions/{产物名}/{产物名去掉.json}_dv{K}.json
  │     到 artifacts/{产物名}.json
  │
  ├─ 3. 追加一条rollback类型的版本日志：
  │     {
  │       "version": K+1,
  │       "timestamp": "...",
  │       "agent": "orchestrator",
  │       "change_type": "rollback",
  │       "change_detail": "回滚到dv{K}",
  │       "previous_version": N  // 回滚前的版本号
  │     }
  │
  ├─ 4. 不删除dv(K+1)到dv(N)的版本文件
  │     （保留完整历史，支持后续恢复到回滚前状态）
  │
  └─ 5. 通知受影响的下游Agent
```

**回滚不删除后续版本的设计理由**：

| 场景 | 若删除后续版本 | 若保留后续版本 |
|------|---------------|---------------|
| 回滚后发现是误判 | 无法恢复，需重新生成 | 可直接前滚到dv(N) |
| 需要对比回滚前后的差异 | 无法对比 | 可diff dv(K)和dv(N) |
| 问题分析需要历史数据 | 无法追溯 | 完整版本链可审计 |

### 4.6 前滚操作

当回滚后发现需要恢复到回滚前的状态时，执行前滚：

```
当前生效版本dv(K)（回滚后）
需要恢复到dv(N)（回滚前）
  │
  ├─ 1. 验证dv(N)存在于versions/目录
  ├─ 2. 更新artifacts/指向dv(N)
  ├─ 3. 追加版本日志：change_type="rollforward", previous_version=K
  └─ 4. 通知受影响的下游Agent
```

---

## 5. 版本在消息中的携带

### 5.1 result消息中的版本信息

Agent完成产出后，通过A3的result消息携带版本信息：

```json
{
  "type": "result",
  "payload": {
    "task_id": "uuid",
    "status": "SUCCESS",
    "artifacts": {
      "api_def.json": {
        "path": "output/{pid}/artifacts/api_def.json",
        "schema_version": 1,
        "data_version": 2,
        "change_summary": "新增用户管理接口的审计参数"
      }
    }
  }
}
```

### 5.2 dispatch消息中的版本约束

Orchestrator调度Agent时，可指定版本约束：

```json
{
  "type": "dispatch",
  "payload": {
    "task_id": "uuid",
    "stage": "backend",
    "inputs": {
      "api_def.json": {
        "path": "output/{pid}/artifacts/api_def.json",
        "schema_version": 1,
        "data_version": 2,
        "min_data_version": 2
      }
    },
    "config": { "...": "..." }
  }
}
```

**版本约束字段**：

| 字段 | 说明 | 使用场景 |
|------|------|---------|
| `min_data_version` | 下游Agent要求的最低数据版本 | Backend要求api_def.json至少为dv2（包含审计参数） |
| `schema_version` | 下游Agent期望的Schema版本 | 确保消费者与Schema版本匹配 |

### 5.3 版本不匹配处理

```
Agent读取输入产物时检测版本
  │
  ├─ Schema版本不匹配
  │    → 上报Orchestrator（错误码：VERSION_SCHEMA_MISMATCH）
  │    → Orchestrator决策：终止/降级/等待Schema升级
  │
  ├─ 数据版本低于min_data_version
  │    → 上报Orchestrator（错误码：VERSION_DATA_TOO_LOW）
  │    → 可能是回退后版本回退，需确认是否应重新执行上游
  │
  └─ 版本匹配
       → 正常消费
```

---

## 6. 并行阶段版本合并

### 6.1 并行版本冲突场景

S4阶段Backend和Frontend并行执行，可能产生以下版本冲突：

| 场景 | 说明 | 风险等级 |
|------|------|---------|
| 两者读取同一版本的api_def.json | 正常情况 | 无风险 |
| 两者独立修改decision_summary | 各自追加独立决策项 | 低风险（不同key） |
| 回退导致api_def.json版本变更 | 一方基于旧版本生成 | 高风险（产物不一致） |

### 6.2 decision_summary并行合并

A4第5.3节定义了决策摘要的合并规则，D2补充版本层面的合并机制：

```
S3'完成时 decision_summary.json _data_version=3
  │
  ├─ Backend追加 → decision_summary_backend_dv4.json
  ├─ Frontend追加 → decision_summary_frontend_dv4.json
  │
  └─ Orchestrator合并
       │
       ├─ 1. 读取两个并行快照
       ├─ 2. 以dv3为基线，提取Backend增量（dv3→dv4_backend）
       ├─ 3. 以dv3为基线，提取Frontend增量（dv3→dv4_frontend）
       ├─ 4. 合并增量到基线
       │    a. 不同key → 直接合并
       │    b. 相同key相同值 → 取一份
       │    c. 相同key不同值 → Orchestrator仲裁
       ├─ 5. 写入合并版本 decision_summary_dv4.json
       │    _data_version=4
       │    _data_version_log追加：change_type="merge"
       └─ 6. 更新artifacts/decision_summary.json
```

**合并后的版本日志示例**：

```json
{
  "version": 4,
  "timestamp": "2026-04-30T00:20:00+08:00",
  "agent": "orchestrator",
  "change_type": "merge",
  "change_detail": "合并Backend(dv4_backend)和Frontend(dv4_frontend)的并行快照",
  "previous_version": 3
}
```

### 6.3 api_def.json的版本一致性保障

api_def.json在S4期间不应被并行Agent修改（仅API Agent有权产出），但回退场景可能导致版本变更：

```
S4期间api_def.json被Backend消费(dv2)
  │
  ├─ Frontend触发L3回退到API Agent
  │    → API Agent重新产出api_def.json(dv3)
  │    → Backend仍基于dv2生成代码
  │
  └─ Orchestrator协调（A5 4.3节）
       ├─ 若dv3仅新增接口 → Backend兼容，不需重新生成
       │    → 通知Backend有新版本可用
       │
       └─ 若dv3修改已有接口 → Backend不兼容
            → Backend产物作废，需重新生成
            → 回滚Backend到dv2版本对应的状态
```

---

## 7. 版本与回退的协调

### 7.1 回退触发的版本操作

A5定义了回退场景和产物清理规则，D2定义回退时的版本操作：

| 回退场景 | 版本操作 | 说明 |
|---------|---------|------|
| Backend L3回退到API Agent | api_def.json回滚到Backend消费前的版本 | 确保API Agent基于正确版本重新生成 |
| Frontend L3回退到API Agent | api_def.json可能升级（API Agent重新产出） | 版本变更需通知Backend |
| 级联回退到DB Agent | db_model.json + api_def.json均回滚 | 下游所有产物版本链标记为"因回退废弃" |
| 决策摘要回滚 | 恢复到回退目标Agent完成时的版本 | 撤销回退目标之后的追加项 |

### 7.2 回退版本目标确定

```
Orchestrator决定回退到Agent X
  │
  ├─ 1. 查找Agent X完成时的管线状态快照
  │     从pipeline_state.json的变更日志中定位
  │
  ├─ 2. 从快照中提取Agent X产出物的_data_version
  │     例如：api_def_dv2 对应 API Agent完成时
  │
  ├─ 3. 执行版本回滚（4.5节流程）
  │     回滚所有下游产物到对应版本
  │
  └─ 4. 清理回退后Agent的版本链
       标记dv(K+1)到dv(N)为"回退废弃"（不删除文件）
       在版本日志中记录回退原因
```

### 7.3 回退废弃标记

回滚后不删除后续版本文件，而是标记为废弃：

```json
{
  "_data_version": 3,
  "_deprecated": true,
  "_deprecated_reason": "rollback_from_backend_L3",
  "_deprecated_at": "2026-04-30T00:25:00+08:00",
  "_superseded_by_version": 2,
  "...": "原始内容保留不变"
}
```

**废弃标记的用途**：

| 用途 | 说明 |
|------|------|
| 问题分析 | 回滚后仍可查看被废弃版本的内容 |
| 前滚恢复 | 可清除废弃标记恢复到该版本 |
| 审计追溯 | 版本链完整，每次变更可追溯 |

---

## 8. 版本比较（Diff）

### 8.1 Diff操作定义

支持两个版本之间的结构化比较，用于问题定位和变更审计：

| Diff类型 | 说明 | 输出格式 |
|---------|------|---------|
| 字段级Diff | 对比两个版本的顶层字段差异 | 新增/删除/修改的字段列表 |
| 数组级Diff | 对比数组元素的增删改 | 元素级差异列表 |
| 语义级Diff | 判断变更是否影响兼容性 | 兼容性评估结果 |

### 8.2 Diff输出格式

```json
{
  "diff_id": "uuid",
  "artifact": "api_def.json",
  "from_version": 2,
  "to_version": 3,
  "timestamp": "2026-04-30T00:30:00+08:00",
  "summary": {
    "fields_added": 2,
    "fields_removed": 0,
    "fields_modified": 1,
    "compatibility": "breaking"
  },
  "details": [
    {
      "path": "interfaces[2].methods[0].audit_action",
      "change_type": "added",
      "old_value": null,
      "new_value": "user_create",
      "compatibility_impact": "compatible"
    },
    {
      "path": "interfaces[0].methods[1].path",
      "change_type": "modified",
      "old_value": "/users",
      "new_value": "/users/",
      "compatibility_impact": "breaking"
    }
  ],
  "affected_consumers": ["backend_agent", "frontend_agent"],
  "required_actions": [
    {
      "consumer": "backend_agent",
      "action": "regenerate",
      "reason": "接口路径变更，后端路由需重新生成"
    }
  ]
}
```

### 8.3 Diff触发场景

| 场景 | 触发者 | 目的 |
|------|--------|------|
| 回退前评估 | Orchestrator | 评估回退的影响范围 |
| 版本升级后验证 | Agent | 确认变更符合预期 |
| 问题诊断 | 用户/Orchestrator | 定位哪次变更引入了问题 |
| 归档前审查 | Orchestrator | 生成版本变更摘要 |

---

## 9. 归档与压缩

### 9.1 归档策略

管线完成后对版本链进行压缩归档，减少存储占用：

| 数据 | 保留策略 | 原因 |
|------|---------|------|
| 最终版本(dv(N)) | **完整保留** | 管线产出的权威版本 |
| 首版(dv(1)) | **完整保留** | 初始状态，用于对比总变更量 |
| 中间版本(dv(2)..dv(N-1)) | **仅保留Diff** | 存储优化，需要时可从首版+Diff链重建 |
| 废弃版本 | **保留Diff+废弃原因** | 审计追溯需要 |
| 错误版本（回退产生的） | **保留Diff+错误上下文** | 问题分析需要 |

### 9.2 Diff格式（归档用）

```json
{
  "from_version": 2,
  "to_version": 3,
  "patch": [
    {
      "op": "add",
      "path": "/interfaces/2/methods/0/audit_action",
      "value": "user_create"
    },
    {
      "op": "replace",
      "path": "/interfaces/0/methods/1/path",
      "value": "/users/"
    }
  ],
  "metadata": {
    "timestamp": "2026-04-30T00:15:00+08:00",
    "agent": "api_agent",
    "change_type": "modify",
    "change_detail": "修正用户列表路径并添加审计参数"
  }
}
```

**Diff格式采用JSON Patch（RFC 6902）**，确保标准化和工具兼容性。

### 9.3 归档执行流程

```
管线状态=COMPLETED
  │
  ├─ 1. 确认所有版本链完整
  │
  ├─ 2. 对每个产物的版本链：
  │     a. 保留dv(1)和dv(N)的完整文件
  │     b. 将dv(2)..dv(N-1)转换为Diff文件
  │     c. 删除中间版本的完整文件
  │
  ├─ 3. 对废弃版本同样处理（保留Diff+废弃原因）
  │
  ├─ 4. 生成归档清单：
  │     {
  │       "pipeline_id": "uuid",
  │       "archive_timestamp": "...",
  │       "artifacts": {
  │         "api_def.json": {
  │           "final_version": 3,
  │           "total_versions": 3,
  │           "archived_diffs": [
  │             {"from": 1, "to": 2, "file": "api_def_diff_1_2.json"},
  │             {"from": 2, "to": 3, "file": "api_def_diff_2_3.json"}
  │           ]
  │         }
  │       }
  │     }
  │
  └─ 5. 验证归档完整性
       从dv(1)+Diff链重建dv(N)，与保留的完整dv(N)对比确认一致
```

### 9.4 归档后重建

需要查看历史版本时，从首版+Diff链逐步重建：

```
查看dv(2)：
  dv(1) + diff_1_2 → dv(2)

查看dv(K)：
  dv(1) + diff_1_2 + diff_2_3 + ... + diff_{K-1}_K → dv(K)
```

---

## 10. 跨产物版本协调

### 10.1 产物版本快照

管线在关键节点保存跨产物的版本快照，确保各产物版本的一致性：

| 快照点 | 包含的产物版本 | 用途 |
|--------|-------------|------|
| S3'完成 | design(dv1) + db_model(dv1) + api_def(dv1) | 前后端并行前的基线 |
| S4 Backend完成 | api_def(dv1) + routes(dv1) + backend_context(dvK) | Backend产出校验基准 |
| S4 Frontend完成 | api_def(dv1) + frontend_context(dvK) | Frontend产出校验基准 |
| S5完成 | 所有产物最终版本 | 交付基线 |

### 10.2 版本快照格式

```json
{
  "snapshot_id": "uuid",
  "pipeline_id": "uuid",
  "snapshot_point": "S3'_completed",
  "timestamp": "2026-04-30T00:10:00+08:00",
  "artifact_versions": {
    "design.json": {"schema_version": 1, "data_version": 1},
    "db_model.json": {"schema_version": 1, "data_version": 1},
    "api_def.json": {"schema_version": 1, "data_version": 1},
    "decision_summary.json": {"schema_version": 1, "data_version": 3}
  }
}
```

### 10.3 版本一致性校验

在关键节点校验产物版本一致性：

```
S5 Validate开始前
  │
  ├─ 1. 读取S3'完成时的版本快照
  ├─ 2. 读取当前artifacts/下各产物版本
  ├─ 3. 校验：
  │     a. api_def.json版本是否与Backend/Frontend消费时一致
  │     b. decision_summary是否已正确合并（S4后的版本应大于S3'时的版本）
  │     c. routes.json与api_def.json的版本是否匹配
  │
  └─ 4. 不一致时
       → 上报Orchestrator（错误码：VERSION_CONSISTENCY_VIOLATION）
       → 触发版本对齐或回退
```

### 10.4 版本对齐

当发现产物版本不一致时，执行对齐操作：

| 不一致类型 | 对齐策略 |
|-----------|---------|
| api_def.json被修改但Backend基于旧版本 | 评估兼容性（3.3节），兼容则通知，不兼容则回退Backend |
| decision_summary未合并 | 执行合并（6.2节），版本+1 |
| routes.json与api_def.json不匹配 | 重新生成routes.json（Backend级联更新） |

---

## 11. 版本与断路器的联动

### 11.1 版本变更频率监控

断路器（A5第6节）的状态可参考版本变更频率：

| 指标 | 计算 | 异常阈值 | 含义 |
|------|------|---------|------|
| 产物回滚率 | 回滚次数/总版本变更次数 | >30% | 产物质量不稳定 |
| 契约变更频率 | api_def.json的_data_version增量/S4耗时 | >2次/h | 契约设计不成熟 |
| 决策冲突率 | 合并冲突次数/合并总次数 | >20% | 并行Agent决策不一致 |

### 11.2 版本变更触发断路器

```
api_def.json在S4期间频繁变更
  │
  ├─ dv1 → dv2（回退后重新产出）
  ├─ dv2 → dv3（又一次回退）
  ├─ dv3 → dv4（第三次变更）
  │
  └─ 契约变更频率超过阈值
       → 触发Agent级断路器（API Agent）
       → API Agent断路器OPEN
       → 暂停S4，通知用户
```

---

## 12. 版本号在pipeline_state中的反映

pipeline_state.json（D1第7章）中补充版本信息追踪：

```json
{
  "pipeline_id": "uuid",
  "artifact_versions": {
    "design.json": {"schema_version": 1, "data_version": 1},
    "db_model.json": {"schema_version": 1, "data_version": 1},
    "api_def.json": {"schema_version": 1, "data_version": 2},
    "routes.json": {"schema_version": 1, "data_version": 1},
    "decision_summary.json": {"schema_version": 1, "data_version": 4},
    "backend_context.json": {"schema_version": 1, "data_version": 8},
    "frontend_context.json": {"schema_version": 1, "data_version": 6},
    "validation_report.json": null,
    "error_context.json": null
  },
  "version_snapshots": [
    {
      "snapshot_point": "S3'_completed",
      "timestamp": "2026-04-30T00:10:00+08:00",
      "artifact_versions": { "...": "..." }
    }
  ]
}
```

此字段使Orchestrator无需遍历文件系统即可了解所有产物的当前版本状态。

---

## 13. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，定义双层版本号、变更检测与兼容性、版本链管理、消息版本携带、并行版本合并、回退版本协调、版本Diff、归档压缩、跨产物版本协调、版本与断路器联动 |
