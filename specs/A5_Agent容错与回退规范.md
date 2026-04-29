# A5 Agent容错与回退规范

## 1. 总则

### 1.1 目标
定义管线中各层级的容错机制、降级策略、回退级联、断路器模式、补偿事务与故障恢复流程，是A2异常处理的操作手册与纵深补充。

### 1.2 定位

| 维度 | 说明 |
|------|------|
| **与A2的关系** | A2第5章定义了异常4级（L1-L4）和回退策略总纲；A5深入每级的实现细节、补充A2未覆盖的容错领域 |
| **与A3的关系** | A3定义了7种消息类型和18个错误码；A5复用其消息格式，补充容错专用消息与错误传播规则 |
| **与A4的关系** | A4定义了快照版本链和回滚流程；A5定义何时触发回滚、回滚后的产物清理与状态恢复策略 |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **纵深防御** | 容错不依赖单层机制，从Agent内部→Agent间→管线级逐层保护 |
| **快速失败优先** | 可判定的错误立即暴露，不通过重试掩盖确定性故障 |
| **降级保核心** | 非核心功能可降级，核心管线必须完整 |
| **爆炸半径最小** | 单点故障不级联扩散，隔离影响范围 |
| **可观测可追溯** | 每次容错动作产生结构化日志，支持事后审计 |

---

## 2. Agent内部容错

A2定义了Agent级重试（MAX_RETRY=3）和修复（MAX_FIX_ROUNDS=3），本节深入Agent内部更细粒度的容错。

### 2.1 文件级容错

代码生成Agent（Backend/Frontend）按单文件粒度生成，每个文件有独立的容错周期：

```
生成文件 Fi
  │
  ├─ 成功 → 记录到completed_files，保存快照v(N+1)
  │
  └─ 失败 → 进入文件级容错
       │
       ├─ L1类：LLM超时/限流/文件写入冲突
       │    → 文件级重试，MAX_FILE_RETRY=2
       │    → 重试仍失败 → 纳入Agent级L1重试计数
       │
       ├─ L2类：静态检查失败/JSON格式错误
       │    → 文件级修复，MAX_FILE_FIX=2
       │    → 修复仍失败 → 纳入Agent级L2修复计数
       │
       └─ L3/L4类：直接上报Orchestrator，不做文件级容错
```

**关键区别**：

| 维度 | Agent级（A2） | 文件级（A5） |
|------|-------------|-------------|
| 粒度 | 整个Agent任务 | 单个文件 |
| 计数器 | retry_count / fix_rounds | file_retry_count / file_fix_count |
| 上下文 | 保持/追加错误信息 | 保持同文件输入，不追加 |
| 失败升级 | 上报Orchestrator | 纳入Agent级计数 |

**文件级与Agent级计数关系**：

```
Agent级retry_count = Σ(文件级file_retry失败次数)
Agent级fix_rounds  = Σ(文件级file_fix失败次数)

当Agent级累计超过A2定义的MAX_RETRY/MAX_FIX_ROUNDS时，上报Orchestrator
```

### 2.2 LLM调用级容错

每次LLM调用是Agent执行的最小单元，需独立处理：

| 故障类型 | 错误码 | 处理策略 | 最大重试 |
|---------|--------|---------|---------|
| LLM API超时 | `LLM_TIMEOUT` | 指数退避重试：2s → 4s → 8s | 3次 |
| LLM API限流 | `LLM_RATE_LIMIT` | 读取Retry-After头，按指定时间等待 | 3次 |
| LLM输出截断 | `LLM_OUTPUT_TRUNCATED` | 自动续写（追加"请继续"） | 2次 |
| LLM输出格式错误 | `LLM_FORMAT_ERROR` | 解析已输出部分+重试缺失部分 | 2次 |
| LLM内容安全拒绝 | `LLM_CONTENT_FILTERED` | 标记为不可重试，上报Agent级L4 | 0次 |

**LLM调用级重试不计入Agent级重试计数**——LLM基础设施故障是外部依赖问题，不应消耗Agent自身的重试配额。

**输出截断续写规则**：

```
1. 检测finish_reason="length"
2. 追加续写Prompt："请从上次中断处继续，不要重复已输出的内容"
3. 拼接两次输出，去除重叠部分
4. 若续写仍截断 → 第二次续写
5. 两次续写仍截断 → 文件级L2修复（可能是Token不足，需拆分生成）
```

### 2.3 Skill调用级容错

Skill是Agent的执行器，Skill失败不等同于Agent失败：

```
Skill调用失败
  │
  ├─ Skill返回可恢复错误（如临时文件冲突）
  │    → Skill级重试，MAX_SKILL_RETRY=1
  │
  ├─ Skill返回不可恢复错误（如输入JSON Schema不匹配）
  │    → Agent尝试修正输入后重新调用（1次）
  │
  └─ Agent修正后仍失败
       → 上报Orchestrator，标记为Agent级异常
```

---

## 3. 降级策略

A2未覆盖降级场景。当L1-L3容错手段均不奏效时，降级是避免管线完全失败的保底手段。

### 3.1 降级分级

| 级别 | 名称 | 触发条件 | 效果 |
|------|------|---------|------|
| **D0** | 无降级 | 正常执行 | 全功能，全质量 |
| **D1** | RAG降级 | RAG检索连续3次失败 | 跳过RAG，仅用LLM内置知识 |
| **D2** | 质量降级 | L2修复超过2轮仍未通过 | 降低检查严格度，标记warnings |
| **D3** | 功能降级 | 核心模块生成失败且无法修复 | 跳过该模块，生成骨架代码+TODO |
| **D4** | Agent降级 | Agent整体失败且回退不可行 | 由Orchestrator直接生成简化版 |

### 3.2 RAG降级（D1）

```
RAG检索失败（error_code: RAG_RETRIEVAL_FAILED）
  │
  ├─ 第1次失败 → 重试（A2 L1流程）
  ├─ 第2次失败 → 重试
  ├─ 第3次失败 → 进入RAG降级
  │    → Prompt中移除RAG检索结果部分
  │    → Prompt-Head追加："RAG知识库不可用，请基于通用知识生成"
  │    → 在result消息的warnings中标记："rag_degraded: true"
  └─ RAG恢复后（下次调用成功） → 自动退出降级
```

**RAG降级影响评估**：

| Agent | RAG角色 | 降级影响 | 可接受性 |
|-------|--------|---------|---------|
| PRD | 广度扩展 | 知识链可能不全，遗漏边界需求 | 中等影响，PRD由用户确认 |
| Design | 定向补充 | 可能缺少领域最佳实践 | 中等影响，架构决策仍基于PRD |
| DB | 定向补充 | 可能缺少MySQL特性利用 | 低影响，标准SQL即可 |
| API | 定向补充 | 可能缺少RESTful最佳实践 | 低影响 |
| Backend | 代码修复 | 修复时缺少范例检索，修复成功率降低 | 中等影响，修复轮次可能增加 |
| Frontend | UI模板检索 | UI布局可能不够专业 | 中等影响，功能不受影响 |
| Validate | 代码修复 | 同Backend | 中等影响 |

### 3.3 质量降级（D2）

当L2修复轮次达到阈值但仍未通过检查时，降低检查严格度：

```
代码检查修复循环
  │
  ├─ 第1轮修复 → 仍有问题
  ├─ 第2轮修复 → 仍有问题
  │    → 触发质量降级
  │    → 静态分析：仅保留error级别，降级warning为info
  │    → LLM审查：跳过非核心检查项（如代码风格、注释完整性）
  │    → 运行时验证：跳过非核心端点测试
  │    → 标记降级检查报告：{"quality_degraded": true, "skipped_checks": [...]}
  └─ 降级后通过 → 产出代码，附带降级检查报告
```

**不可降级的检查项**：

| 检查项 | 原因 |
|--------|------|
| Python语法错误 | 运行必崩 |
| Flask路由注册缺失 | API不可达 |
| 数据库Model字段缺失 | 数据读写失败 |
| api_def.json契约不一致 | 前后端无法对接 |

### 3.4 功能降级（D3）

当某个功能模块多次生成失败时，生成骨架代码替代：

```
模块生成失败（文件级+Agent级容错均耗尽）
  │
  ├─ 非核心模块（如日志管理、系统监控）
  │    → 生成骨架代码：
  │       - Model: 仅定义类和字段，方法体为pass
  │       - Service: 仅定义类和方法签名，方法体raise NotImplementedError
  │       - API: 仅注册路由，返回501 Not Implemented
  │    → 标记：{"module_degraded": "log_management", "reason": "generation_failed"}
  │
  └─ 核心模块（用户管理、告警管理）
       → 不降级，升级为L3回退
```

**核心/非核心模块划分**：

| 模块 | 核心性 | 降级策略 |
|------|--------|---------|
| 用户管理 | 核心 | 不降级 |
| 告警管理 | 核心 | 不降级 |
| 日志管理 | 非核心 | D3降级 |
| 系统监控 | 非核心 | D3降级 |
| 网络状态监控 | 非核心 | D3降级 |

### 3.5 Agent降级（D4）

当Agent整体失败且回退不可行（如回退深度超限）时，Orchestrator接管：

```
Agent失败 + 回退不可行
  │
  └─ Orchestrator生成简化版
       → 基于已有design.json + api_def.json
       → 生成最小可运行代码（仅基础设施+1个核心模块）
       → 标记：{"agent_degraded": "backend", "reason": "rollback_depth_exceeded"}
       → 在管线进度报告中标注降级状态
```

### 3.6 降级恢复

| 降级类型 | 恢复条件 | 恢复动作 |
|---------|---------|---------|
| D1 RAG降级 | RAG检索成功 | 自动退出降级，后续调用恢复正常 |
| D2 质量降级 | 阶段不可逆 | 标记在检查报告中，Validate阶段可重新检查 |
| D3 功能降级 | 用户手动触发 | 重新调度Agent，单阶段执行该模块 |
| D4 Agent降级 | 用户手动触发 | 断点续执行，从失败Agent重新开始 |

---

## 4. 并行故障处理

A2未覆盖S4并行阶段单方失败的处理。S4阶段Backend和Frontend完全并行，单方失败时需特殊处理。

### 4.1 单方失败场景矩阵

| Backend | Frontend | 处理策略 |
|---------|----------|---------|
| ✅ 成功 | ✅ 成功 | 正常进入S5 |
| ✅ 成功 | ❌ 失败 | 等待Frontend容错，不阻塞Backend结果 |
| ❌ 失败 | ✅ 成功 | 等待Backend容错，不阻塞Frontend结果 |
| ❌ 失败 | ❌ 失败 | 两者独立容错，均失败时整体升级 |

### 4.2 单方失败的容错流程

```
S4并行阶段
  │
  ├─ Backend完成 → Frontend仍在运行
  │    → Backend产物暂存，状态标记backend_completed
  │    → 等待Frontend（不超过Frontend超时上限）
  │
  ├─ Frontend完成 → Backend仍在运行
  │    → Frontend产物暂存，状态标记frontend_completed
  │    → 等待Backend（不超过Backend超时上限）
  │
  ├─ Backend成功 + Frontend失败
  │    → Frontend独立进入L1-L3容错
  │    → Backend产物冻结（A4冻结态），不受Frontend影响
  │    → Frontend L1/L2容错成功 → 正常进入S5
  │    → Frontend L3回退 → 可能影响Backend（见4.3）
  │    → Frontend L4 → 管线降级（Backend产物保留，Frontend D3/D4降级）
  │
  └─ 两者均失败
       → 各自独立进入L1-L3容错
       → 均L3回退 → Orchestrator协调统一回退（见4.3）
       → 均L4 → 整体升级为L4
```

### 4.3 并行回退协调

当并行Agent之一触发L3回退时，需协调双方状态：

**场景1：Frontend L3回退到API Agent**

```
1. Frontend上报L3回退到API Agent
2. Orchestrator评估回退影响：
   - Backend已基于原api_def.json生成代码
   - 回退后api_def.json可能变化
   - Backend产物可能失效
3. 协调策略：
   a. 若回退原因是Frontend特有需求（如需要WebSocket事件补充）
      → 仅回退API Agent，Backend产物保留
      → API Agent增量修改api_def.json（不改Backend已消费的部分）
   b. 若回退原因影响双方共享的契约部分
      → Backend也需回退（产物作废）
      → Orchestrator标记backend_requires_regeneration: true
      → API Agent重新生成后，Backend从S4重新开始
4. 回退通知使用A3的rollback消息格式，additional_context中标注并行影响
```

**场景2：Backend L3回退到API Agent**

```
1. Backend上报L3回退到API Agent
2. Orchestrator评估回退影响：
   - Frontend已基于原api_def.json生成代码
   - 回退后api_def.json可能变化
   - Frontend产物可能失效
3. 协调策略：
   a. 若回退原因是Backend特有需求
      → 仅回退API Agent，Frontend产物保留
      → API Agent增量修改
   b. 若影响共享契约
      → Frontend也需重新生成
      → 等同于整个S4重新开始
```

**并行回退决策矩阵**：

| 回退影响范围 | 策略 | 对另一方的影响 |
|------------|------|--------------|
| 仅影响发起方 | 单方回退 | 无影响，另一方产物保留 |
| 影响共享契约但可增量修改 | 增量回退 | 另一方需检查是否受影响 |
| 影响共享契约且需重写 | 双方回退 | 另一方产物作废，S4整体重做 |

### 4.4 并行超时不对称处理

Backend和Frontend超时上限不同（30min vs 25min），可能产生一方超时一方仍在运行的情况：

```
Frontend超时(25min) + Backend仍在运行
  │
  ├─ Frontend超时视为L1异常，进入重试
  ├─ Backend继续运行（不因Frontend超时而中断）
  └─ 若Backend也超时 → 两者独立进入各自容错流程
```

---

## 5. 回退级联与产物清理

A2第5.3节定义了回退范围表，A4第4.4节定义了快照回滚流程。本节补充回退后的产物清理规则和级联回退机制。

### 5.1 产物清理规则

当Orchestrator决定回退到某上游Agent时，当前Agent及下游的产物需按规则清理：

**产物分类**：

| 产物类型 | 清理策略 | 原因 |
|---------|---------|------|
| 代码文件（.py/.vue/.js） | 删除 | 基于旧输入生成，需重新生成 |
| 产物JSON（routes.json等） | 删除 | 基于旧代码，需重新生成 |
| 上下文快照（v(K+1)...v(N)） | 删除 | A4回滚流程 |
| 检查报告 | 删除 | 基于旧代码，无参考价值 |
| 决策摘要追加项 | 回滚 | 恢复到回退目标版本 |
| 上游产物（api_def.json等） | 保留 | 回退目标即上游，其产物需保留 |
| 错误快照 | 保留 | 用于问题分析，归档后保留 |

### 5.2 级联回退场景

当回退后上游Agent重新执行仍失败时，可能需要进一步回退：

```
Backend失败 → L3回退到API Agent
  │
  ├─ API Agent重新执行成功
  │    → Backend重新执行
  │
  └─ API Agent重新执行也失败
       → API Agent L3回退到DB Agent（rollback_depth=2）
       │
       ├─ DB Agent重新执行成功
       │    → API Agent重新执行
       │    → Backend重新执行
       │
       └─ DB Agent重新执行也失败
            → rollback_depth达到MAX_ROLLBACK_DEPTH=2
            → 升级为L4人工介入
```

**级联回退时的产物清理**：

```
回退到API Agent：
  清理范围：Backend产物 + Backend/Frontend上下文快照

级联回退到DB Agent：
  清理范围：API产物 + Backend产物 + Frontend产物 + 所有下游上下文快照
  保留范围：Design产物、PRD产物（S1-S2不回退）
```

### 5.3 产物清理执行流程

```
1. Orchestrator确定回退目标和范围
2. 生成清理清单：
   {
     "rollback_from": "backend_agent",
     "rollback_to": "api_agent",
     "artifacts_to_delete": [
       "output/{pid}/artifacts/backend/",
       "output/{pid}/intermediate/routes.json"
     ],
     "snapshots_to_delete": [
       "output/{pid}/state/agent_contexts/backend_context_v*.json",
       "output/{pid}/state/agent_contexts/frontend_context_v*.json"
     ],
     "decision_summary_rollback_to_version": 3
   }
3. 执行清理（删除文件、回滚决策摘要）
4. 验证清理完整性（确认文件已删除、决策摘要版本正确）
5. 记录清理日志
6. 调度上游Agent重新执行
```

### 5.4 回退中的决策摘要处理

决策摘要（decision_summary.json）的回滚遵循A4的快照回滚规则，补充以下细节：

| 场景 | 处理方式 |
|------|---------|
| 回退到API Agent | 撤销API Agent追加的接口约定项，保留DB Agent及之前的决策 |
| 回退到DB Agent | 撤销DB Agent追加的数据库约定项 + API Agent追加的接口约定项 |
| 并行阶段回退 | 若决策摘要已合并，撤回合并版本，恢复到S3'完成时的基线版本 |

---

## 6. 断路器与熔断

A2在Validate Agent中提到简单熔断（MAX_FIX_ROUNDS=3），本节扩展为完整的断路器模式，覆盖管线/Agent/模块三级。

### 6.1 断路器模式

采用经典的三状态断路器：

```
          成功
    ┌──────────────┐
    │              ▼
  [CLOSED] ──失败达到阈值──→ [OPEN]
    ▲                         │
    │                    冷却时间后
    │                         ▼
    └─────探测成功──── [HALF-OPEN]
                          │
                     探测失败
                          │
                          ▼
                       [OPEN]
```

| 状态 | 行为 | 转换条件 |
|------|------|---------|
| **CLOSED** | 正常执行，记录失败次数 | 连续失败次数 ≥ 阈值 → OPEN |
| **OPEN** | 拒绝执行，直接返回失败 | 冷却时间到期 → HALF-OPEN |
| **HALF-OPEN** | 允许1次探测执行 | 探测成功 → CLOSED（重置失败计数）；探测失败 → OPEN |

### 6.2 三级断路器配置

| 断路器 | 保护对象 | 失败阈值 | 冷却时间 | 超限动作 |
|--------|---------|---------|---------|---------|
| **管线级** | 整条管线 | 3次L4异常 | 10 min | 管线暂停，通知用户 |
| **Agent级** | 单个Agent | 3次连续L1/L2失败 | 5 min | Agent降级或回退 |
| **模块级** | 代码生成中的单个模块 | 2次连续生成失败 | 不冷却 | 模块D3功能降级 |

### 6.3 管线级断路器

```
管线执行过程中
  │
  ├─ 第1次L4异常 → 记录，继续（可能是局部问题）
  ├─ 第2次L4异常 → 记录，警告用户
  ├─ 第3次L4异常 → 管线级断路器OPEN
  │    → 管线暂停，所有运行中Agent收到暂停通知
  │    → 生成管线故障报告（包含所有L4异常详情）
  │    → 通知用户，等待决策
  │
  └─ 冷却10min后 → HALF-OPEN
       → 用户可选择：继续执行 / 断点续执行 / 终止管线
       → 若选择继续 → 尝试执行下一个Agent
       → 若成功 → CLOSED，管线恢复
       → 若失败 → 重新OPEN
```

### 6.4 Agent级断路器

```
Agent执行过程中
  │
  ├─ 连续3次L1失败（如LLM持续超时）
  │    → Agent级断路器OPEN
  │    → 不再重试LLM调用
  │    → 冷却5min
  │    → HALF-OPEN：尝试1次LLM调用
  │    → 成功 → CLOSED，继续执行
  │    → 失败 → 重新OPEN，升级为L3回退或L4人工
  │
  ├─ 连续3次L2修复失败
  │    → 触发D2质量降级
  │    → 降级后仍失败 → L3回退
  │
  └─ LLM基础设施故障（所有LLM调用失败）
       → 不消耗Agent重试配额
       → 直接触发管线级断路器（基础设施是全局依赖）
```

### 6.5 模块级断路器

```
Backend/Frontend逐模块生成
  │
  ├─ 模块Mi第1次生成失败 → 文件级容错
  ├─ 模块Mi第2次生成失败（文件级容错耗尽）
  │    → 模块级断路器OPEN
  │    → 非核心模块：D3功能降级（生成骨架+TODO）
  │    → 核心模块：不降级，跳过该模块继续后续模块
  │    → 管线完成后标记该模块需手动补充
  └─ 所有模块处理完毕 → 检查是否有降级模块
       → 有 → result消息中标记 {"degraded_modules": [...]}
       → 无 → 正常完成
```

### 6.6 断路器状态持久化

断路器状态随管线状态持久化到pipeline_state.json：

```json
{
  "pipeline_id": "uuid",
  "circuit_breakers": {
    "pipeline": {
      "state": "closed",
      "failure_count": 1,
      "last_failure_at": "2026-04-30T00:10:00+08:00"
    },
    "backend_agent": {
      "state": "half_open",
      "failure_count": 3,
      "opened_at": "2026-04-30T00:08:00+08:00",
      "half_open_at": "2026-04-30T00:13:00+08:00"
    },
    "backend_user_module": {
      "state": "open",
      "failure_count": 2,
      "degraded": true
    }
  }
}
```

---

## 7. 补偿与部分完成

A2/A3/A4未覆盖部分完成的补偿机制。当管线无法完整完成但部分产物有价值时，需定义补偿事务确保已产出物的一致性。

### 7.1 部分完成场景

| 场景 | 已完成 | 未完成 | 产物可用性 |
|------|--------|--------|-----------|
| S4 Backend成功，Frontend失败 | S1-S4(Backend) | S4(Frontend)+S5+S6 | 后端代码+文档可用 |
| S5 Validate部分通过 | S1-S5(部分) | S5(修复)+S6 | 代码+检查报告可用 |
| S4后用户终止管线 | S1-S4 | S5-S6 | 前后端代码+文档可用 |
| D3功能降级后完成 | 全阶段（有降级模块） | 无 | 主体可用，降级模块不可用 |

### 7.2 补偿事务定义

当管线无法完整完成时，对已完成部分执行补偿，确保产物自洽：

| 补偿事务 | 触发条件 | 执行内容 |
|---------|---------|---------|
| **前端代码补偿** | Backend成功+Frontend降级/失败 | 生成最小前端（仅布局+路由+API调用骨架），确保后端可演示 |
| **后端代码补偿** | Frontend成功+Backend降级/失败 | 生成最小后端（仅app初始化+健康检查路由），确保前端可启动 |
| **部署配置补偿** | S6未执行 | 基于已有代码生成最小Dockerfile+docker-compose.yml |
| **降级模块补偿** | D3功能降级 | 在API路由中注册降级模块的501端点，避免前端404 |
| **检查报告补偿** | S5未执行 | 对已完成代码执行仅Layer1静态分析，输出简化检查报告 |

### 7.3 部分完成的产物标记

部分完成的管线产出物需明确标记，避免用户误用：

```json
{
  "pipeline_id": "uuid",
  "completion_status": "partial",
  "completed_stages": ["prd", "design", "db", "api", "backend"],
  "failed_stages": ["frontend"],
  "compensation_applied": ["frontend_skeleton_compensation"],
  "degraded_modules": [],
  "product_integrity": {
    "backend_code": "complete",
    "frontend_code": "compensated",
    "database_sql": "complete",
    "documents": "complete",
    "devops_config": "missing",
    "validation_report": "partial_l1_only"
  },
  "warnings": [
    "Frontend代码为补偿生成，仅包含布局和路由骨架",
    "DevOps配置未生成，需手动创建",
    "未执行完整代码验证，仅Layer1静态分析"
  ]
}
```

### 7.4 补偿事务与断点续执行的衔接

补偿生成的内容是临时性的，用户后续可通过断点续执行（A2第6节）重新执行失败阶段：

```
1. 管线部分完成，补偿已应用
2. 用户修复问题（如手动修改前端代码）
3. 用户发起断点续执行：resume_from="frontend"
4. Orchestrator：
   a. 删除补偿生成的临时前端代码
   b. 保留用户手动修改的部分（如有）
   c. 调度Frontend Agent重新执行
5. Frontend完成后继续S5-S6
```

---

## 8. 容错配置

本节定义各Agent可配置的容错参数，使容错行为可调而非硬编码。

### 8.1 全局容错配置

```json
{
  "fault_tolerance": {
    "global": {
      "max_retry": 3,
      "max_fix_rounds": 3,
      "max_rollback_depth": 2,
      "retry_interval_base_ms": 1000,
      "retry_interval_max_ms": 5000,
      "circuit_breaker": {
        "pipeline_failure_threshold": 3,
        "pipeline_cooldown_minutes": 10,
        "agent_failure_threshold": 3,
        "agent_cooldown_minutes": 5
      },
      "degradation": {
        "rag_max_retries_before_degrade": 3,
        "quality_degrade_after_fix_rounds": 2,
        "module_max_failures_before_degrade": 2
      }
    }
  }
}
```

### 8.2 各Agent容错参数覆盖

各Agent可在全局配置基础上覆盖特定参数：

| Agent | 覆盖参数 | 默认值 | 覆盖值 | 原因 |
|-------|---------|--------|--------|------|
| Backend | max_file_retry | 2 | 3 | 后端文件多，依赖关系复杂 |
| Frontend | max_file_retry | 2 | 2 | 前端文件相对简单 |
| Validate | max_fix_rounds | 3 | 3 | 不覆盖，修复是核心职责 |
| DevOps | max_retry | 3 | 2 | 配置文件简单，2次足够 |

### 8.3 容错配置注入方式

容错配置通过A3的dispatch消息注入Agent：

```json
{
  "type": "dispatch",
  "payload": {
    "task_id": "uuid",
    "stage": "backend",
    "inputs": { "...": "..." },
    "config": {
      "max_retry": 3,
      "timeout": 1800,
      "mode": "full",
      "fault_tolerance": {
        "max_file_retry": 3,
        "max_file_fix": 2,
        "circuit_breaker": {
          "module_failure_threshold": 2
        }
      }
    }
  }
}
```

### 8.4 超时配置补充

A2第8节定义了各阶段默认超时，本节补充超时的容错行为配置：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `timeout_action` | 超时后的动作 | `retry`（按A2视为L1） |
| `timeout_max_retries` | 超时最大重试次数 | 1次（超时通常意味着任务过重，重试成功率低） |
| `timeout_escalation` | 超时重试失败后的升级 | `degrade`（尝试降级而非直接L3回退） |

---

## 9. 故障恢复模式

A2状态机中定义了MANUAL_INTERVENTION状态，本节补充从该状态恢复的完整流程。

### 9.1 恢复模式定义

| 模式 | 触发条件 | 自动化程度 | 用户操作 |
|------|---------|-----------|---------|
| **自动恢复** | L1/L2容错成功 | 全自动 | 无需操作 |
| **半自动恢复** | 断路器HALF-OPEN探测 | 需确认 | 确认继续/调整参数 |
| **手动恢复** | L4人工介入后 | 全手动 | 修改输入/产物/配置后重新执行 |

### 9.2 自动恢复

L1/L2容错成功后自动恢复，用户无感知：

```
Agent L1重试成功
  │
  ├─ 更新Agent状态为RUNNING
  ├─ 重置断路器失败计数
  ├─ 继续后续执行
  └─ 日志记录：recovery_type=auto, trigger=L1_retry_success
```

### 9.3 半自动恢复

断路器冷却后进入HALF-OPEN，需要Orchestrator决策是否探测：

```
Agent级断路器OPEN → 冷却5min → HALF-OPEN
  │
  ├─ 自动探测：尝试1次LLM调用
  │    → 成功 → CLOSED，Agent恢复执行
  │    → 失败 → 重新OPEN
  │
  └─ 管线级断路器OPEN → 冷却10min → HALF-OPEN
       → 需用户确认：
          a. 继续执行（从断点恢复）
          b. 调整参数后续执行（如增大超时）
          c. 终止管线（产出部分完成产物）
```

### 9.4 手动恢复流程

L4人工介入后的恢复流程：

```
L4人工介入
  │
  ├─ 1. 生成故障报告
  │     {
  │       "pipeline_id": "uuid",
  │       "failed_agent": "backend_agent",
  │       "error_level": "L4",
  │       "error_code": "FIX_ROUNDS_EXCEEDED",
  │       "error_message": "...",
  │       "attempted_actions": [
  │         {"action": "L1_retry", "count": 3, "result": "failed"},
  │         {"action": "L2_fix", "rounds": 3, "result": "failed"},
  │         {"action": "L3_rollback", "target": "api_agent", "result": "failed"}
  │       ],
  │       "current_state_snapshot": "path/to/pipeline_state.json",
  │       "artifacts_status": {
  │         "prd_doc": "complete",
  │         "design_doc": "complete",
  │         "backend_code": "partial",
  │         "frontend_code": "none"
  │       },
  │       "suggested_actions": [
  │         {"action": "modify_api_def_and_retry", "description": "修改api_def.json后重新执行Backend"},
  │         {"action": "degrade_and_continue", "description": "降级Backend后继续管线"},
  │         {"action": "abort_pipeline", "description": "终止管线，产出部分完成产物"}
  │       ]
  │     }
  │
  ├─ 2. 等待用户决策
  │     用户可能：
  │     a. 修改上游产物（如手动调整api_def.json）
  │     b. 修改容错配置（如增大max_fix_rounds）
  │     c. 选择降级继续
  │     d. 选择终止管线
  │
  └─ 3. 执行用户决策
       ├─ 修改后重试：resume_from=当前失败Agent，use_existing_artifacts=已有产物
       ├─ 降级继续：应用D3/D4降级，管线继续
       └─ 终止：应用补偿事务，产出部分完成产物
```

### 9.5 手动恢复的上下文重建

用户修改上游产物后重新执行时，需重建上下文：

```
1. 用户修改了api_def.json（手动添加了缺失的端点）
2. 断点续执行：resume_from="backend"
3. Orchestrator重建Backend上下文：
   a. 加载pipeline_state.json，确认已有产物路径
   b. 读取修改后的api_def.json
   c. 创建新的backend_context（version=1，因为从头重新生成）
   d. 调度Backend Agent，dispatch消息的config.mode="resume"
4. Backend Agent从S4重新开始执行
```

---

## 10. 错误传播与隔离

防止单点故障级联扩散，确保错误影响范围可控。

### 10.1 错误传播规则

| 传播方向 | 规则 | 原因 |
|---------|------|------|
| 下游→上游 | 仅通过Orchestrator回退通知（A3 rollback消息） | 下游不得直接修改上游产物 |
| 上游→下游 | 回退后下游产物自动失效，需清理 | 上游变更使下游产物不一致 |
| 并行→并行 | 不传播，通过Orchestrator协调 | 并行Agent无直接通信（A3） |
| Agent→管线 | 仅L4升级传播，L1-L3在Agent内部消化 | 避免局部问题影响全局 |

### 10.2 错误隔离机制

| 隔离维度 | 机制 | 说明 |
|---------|------|------|
| **Agent隔离** | 目录隔离（A3第8.1节） | Agent只写自己的输出目录，故障不污染其他Agent产物 |
| **模块隔离** | 模块级断路器（6.5节） | 单模块失败不阻断其他模块生成 |
| **阶段隔离** | 快照版本链（A4第4节） | 各阶段上下文独立，回滚精确到阶段 |
| **调用隔离** | LLM调用级容错不计入Agent重试（2.2节） | LLM基础设施故障不消耗Agent配额 |

### 10.3 错误爆炸半径控制

```
单点故障发生
  │
  ├─ LLM调用失败 → 爆炸半径：当前文件
  │    → 调用级重试，不影响其他文件
  │
  ├─ 文件生成失败 → 爆炸半径：当前模块
  │    → 文件级容错，不影响其他模块
  │
  ├─ 模块生成失败 → 爆炸半径：当前Agent
  │    → 模块级断路器/D3降级，不影响并行Agent
  │
  ├─ Agent失败 → 爆炸半径：当前阶段
  │    → Agent级断路器/回退，不影响已完成阶段
  │
  └─ 管线级故障 → 爆炸半径：整条管线
       → 管线级断路器/补偿，保护已完成产物
```

### 10.4 错误日志隔离

各层级的错误日志独立存储，互不干扰：

```
output/{pipeline_id}/
├── logs/
│   ├── pipeline.log              # 管线级日志（Orchestrator写入）
│   └── agent_logs/
│       ├── backend_agent.log     # Agent级日志
│       └── frontend_agent.log
└── errors/
    ├── pipeline_errors.json      # L4管线级错误汇总
    ├── backend_agent_errors.json # Agent级错误明细
    └── frontend_agent_errors.json
```

**错误日志格式**：

```json
{
  "error_id": "uuid",
  "timestamp": "2026-04-30T00:15:00+08:00",
  "pipeline_id": "uuid",
  "agent": "backend_agent",
  "level": "L2",
  "error_code": "STATIC_ANALYSIS_FAILED",
  "message": "Layer1 static analysis found 3 issues",
  "blast_radius": "file",
  "affected_files": ["app/services/user.py"],
  "recovery_action": "file_fix_round_1",
  "recovery_result": "success"
}
```

### 10.5 防止错误连锁的策略

| 策略 | 实现方式 | 防护场景 |
|------|---------|---------|
| **重试上限** | A2 MAX_RETRY=3 | 防止无限重试消耗资源 |
| **回退深度上限** | A2 MAX_ROLLBACK_DEPTH=2 | 防止回退级联无限向上 |
| **断路器** | 本规范第6节 | 防止持续失败耗尽系统资源 |
| **降级** | 本规范第3节 | 防止非核心故障阻塞核心流程 |
| **产物清理** | 本规范第5节 | 防止基于错误输入的产物污染后续阶段 |
| **独立计数** | LLM调用级与Agent级分离（2.2节） | 防止基础设施故障被误判为Agent故障 |

---

## 11. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，定义Agent内部容错、降级策略、并行故障处理、回退级联、断路器、补偿事务、容错配置、故障恢复、错误传播与隔离 |
