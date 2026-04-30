# P1 Prompt模板规范

## 1. 总则

### 1.1 目标
定义管线中所有 Prompt 的统一结构、模板语法、变量绑定规则、组装流程与校验机制，确保 Prompt 构建过程可复现、可追踪、可调试，为 P2（输出约束）、P3（上下文注入）、P4（分段衔接）提供模板骨架。

### 1.2 定位

| 维度 | 说明 |
|------|------|
| **与A1的关系** | A1定义了三明治式Prompt结构（Head/Tail）和四层状态注入；P1将此结构模板化，定义模板语法和组装规则 |
| **与A4的关系** | A4定义了Token三档制和上下文注入组装流程（9步）；P1定义Prompt模板如何接收上下文注入的变量并组装为最终Prompt |
| **与A5的关系** | A5定义了L2修复循环的修复Prompt策略；P1定义修复Prompt的模板格式和变量绑定 |
| **与D4的关系** | D4定义了错误数据在Prompt中的注入策略和Token预算；P1定义错误注入的模板占位符和组装规则 |
| **与P2的关系** | P1定义模板骨架，P2定义模板中输出约束段的填充规则 |
| **与P3的关系** | P1定义模板变量占位符，P3定义上下文注入变量的来源和解析逻辑 |
| **与P4的关系** | P1定义单次Prompt模板，P4定义跨次Prompt的分段衔接策略 |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **模板与数据分离** | Prompt模板是静态骨架，动态数据通过变量注入，不硬编码到模板中 |
| **声明式优先** | 模板声明"需要什么"（占位符），而非"怎么获取"（获取逻辑在组装器中） |
| **最小变异** | 同类Agent的Prompt模板结构一致，仅变量值不同，避免每个Agent一套独立模板结构 |
| **可校验可回放** | 每次组装的Prompt保存为JSON记录，包含模板ID、变量值、最终文本，支持事后审计 |
| **Token感知** | 模板定义各段的Token预算区间，组装时超限则触发裁剪而非静默截断 |

---

## 2. Prompt模板分类

### 2.1 按用途分类

| 类别 | 模板ID前缀 | 用途 | 使用者 |
|------|-----------|------|--------|
| **生成模板** | `gen_` | 正常流程的代码/文档生成 | Backend Agent、Frontend Agent、PRD Agent、Design Agent、DB Agent、API Agent、DevOps Agent |
| **修复模板** | `fix_` | L2修复循环中的定向修复 | Backend Agent、Frontend Agent、Validate Agent |
| **检查模板** | `chk_` | 代码检查与审查 | Validate Agent |
| **回退模板** | `rbk_` | L3回退时向上游注入原因 | Orchestrator → 上游Agent |
| **系统模板** | `sys_` | Agent角色声明与行为约束 | 所有Agent |

### 2.2 按Agent分类

每个Agent至少拥有1个系统模板 + 1个生成模板。代码生成类Agent额外拥有修复模板和检查模板。

| Agent | 系统模板 | 生成模板 | 修复模板 | 检查模板 |
|-------|---------|---------|---------|---------|
| PRD Agent | `sys_prd` | `gen_prd` | — | — |
| Design Agent | `sys_design` | `gen_design` | — | — |
| DB Agent | `sys_db` | `gen_db` | — | — |
| API Agent | `sys_api` | `gen_api` | — | — |
| Backend Agent | `sys_backend` | `gen_backend_file` | `fix_backend` | `chk_backend` |
| Frontend Agent | `sys_frontend` | `gen_frontend_file` | `fix_frontend` | `chk_frontend` |
| Validate Agent | `sys_validate` | — | `fix_validate` | `chk_validate` |
| DevOps Agent | `sys_devops` | `gen_devops` | — | — |

### 2.3 回退模板

| 回退场景 | 模板ID | 注入目标 |
|---------|--------|---------|
| Backend→API回退 | `rbk_backend_to_api` | API Agent |
| Backend→DB回退 | `rbk_backend_to_db` | DB Agent |
| Frontend→API回退 | `rbk_frontend_to_api` | API Agent |
| Validate→Backend回退 | `rbk_validate_to_backend` | Backend Agent |
| Validate→Frontend回退 | `rbk_validate_to_frontend` | Frontend Agent |

---

## 3. 模板语法

### 3.1 占位符语法

模板使用 `{{变量名}}` 标记变量占位符，支持3种类型：

| 类型 | 语法 | 说明 | 示例 |
|------|------|------|------|
| **标量变量** | `{{var_name}}` | 替换为字符串值 | `{{target_file}}` → `app/services/user.py` |
| **块变量** | `{{#block_name}}...{{/block_name}}` | 条件块，变量为真时保留块内容 | `{{#has_errors}}=== 错误信息 ==={{error_details}}{{/has_errors}}` |
| **列表变量** | `{{#list_name}}{{.}}{{/list_name}}` | 迭代列表，`.` 代表当前元素 | `{{#pending_files}}- {{.}}{{/pending_files}}` |

### 3.2 占位符命名规范

| 规则 | 说明 | 正确 | 错误 |
|------|------|------|------|
| snake_case命名 | 与JSON产物风格一致 | `{{target_file}}` | `{{targetFile}}` |
| 语义前缀 | 标注变量来源 | `{{ctx_completed_files}}` | `{{files}}` |
| 无缩写 | 变量名自解释 | `{{decision_summary}}` | `{{ds}}` |

**语义前缀定义**：

| 前缀 | 来源 | 示例 |
|------|------|------|
| `ctx_` | 上下文清单（A1四层结构） | `ctx_completed_files`、`ctx_dependency_signatures` |
| `dec_` | 决策摘要（decision_summary.json） | `dec_response_format`、`dec_audit_decorator` |
| `err_` | 错误数据（D4 ErrorRecordJSON） | `err_error_code`、`err_error_message` |
| `rag_` | RAG检索结果 | `rag_retrieved_patterns`、`rag_fix_pairs` |
| `task_` | 当前任务描述 | `task_target_file`、`task_methods` |
| `cfg_` | 管线配置 | `cfg_token_tier`、`cfg_max_retry` |

### 3.3 注释语法

模板中可嵌入注释，组装时自动剥离：

```
{{! 这是注释，不会出现在最终Prompt中}}
```

用途：标注模板段落的用途、维护说明、变量来源。

### 3.4 段标记语法

模板中的逻辑段用段标记分隔，便于按段裁剪（A4 Token超限时）：

```
{{@segment:SEGMENT_NAME}}
段内容
{{/segment:SEGMENT_NAME}}
```

**预定义段名**：

| 段名 | 说明 | 裁剪优先级 |
|------|------|-----------|
| `HEAD` | Prompt-Head，任务声明 | 不裁剪 |
| `JSON_STATE` | JSON精确状态 | 不裁剪 |
| `DECISION_SUMMARY` | 决策摘要 | 4（最后裁剪） |
| `NEAR_WINDOW` | 近窗口依赖文件 | 2（优先裁剪） |
| `ERROR_CONTEXT` | 错误注入（修复/回退时） | 3 |
| `RAG_CONTEXT` | RAG检索结果 | 2 |
| `TAIL` | Prompt-Tail，约束强化 | 不裁剪 |

---

## 4. 模板结构定义

### 4.1 三明治式Prompt通用结构

所有Prompt模板遵循A1定义的三明治结构，由5个逻辑段组成：

```
┌─────────────────────────────────────────────┐
│ {{@segment:HEAD}}                           │  ← Prompt-Head
│ [系统角色声明 + 任务目标]                     │
├─────────────────────────────────────────────┤
│ {{@segment:JSON_STATE}}                     │  ← 四层注入
│ [JSON精确状态]                               │
├─────────────────────────────────────────────┤
│ {{@segment:DECISION_SUMMARY}}               │
│ [决策摘要]                                   │
├─────────────────────────────────────────────┤
│ {{@segment:NEAR_WINDOW}}                    │
│ [近窗口依赖文件]                             │
├─────────────────────────────────────────────┤
│ {{@segment:TAIL}}                           │  ← Prompt-Tail
│ [输出约束 + 衔接约束 + 禁止项]               │
└─────────────────────────────────────────────┘
```

**修复Prompt额外段**：

```
┌─────────────────────────────────────────────┐
│ {{@segment:HEAD}}                           │
├─────────────────────────────────────────────┤
│ {{@segment:ERROR_CONTEXT}}                  │  ← 修复时注入
│ [错误详情 + 修复要求]                        │
├─────────────────────────────────────────────┤
│ {{@segment:JSON_STATE}}                     │
├─────────────────────────────────────────────┤
│ {{@segment:NEAR_WINDOW}}                    │
├─────────────────────────────────────────────┤
│ {{@segment:TAIL}}                           │
└─────────────────────────────────────────────┘
```

### 4.2 Prompt-Head 段

**职责**：锚定生成方向（首位效应）

**结构**：
```
{{@segment:HEAD}}
你是{{task_agent_role}}。当前任务：生成 {{task_target_file}}
目标：{{task_objective}}
{{#task_methods}}需要实现的方法：{{task_methods}}{{/task_methods}}
{{/segment:HEAD}}
```

**各Agent的Prompt-Head变量**：

| Agent | `task_agent_role` | `task_objective` 示例 |
|-------|-------------------|----------------------|
| PRD Agent | 需求文档生成器 | 从自然语言提取需求，生成PRD文档 |
| Design Agent | 概要设计文档生成器 | 基于PRD提取架构和功能模块，生成概要设计 |
| DB Agent | 数据库设计器 | 基于概要设计进行三阶段数据库设计 |
| API Agent | 接口定义设计器 | 基于概要设计和数据模型设计ABC三层接口 |
| Backend Agent | Flask后端代码生成器 | 生成当前模块的服务/路由/接口实现代码 |
| Frontend Agent | Vue3前端代码生成器 | 生成当前模块的页面/组件/API调用代码 |
| Validate Agent | 代码检查器 | 执行三层检查+契约一致性校验 |
| DevOps Agent | 部署配置生成器 | 基于项目代码生成容器化与CI/CD配置 |

### 4.3 JSON精确状态段

**职责**：描述已生成的结构化事实

**结构**：
```
{{@segment:JSON_STATE}}
=== 项目状态 ===
已完成文件：
{{#ctx_completed_files}}- {{.}}
{{/ctx_completed_files}}
待生成文件：
{{#ctx_pending_files}}- {{.}}
{{/ctx_pending_files}}
当前模块：{{ctx_current_module}}
依赖签名：
{{ctx_dependency_signatures}}
{{/segment:JSON_STATE}}
```

**Token预算**：A4三档制中占10%-15%。

**裁剪规则**（A4超限时）：
1. `ctx_pending_files` 仅保留当前模块文件
2. `ctx_dependency_signatures` 仅保留当前文件直接依赖（D0）
3. 移除JSON格式化空行

### 4.4 决策摘要段

**职责**：全局架构约定，贯穿始终

**结构**：
```
{{@segment:DECISION_SUMMARY}}
=== 架构决策 ===
{{#dec_decisions}}
- **{{dec_key}}**: {{dec_value}}（{{dec_updated_by}}）
{{/dec_decisions}}
{{/segment:DECISION_SUMMARY}}
```

**Token预算**：A4三档制中占5%-10%。

**裁剪规则**：
1. 移除 `dec_updated_by` 来源标注
2. 仅保留与当前任务相关的决策项
3. 紧凑档：仅保留核心5条决策

**决策项相关性判定**：

| 任务类型 | 必含决策项 | 可选决策项 |
|---------|-----------|-----------|
| 后端代码生成 | response_format, exception_handling, pagination, audit | database_conventions, device_identification |
| 前端代码生成 | response_format, pagination, ui_framework, state_management | websocket, chart_library |
| 数据库设计 | database_conventions | tech_stack |
| 接口设计 | response_format, exception_handling, pagination | audit, device_identification |

### 4.5 近窗口依赖文件段

**职责**：最近依赖文件的完整代码

**结构**：
```
{{@segment:NEAR_WINDOW}}
=== 依赖文件 ===
{{#ctx_near_window_files}}
--- {{nw_file_path}} ---
{{nw_file_content}}
{{/ctx_near_window_files}}
{{/segment:NEAR_WINDOW}}
```

**Token预算**：A4三档制中占30%-40%（最大段）。

**裁剪规则**（A4定义的5级裁剪优先级）：
1. 裁剪D2的签名（保留class名，去掉方法签名）
2. 裁剪D1的方法体（仅保留签名）
3. 裁剪D0的注释和空行
4. 裁剪D0的非核心方法
5. 裁剪决策摘要中的解释说明

### 4.6 错误上下文段

**职责**：修复/回退时注入错误详情

**结构**：
```
{{@segment:ERROR_CONTEXT}}
=== 错误信息 ===
错误码：{{err_error_code}}
错误级别：{{err_error_level}}
错误描述：{{err_error_message}}
{{#err_error_details}}
详细位置：{{err_location}}
修复建议：{{err_fix_suggestion}}
{{/err_error_details}}
{{#err_previous_attempts}}
上次尝试（第{{attempt_round}}轮）：
{{attempt_result_summary}}
{{/err_previous_attempts}}
请针对以上错误进行修复，保持其他部分不变。
{{/segment:ERROR_CONTEXT}}
```

**Token预算**：
- 紧凑档：≤200 tokens（D4第15章）
- 标准档：≤500 tokens
- 宽裕档：≤1000 tokens

**修复轮次注入规则**：

| 修复轮次 | 注入内容 | 说明 |
|---------|---------|------|
| 第1轮 | 当前错误详情 | 标准修复 |
| 第2轮 | 当前错误 + 第1轮修复摘要 | 避免重复相同修复策略 |
| 第3轮 | 全部历史 + 明确提示"这是最后一次自动修复" | 熔断预警 |

### 4.7 RAG上下文段

**职责**：注入RAG检索结果（代码模式库/Bug-Fix对）

**结构**：
```
{{@segment:RAG_CONTEXT}}
{{#rag_has_results}}
=== 参考范例 ===
{{#rag_retrieved_patterns}}
--- 范例：{{rag_pattern_name}}（相似度：{{rag_similarity}}）---
{{rag_pattern_content}}
{{/rag_retrieved_patterns}}
{{#rag_fix_pairs}}
--- Bug-Fix对：{{rag_fix_description}} ---
修复前：{{rag_before_code}}
修复后：{{rag_after_code}}
{{/rag_fix_pairs}}
{{/rag_has_results}}
{{/segment:RAG_CONTEXT}}
```

**RAG降级规则**（A5 D0降级）：

| 降级级别 | RAG注入行为 | 对模板的影响 |
|---------|-----------|-------------|
| 正常 | 注入Top-3检索结果 | `RAG_CONTEXT`段完整注入 |
| D0-1 | 仅注入Top-1 | `rag_retrieved_patterns`列表缩减 |
| D0-2 | 不注入RAG结果 | `RAG_CONTEXT`段整体跳过（块变量控制） |
| D0-3 | 不调用RAG | 不触发检索，`rag_has_results`为false |

### 4.8 Prompt-Tail 段

**职责**：收束输出格式（近因效应）

**结构**：
```
{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：```{{task_output_language}}:{{task_target_file}}
- {{task_constraint_1}}
- {{task_constraint_2}}
{{#task_extra_constraints}}
- {{.}}
{{/task_extra_constraints}}
=== 禁止项 ===
- 不要重复导入已在依赖文件中定义的类
- 不要生成非当前目标文件的代码
- 不要修改依赖文件中的任何内容
{{#task_prohibitions}}
- {{.}}
{{/task_prohibitions}}
{{/segment:TAIL}}
```

**各Agent的Prompt-Tail变量**：

| Agent | `task_output_language` | 典型约束 |
|-------|----------------------|---------|
| PRD Agent | `markdown` | 按PRD模板章节输出 |
| Design Agent | `markdown` | 包含架构图和模块划分 |
| DB Agent | `sql` | 每张表独立CREATE语句 |
| API Agent | `json` | 严格遵循api_def.json Schema |
| Backend Agent | `python` | 继承ABC接口、@audit_log装饰器、ServiceException |
| Frontend Agent | `vue` | 组件命名PascalCase、API调用对齐api_def.json |
| Validate Agent | `json` | 检查报告Schema（D1第9章） |
| DevOps Agent | `yaml` | Dockerfile/docker-compose/CI配置 |

---

## 5. 完整模板定义

### 5.1 生成模板（gen_）

#### 5.1.1 gen_backend_file（Backend代码生成）

```
{{@segment:HEAD}}
你是Flask后端代码生成器。当前任务：生成 {{task_target_file}}
目标：{{task_objective}}
{{#task_methods}}需要实现的方法：{{task_methods}}{{/task_methods}}
{{/segment:HEAD}}

{{@segment:JSON_STATE}}
=== 项目状态 ===
已完成文件：
{{#ctx_completed_files}}- {{.}}
{{/ctx_completed_files}}
当前模块：{{ctx_current_module}}
待生成文件：
{{#ctx_pending_files}}- {{.}}
{{/ctx_pending_files}}
依赖签名：
{{ctx_dependency_signatures}}
{{/segment:JSON_STATE}}

{{@segment:DECISION_SUMMARY}}
=== 架构决策 ===
{{#dec_decisions}}
- **{{dec_key}}**: {{dec_value}}
{{/dec_decisions}}
{{/segment:DECISION_SUMMARY}}

{{@segment:NEAR_WINDOW}}
=== 依赖文件 ===
{{#ctx_near_window_files}}
--- {{nw_file_path}} ---
{{nw_file_content}}
{{/ctx_near_window_files}}
{{/segment:NEAR_WINDOW}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：```python:{{task_target_file}}
- 严格继承{{task_interface_name}}的所有抽象方法
- 每个方法添加@audit_log装饰器
- 使用ServiceException处理业务异常
{{#task_extra_constraints}}
- {{.}}
{{/task_extra_constraints}}
=== 禁止项 ===
- 不要重复导入已在依赖文件中定义的类
- 不要生成非{{task_target_file}}的代码
- 不要修改依赖文件中的任何内容
{{#task_prohibitions}}
- {{.}}
{{/task_prohibitions}}
{{/segment:TAIL}}
```

#### 5.1.2 gen_frontend_file（Frontend代码生成）

```
{{@segment:HEAD}}
你是Vue3前端代码生成器。当前任务：生成 {{task_target_file}}
目标：{{task_objective}}
{{#task_components}}需要使用的组件：{{task_components}}{{/task_components}}
{{/segment:HEAD}}

{{@segment:JSON_STATE}}
=== 项目状态 ===
已完成文件：
{{#ctx_completed_files}}- {{.}}
{{/ctx_completed_files}}
组件注册表：
{{ctx_component_registry}}
Composables注册表：
{{ctx_composables_registry}}
已使用API端点：
{{ctx_api_endpoints_used}}
{{/segment:JSON_STATE}}

{{@segment:DECISION_SUMMARY}}
=== 架构决策 ===
{{#dec_decisions}}
- **{{dec_key}}**: {{dec_value}}
{{/dec_decisions}}
{{/segment:DECISION_SUMMARY}}

{{@segment:NEAR_WINDOW}}
=== 依赖文件 ===
{{#ctx_near_window_files}}
--- {{nw_file_path}} ---
{{nw_file_content}}
{{/ctx_near_window_files}}

=== API契约 ===
{{ctx_api_def_relevant}}
{{/segment:NEAR_WINDOW}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：```vue:{{task_target_file}}
- 组件命名：PascalCase
- API调用必须与api_def.json中的路径/方法/参数完全一致
- 使用Element Plus组件库
{{#task_extra_constraints}}
- {{.}}
{{/task_extra_constraints}}
=== 禁止项 ===
- 不要硬编码API路径，必须从api_def.json中引用
- 不要生成非{{task_target_file}}的代码
- 不要在组件中直接操作DOM
{{#task_prohibitions}}
- {{.}}
{{/task_prohibitions}}
{{/segment:TAIL}}
```

#### 5.1.3 gen_prd（PRD文档生成）

```
{{@segment:HEAD}}
你是需求文档生成器。当前任务：基于用户输入生成PRD文档
用户输入：{{task_user_input}}
{{/segment:HEAD}}

{{@segment:RAG_CONTEXT}}
{{#rag_has_results}}
=== 领域知识扩展 ===
{{#rag_retrieved_patterns}}
- {{rag_pattern_name}}：{{rag_pattern_content}}
{{/rag_retrieved_patterns}}
{{/rag_has_results}}
{{/segment:RAG_CONTEXT}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：markdown（将转为.docx）
- 按以下章节结构输出：
  1. 项目概述
  2. 功能需求（按模块分节）
  3. 非功能需求（性能/安全/可用性）
  4. 用户角色与权限
  5. 业务流程描述
  6. 数据实体初步识别
=== 禁止项 ===
- 不要做架构决策
- 不要做技术选型
- 不要生成代码
{{/segment:TAIL}}
```

#### 5.1.4 gen_design（概要设计生成）

```
{{@segment:HEAD}}
你是概要设计文档生成器。当前任务：基于PRD文档生成概要设计
{{/segment:HEAD}}

{{@segment:JSON_STATE}}
=== PRD摘要 ===
{{ctx_prd_summary}}
{{/segment:JSON_STATE}}

{{@segment:RAG_CONTEXT}}
{{#rag_has_results}}
=== 架构参考 ===
{{#rag_retrieved_patterns}}
- {{rag_pattern_name}}：{{rag_pattern_content}}
{{/rag_retrieved_patterns}}
{{/rag_has_results}}
{{/segment:RAG_CONTEXT}}

{{@segment:DECISION_SUMMARY}}
=== 已确定约束 ===
{{#dec_decisions}}
- **{{dec_key}}**: {{dec_value}}
{{/dec_decisions}}
{{/segment:DECISION_SUMMARY}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：markdown（将转为.docx）+ 结构化JSON
- 必须包含：系统架构、模块划分、技术选型、接口概述
- 结构化JSON必须严格遵循D1 design.json Schema
- 架构模式：ABC三层架构（Interface → AbstractClass → DeviceImpl）
- 设备识别：Flask Config + 工厂模式
- 审计：装饰器替代AOP
=== 禁止项 ===
- 不要修改PRD内容
- 不要做数据库表设计
- 不要生成代码
{{/segment:TAIL}}
```

#### 5.1.5 gen_db（数据库设计生成）

```
{{@segment:HEAD}}
你是数据库设计器。当前任务：基于概要设计进行三阶段数据库设计
{{/segment:HEAD}}

{{@segment:JSON_STATE}}
=== 概要设计摘要 ===
{{ctx_design_summary}}
{{/segment:JSON_STATE}}

{{@segment:RAG_CONTEXT}}
{{#rag_has_results}}
=== MySQL 8.0特性参考 ===
{{#rag_retrieved_patterns}}
- {{rag_pattern_name}}：{{rag_pattern_content}}
{{/rag_retrieved_patterns}}
{{/rag_has_results}}
{{/segment:RAG_CONTEXT}}

{{@segment:DECISION_SUMMARY}}
=== 数据库约定 ===
{{#dec_decisions}}
- **{{dec_key}}**: {{dec_value}}
{{/dec_decisions}}
{{/segment:DECISION_SUMMARY}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：markdown文档（将转为.docx）+ SQL文件 + db_model.json
- 三阶段设计法：概念设计(ER图) → 逻辑设计(3NF) → 物理设计(表结构)
- SQL文件包含完整CREATE TABLE语句（含索引、外键、约束）
- db_model.json严格遵循D1 db_model.json Schema
- 字符集：utf8mb4，引擎：InnoDB
=== 禁止项 ===
- 不要修改概要设计
- 不要生成API接口
- 不要生成代码
{{/segment:TAIL}}
```

#### 5.1.6 gen_api（接口定义生成）

```
{{@segment:HEAD}}
你是接口定义设计器。当前任务：基于概要设计和数据模型设计ABC三层接口
{{/segment:HEAD}}

{{@segment:JSON_STATE}}
=== 数据模型 ===
{{ctx_db_model_summary}}
=== 模块划分 ===
{{ctx_module_list}}
{{/segment:JSON_STATE}}

{{@segment:RAG_CONTEXT}}
{{#rag_has_results}}
=== RESTful参考 ===
{{#rag_retrieved_patterns}}
- {{rag_pattern_name}}：{{rag_pattern_content}}
{{/rag_retrieved_patterns}}
{{/rag_has_results}}
{{/segment:RAG_CONTEXT}}

{{@segment:DECISION_SUMMARY}}
=== 接口约定 ===
{{#dec_decisions}}
- **{{dec_key}}**: {{dec_value}}
{{/dec_decisions}}
{{/segment:DECISION_SUMMARY}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：markdown文档（将转为.docx）+ api_def.json
- 三层接口架构：ABC接口层 → 抽象实现层 → 设备扩展层
- api_def.json是前后端唯一契约，严格遵循D1 api_def.json Schema
- 统一分页/过滤/排序规范（BaseQueryParser）
- WebSocket用于监控/告警实时推送
=== 禁止项 ===
- 不要修改数据库设计
- 不要生成业务代码实现
- 不要在接口中暴露内部实现细节
{{/segment:TAIL}}
```

#### 5.1.7 gen_devops（DevOps配置生成）

```
{{@segment:HEAD}}
你是部署配置生成器。当前任务：生成容器化与CI/CD配置
目标平台：{{cfg_deploy_platform}}
部署目标：{{cfg_deploy_target}}
{{/segment:HEAD}}

{{@segment:JSON_STATE}}
=== 项目信息 ===
后端框架：Flask
前端框架：Vue 3
数据库：MySQL 8.0
入口文件：{{ctx_backend_entry}} / {{ctx_frontend_entry}}
建库SQL：{{ctx_db_sql_path}}
{{/segment:JSON_STATE}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出文件清单：
  1. Dockerfile（后端）
  2. Dockerfile（前端/nginx）
  3. docker-compose.yml
  4. nginx.conf
  5. CI配置文件（{{cfg_deploy_platform}}格式）
  6. .env.example
  7. 辅助脚本（启动/停止/健康检查）
- 每个文件用 ```filename 标记
- 后端基于Python 3.11，前端基于Node 18构建+nginx运行
=== 禁止项 ===
- 不要修改应用代码
- 不要修改数据库设计
- 不要硬编码敏感信息（使用环境变量）
{{/segment:TAIL}}
```

### 5.2 修复模板（fix_）

#### 5.2.1 fix_backend（Backend代码修复）

```
{{@segment:HEAD}}
你是Flask后端代码修复器。当前任务：修复 {{task_target_file}} 中的错误
{{/segment:HEAD}}

{{@segment:ERROR_CONTEXT}}
=== 错误信息 ===
错误码：{{err_error_code}}
错误描述：{{err_error_message}}
{{#err_error_details}}
错误位置：{{err_location}}
修复建议：{{err_fix_suggestion}}
{{/err_error_details}}
{{#err_previous_attempts}}
第{{attempt_round}}轮修复结果：{{attempt_result_summary}}
{{/err_previous_attempts}}
请针对以上错误修复，保持未报错部分不变。
{{/segment:ERROR_CONTEXT}}

{{@segment:NEAR_WINDOW}}
=== 当前文件 ===
--- {{task_target_file}} ---
{{ctx_current_file_content}}

=== 相关依赖 ===
{{#ctx_near_window_files}}
--- {{nw_file_path}} ---
{{nw_file_content}}
{{/ctx_near_window_files}}
{{/segment:NEAR_WINDOW}}

{{@segment:RAG_CONTEXT}}
{{#rag_has_results}}
=== Bug-Fix参考 ===
{{#rag_fix_pairs}}
问题描述：{{rag_fix_description}}
修复前：{{rag_before_code}}
修复后：{{rag_after_code}}
{{/rag_fix_pairs}}
{{/rag_has_results}}
{{/segment:RAG_CONTEXT}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：```python:{{task_target_file}}
- 仅修复错误相关部分，不要重写整个文件
- 保持与依赖文件的接口一致
- 保持@audit_log装饰器和ServiceException异常处理
{{#err_is_last_attempt}}
⚠️ 这是最后一次自动修复尝试。如果仍然失败将升级为L3回退。
{{/err_is_last_attempt}}
{{/segment:TAIL}}
```

#### 5.2.2 fix_frontend（Frontend代码修复）

结构同 `fix_backend`，差异点：
- `task_output_language` → `vue`
- 约束中强调"API调用必须与api_def.json一致"
- 禁止项中强调"不要硬编码API路径"

#### 5.2.3 fix_validate（Validate检查修复）

```
{{@segment:HEAD}}
你是代码检查修复器。当前任务：修复 {{task_target_file}} 的检查问题
检查层级：{{chk_layer}}
{{/segment:HEAD}}

{{@segment:ERROR_CONTEXT}}
=== 检查问题 ===
{{#chk_issues}}
- [{{chk_issue_severity}}] {{chk_issue_message}}
  位置：{{chk_issue_location}}
{{/chk_issues}}
{{/segment:ERROR_CONTEXT}}

{{@segment:NEAR_WINDOW}}
--- {{task_target_file}} ---
{{ctx_current_file_content}}
{{/segment:NEAR_WINDOW}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：```{{task_output_language}}:{{task_target_file}}
- 仅修复检查问题，不要重写整个文件
- 修复后不得引入新的检查问题
{{/segment:TAIL}}
```

### 5.3 检查模板（chk_）

#### 5.3.1 chk_validate（Validate三层检查）

```
{{@segment:HEAD}}
你是代码审查员。当前任务：检查 {{task_target_file}}
检查范围：{{chk_scope}}
{{/segment:HEAD}}

{{@segment:NEAR_WINDOW}}
--- {{task_target_file}} ---
{{ctx_current_file_content}}

{{#chk_has_contract}}
=== API契约 ===
{{ctx_api_def_relevant}}
{{/chk_has_contract}}
{{/segment:NEAR_WINDOW}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：JSON，遵循validation_report.json Schema
- 检查维度：
  1. 静态问题（语法/命名/导入）
  2. 逻辑问题（业务规则/框架规范）
  3. 契约一致性（API路径/参数/响应格式）
  4. 安全问题（注入/XSS/硬编码敏感信息）
- 每个问题标注严重级别（critical/warning/info）和位置
- 若无问题，输出：{"status": "passed", "issues": []}
{{/segment:TAIL}}
```

### 5.4 回退模板（rbk_）

#### 5.4.1 rbk_backend_to_api（Backend→API回退）

```
{{@segment:HEAD}}
你是接口定义设计器。上游回退通知：Backend Agent在生成代码时发现接口定义问题
{{/segment:HEAD}}

{{@segment:ERROR_CONTEXT}}
=== 回退原因 ===
回退来源：Backend Agent
错误描述：{{err_error_message}}
具体问题：{{rbk_detail}}

Backend Agent的补充上下文：
{{rbk_additional_context}}

受影响的下游：{{rbk_affected_downstream}}
{{/segment:ERROR_CONTEXT}}

{{@segment:JSON_STATE}}
=== 原始输入 ===
{{ctx_original_inputs}}
{{/segment:JSON_STATE}}

{{@segment:TAIL}}
=== 输出约束 ===
- 重新审视接口定义，解决上述问题
- 保持api_def.json的向后兼容性（不删除已有接口，可新增参数）
- 修改后更新api_def.json版本号
- 产出修正后的api_def.json
{{/segment:TAIL}}
```

### 5.5 系统模板（sys_）

#### 5.5.1 通用系统模板结构

系统模板作为Prompt的最外层包裹，定义Agent的角色和行为约束。系统模板不参与三明治结构的段标记，而是包裹整个Prompt：

```
你是{{sys_agent_role}}，负责{{sys_agent_responsibility}}。

行为准则：
1. 只产出你职责范围内的交付物，不越界
2. 遵循决策摘要中已确定的全局约定
3. 遇到不确定的问题，标记为[NEEDS_CONFIRM]而非自行决定
4. 输出格式严格遵守约束段的要求

禁止行为：
{{#sys_prohibitions}}
- {{.}}
{{/sys_prohibitions}}

---

{{sys_inner_prompt}}
```

`sys_inner_prompt` 在组装时被替换为三明治式Prompt的完整内容。

#### 5.5.2 各Agent系统模板变量

| Agent | `sys_agent_role` | `sys_agent_responsibility` | 典型禁止项 |
|-------|-----------------|---------------------------|-----------|
| PRD | 需求文档生成器 | 从自然语言提取需求并生成PRD文档 | 不做架构决策、不做技术选型、不生成代码 |
| Design | 概要设计文档生成器 | 基于PRD生成概要设计和结构化JSON | 不修改PRD、不做数据库表设计、不生成代码 |
| DB | 数据库设计器 | 三阶段数据库设计并输出SQL和数据模型 | 不修改概要设计、不生成API接口、不生成代码 |
| API | 接口定义设计器 | 设计ABC三层接口并输出api_def.json | 不修改数据库设计、不生成业务代码实现 |
| Backend | Flask后端代码生成器 | 按模块逐文件生成Flask代码 | 不修改接口定义、不生成前端代码、不做数据库设计 |
| Frontend | Vue3前端代码生成器 | 按模块生成Vue3前端代码 | 不修改后端代码、不修改API路由、不做数据库设计 |
| Validate | 代码质量检查器 | 三层检查+契约一致性校验 | 不做业务逻辑修改、不修改接口定义、不修改数据库设计 |
| DevOps | 部署配置生成器 | 生成容器化与CI/CD配置 | 不修改应用代码、不修改数据库设计 |

---

## 6. 模板组装流程

### 6.1 组装器

Prompt组装器是一个函数，接收模板ID和变量字典，输出最终Prompt文本：

```python
def assemble_prompt(template_id: str, variables: dict, token_budget: dict) -> dict:
    """
    Args:
        template_id: 模板ID，如 "gen_backend_file"
        variables: 变量字典，如 {"task_target_file": "app/services/user.py", ...}
        token_budget: Token预算，如 {"tier": "standard", "max_tokens": 32768}

    Returns:
        {
            "prompt_text": "最终Prompt文本",
            "template_id": "gen_backend_file",
            "template_version": "1.0",
            "variables_used": {...},  # 实际使用的变量值
            "segments_included": ["HEAD", "JSON_STATE", ...],  # 包含的段
            "segments_skipped": ["RAG_CONTEXT"],  # 跳过的段（条件未满足）
            "token_estimate": 28500,  # 估算Token数
            "trim_log": [...]  # 裁剪记录
        }
    """
```

### 6.2 组装步骤

```
Step1: 根据template_id加载模板文本
Step2: 解析模板，提取所有变量占位符
Step3: 从variables字典中匹配变量值
Step4: 评估条件块（{{#block}}...{{/block}}）
       - 变量存在且非空 → 保留块内容
       - 变量不存在或为空 → 移除块内容
Step5: 替换标量变量和列表变量
Step6: 剥离注释（{{! ...}}）
Step7: 估算各段Token占用
Step8: 若总Token超过预算90%，按裁剪优先级逐段裁剪
Step9: 记录裁剪日志（哪段裁剪了什么）
Step10: 组装最终文本，保留段标记（用于调试，LLM不感知）
Step11: 返回组装结果
```

### 6.3 变量缺失处理

| 缺失类型 | 处理方式 | 示例 |
|---------|---------|------|
| 必需变量缺失 | 抛出 `PromptAssemblyError`，中止组装 | `task_target_file` 缺失 |
| 可选变量缺失 | 对应条件块整体跳过 | `rag_has_results` 为空 → 跳过 `RAG_CONTEXT` 段 |
| 变量类型不匹配 | 尝试类型转换，失败则抛出错误 | 列表变量传入字符串 → 尝试解析为单元素列表 |

**变量必需性定义**：

| 变量 | 必需性 | 说明 |
|------|--------|------|
| `task_target_file` | 必需 | 当前生成目标 |
| `task_objective` | 必需 | 任务目标描述 |
| `ctx_completed_files` | 必需 | 已完成文件列表（可为空列表） |
| `dec_decisions` | 必需 | 决策摘要 |
| `err_error_code` | 修复模板必需 | 错误码 |
| `rag_has_results` | 可选 | RAG结果标志 |
| `task_extra_constraints` | 可选 | 额外约束 |
| `task_prohibitions` | 可选 | 额外禁止项 |

---

## 7. 模板存储与版本管理

### 7.1 存储目录

```
templates/
├── system/                      # 系统模板
│   ├── sys_prd.md
│   ├── sys_design.md
│   ├── sys_backend.md
│   └── ...
├── generation/                  # 生成模板
│   ├── gen_backend_file.md
│   ├── gen_frontend_file.md
│   ├── gen_prd.md
│   └── ...
├── fix/                         # 修复模板
│   ├── fix_backend.md
│   ├── fix_frontend.md
│   └── fix_validate.md
├── check/                       # 检查模板
│   └── chk_validate.md
└── rollback/                    # 回退模板
    ├── rbk_backend_to_api.md
    ├── rbk_backend_to_db.md
    └── ...
```

### 7.2 模板元数据

每个模板文件顶部包含YAML前置元数据：

```yaml
---
template_id: gen_backend_file
version: "1.0"
description: Backend单文件代码生成模板
agent: backend_agent
category: generation
required_variables:
  - task_target_file
  - task_objective
  - ctx_completed_files
  - dec_decisions
optional_variables:
  - task_methods
  - task_interface_name
  - task_extra_constraints
  - task_prohibitions
token_budget_range:
  compact: [2000, 6000]
  standard: [8000, 28000]
  generous: [20000, 100000]
segments:
  - HEAD
  - JSON_STATE
  - DECISION_SUMMARY
  - NEAR_WINDOW
  - TAIL
---
```

### 7.3 版本管理

模板遵循D2的版本管理规则：

| 变更类型 | 版本变化 | 说明 |
|---------|---------|------|
| 新增可选变量 | 不改版本号 | 向后兼容 |
| 新增段标记 | 不改版本号 | 段标记是附加信息 |
| 修改段内容结构 | minor +1 | 需验证组装兼容性 |
| 删除变量或段 | major +1 | 破坏性变更 |

### 7.4 组装记录

每次Prompt组装后保存组装记录，用于审计和回放：

```json
{
  "assembly_id": "uuid",
  "timestamp": "2026-04-30T10:00:00+08:00",
  "pipeline_id": "uuid",
  "agent": "backend_agent",
  "template_id": "gen_backend_file",
  "template_version": "1.0",
  "variables": {
    "task_target_file": "app/services/user.py",
    "task_objective": "实现用户管理服务，继承IUserManager接口",
    "ctx_completed_files": ["app/__init__.py", "app/models/user.py"]
  },
  "segments_included": ["HEAD", "JSON_STATE", "DECISION_SUMMARY", "NEAR_WINDOW", "TAIL"],
  "segments_skipped": ["RAG_CONTEXT"],
  "token_estimate": 18500,
  "token_budget_tier": "standard",
  "trim_log": [
    {
      "segment": "NEAR_WINDOW",
      "action": "removed_D2_signatures",
      "reason": "token_over_budget",
      "tokens_saved": 1200
    }
  ],
  "prompt_text_hash": "sha256:..."
}
```

**存储路径**：`output/{pipeline_id}/logs/prompt_assemblies/{agent}_{assembly_id}.json`

**保留策略**：
- 正常执行：保留所有组装记录
- 归档压缩：仅保留首次和末次组装记录，中间记录压缩为摘要

---

## 8. Token预算与裁剪

### 8.1 各段Token预算分配

参照A4三档制，各段的Token预算分配：

| 段 | 紧凑(8K) | 标准(32K) | 宽裕(128K) | 裁剪优先级 |
|----|---------|----------|-----------|-----------|
| HEAD | 5%（~400） | 5%（~1600） | 3%（~3800） | 不裁剪 |
| JSON_STATE | 10%（~800） | 15%（~4900） | 15%（~19000） | 不裁剪 |
| DECISION_SUMMARY | 5%（~400） | 10%（~3200） | 10%（~12800） | 4 |
| NEAR_WINDOW | 30%（~2400） | 40%（~13100） | 40%（~51200） | 2 |
| ERROR_CONTEXT | — | ≤500 | ≤1000 | 3 |
| RAG_CONTEXT | — | ≤800 | ≤2000 | 2 |
| TAIL | 55%（~4400） | 35%（~11400） | 35%（~44800） | 不裁剪 |

**注**：紧凑档HEAD+TAIL占比高（60%），牺牲中间上下文保留任务指令的完整性。

### 8.2 裁剪触发条件

```
if token_estimate > token_budget * 0.9:
    trigger_trimming()
```

预留10%安全余量（A4第7.2节）。

### 8.3 裁剪执行

```
while token_estimate > token_budget * 0.9:
    1. 按裁剪优先级选择下一个可裁剪段
    2. 对该段执行一级裁剪（详见A4第3.4节5级裁剪优先级）
    3. 重新估算Token
    4. 记录裁剪日志
    5. 若所有可裁剪段已裁剪至极限 → 报错（Prompt过长）
```

### 8.4 段级裁剪与A4分级衰减的映射

| P1段标记 | A4裁剪级别 | 裁剪动作 |
|---------|-----------|---------|
| `NEAR_WINDOW` | D2签名 | 移除D2文件的方法签名 |
| `NEAR_WINDOW` | D1方法体 | 移除D1文件的方法体，仅保留签名 |
| `NEAR_WINDOW` | D0注释空行 | 移除D0文件的注释和空行 |
| `NEAR_WINDOW` | D0非核心方法 | 移除D0文件中与当前任务无关的方法 |
| `DECISION_SUMMARY` | 解释说明 | 移除决策项的说明文字，仅保留结论 |

---

## 9. 模板校验

### 9.1 模板文件校验

模板文件提交前必须通过以下校验：

| 校验项 | 规则 | 错误级别 |
|--------|------|---------|
| 占位符闭合 | 所有 `{{#block}}` 必须有对应 `{{/block}}` | error |
| 段标记闭合 | 所有 `{{@segment:X}}` 必须有对应 `{{/segment:X}}` | error |
| 必需变量声明 | 元数据中 `required_variables` 列出的变量必须在模板中出现 | error |
| 变量命名规范 | 占位符使用 snake_case + 语义前缀 | warning |
| 段标记嵌套 | 段标记不可嵌套（段内不能再有段标记） | error |
| 注释闭合 | 所有 `{{! }}` 注释必须闭合 | warning |
| Token预算声明 | 元数据中必须声明 `token_budget_range` | error |

### 9.2 组装结果校验

组装后的Prompt必须通过以下校验：

| 校验项 | 规则 | 错误级别 |
|--------|------|---------|
| 无残留占位符 | 最终文本中不含未替换的 `{{...}}` | error |
| 无残留段标记 | 最终文本中不含 `{{@segment:...}}` 标记 | error |
| Token不超限 | token_estimate ≤ token_budget × 0.9 | error |
| Head存在 | 最终文本包含任务声明 | error |
| Tail存在 | 最终文本包含输出约束 | error |
| 必需段完整 | HEAD、JSON_STATE、TAIL 三段不可缺失 | error |

### 9.3 校验工具

```python
def validate_template(template_path: str) -> list:
    """
    校验模板文件，返回校验结果列表
    Returns:
        [{"check": "占位符闭合", "status": "pass|error|warning", "detail": "..."}]
    """

def validate_assembly(assembly_result: dict) -> list:
    """
    校验组装结果，返回校验结果列表
    Returns:
        [{"check": "无残留占位符", "status": "pass|error|warning", "detail": "..."}]
    """
```

---

## 10. 模板扩展与定制

### 10.1 设备扩展模板

对于网络安全设备的不同类型（防火墙/IDS/VPN），可基于基础模板扩展：

```
templates/generation/gen_backend_file.md       ← 基础模板
templates/generation/gen_backend_file_fw.md    ← 防火墙扩展
templates/generation/gen_backend_file_ids.md   ← IDS扩展
templates/generation/gen_backend_file_vpn.md   ← VPN扩展
```

**扩展方式**：设备扩展模板继承基础模板，在 `task_extra_constraints` 中追加设备特有约束：

```yaml
---
template_id: gen_backend_file_fw
extends: gen_backend_file
version: "1.0"
description: Backend防火墙模块代码生成模板
extra_constraints:
  - 防火墙规则配置接口必须实现IFirewallManager
  - rule_type枚举值：allow/deny/drop/log
  - 规则优先级字段 priority 必须为正整数
---
```

### 10.2 自定义模板注入

用户可在管线配置中指定自定义模板覆盖默认模板：

```json
{
  "template_overrides": {
    "gen_backend_file": "custom/my_backend_template.md",
    "sys_backend": "custom/my_backend_system.md"
  }
}
```

**安全约束**：自定义模板必须通过9.1节的校验才可使用。

---

## 11. 与其他规范的联动

### 11.1 与A4上下文管理的联动

| A4概念 | P1映射 |
|--------|--------|
| Token三档制 | 模板元数据声明各档Token预算区间 |
| 四层状态注入 | 对应模板的4个段（JSON_STATE/DECISION_SUMMARY/NEAR_WINDOW/TAIL） |
| 9步组装流程 | P1组装器的10步组装流程覆盖A4的9步 |
| 分级衰减裁剪 | P1段级裁剪与A4距离分级一致 |
| 快照版本链 | 组装记录保存为JSON，纳入版本链管理 |

### 11.2 与A5容错的联动

| A5概念 | P1映射 |
|--------|--------|
| L2修复循环 | 修复模板（fix_）定义修复Prompt |
| MAX_FIX_ROUNDS=3 | 修复模板中第3轮注入熔断预警 |
| RAG降级(D0) | RAG_CONTEXT段按降级级别缩减 |
| 断路器触发 | 组装记录中的trim_log支持断路器指标计算 |
| L3回退 | 回退模板（rbk_）定义回退Prompt |

### 11.3 与D4错误数据的联动

| D4概念 | P1映射 |
|--------|--------|
| ErrorRecordJSON | `err_` 前缀变量映射到ErrorRecordJSON字段 |
| 错误指标 | 组装记录中的裁剪日志可用于错误指标计算 |
| Bug-Fix对提取 | 修复模板的成功修复记录可作为Bug-Fix对入库 |
| Prompt Token预算 | D4第15章定义的错误注入Token预算直接应用于ERROR_CONTEXT段 |

---

## 12. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，定义模板分类、语法、结构、组装流程、校验机制 |
