# P4 Prompt分段衔接规范

## 1. 总则

### 1.1 目标
定义管线中同一Agent跨多次LLM调用时的Prompt衔接策略，确保生成第N个文件时与第1到N-1个文件的状态、风格、接口、命名保持一致，消除LLM无跨调用记忆导致的生成断裂问题，为P1模板的HEAD/TAIL段提供衔接变量注入规则。

### 1.2 定位

| 维度 | 说明 |
|------|------|
| **与A1的关系** | A1定义了四层结构化状态注入和三明治式Prompt结构，其中"衔接约束"位于Prompt-Tail；P4将"衔接约束"细化为具体的衔接变量和注入规则 |
| **与A4的关系** | A4定义了上下文快照与版本链（每步保存快照，支持回滚）；P4基于A4的快照机制提取衔接信息，定义快照中的哪些字段用于衔接注入 |
| **与A5的关系** | A5定义了文件级容错、LLM输出截断续写、修复循环；P4定义截断续写的衔接Prompt策略和修复循环中的衔接信息递进 |
| **与P1的关系** | P1定义了模板语法和段标记；P4定义HEAD段中的衔接声明和TAIL段中的衔接约束变量，这些变量由P3解析后注入P1模板 |
| **与P2的关系** | P2定义TAIL段中输出约束变量的填充规则；P4定义TAIL段中衔接约束变量的填充规则，二者共同填充TAIL段 |
| **与P3的关系** | P3定义上下文注入变量的来源解析；P4定义衔接变量的来源解析，衔接变量是P3 ctx_变量的子集，P4为P3提供衔接维度的解析规则 |
| **与C2的关系** | C2定义代码块的输出格式规范；P4定义跨代码块的衔接格式约定（如import声明衔接、类继承衔接） |

### 1.3 核心问题

LLM无跨调用记忆，同一Agent的两次LLM调用之间不存在上下文继承。当Backend Agent按文件逐一生成时：

```
调用1: 生成 app/__init__.py → 成功
调用2: 生成 app/models/user.py → LLM不知道调用1生成了什么
调用3: 生成 app/services/user.py → LLM不知道调用1-2的生成结果
```

若不注入衔接信息，LLM可能：
- 重复定义已在其他文件中定义的类/函数
- 与已生成文件的接口签名不一致
- 命名风格、导入路径、异常处理方式不统一
- 漏掉模块间的调用关系

### 1.4 设计原则

| 原则 | 说明 |
|------|------|
| **衔接即约束** | 衔接信息作为约束注入Prompt，而非作为背景知识——LLM必须遵守，而非参考 |
| **增量精确** | 每次调用只注入与当前任务直接相关的衔接信息，避免全量回放浪费Token |
| **签名优先** | 衔接信息以签名（接口/类/方法签名）为主，全文为辅，最小Token开销 |
| **双向锚定** | HEAD段锚定"与前序文件的衔接关系"，TAIL段锚定"对后续文件的衔接约束" |
| **幂等衔接** | 相同快照+相同任务→相同衔接变量，支持重试和回放 |

---

## 2. 衔接场景分类

### 2.1 场景矩阵

| 场景 | 触发条件 | 衔接需求 | 涉及Agent |
|------|---------|---------|----------|
| **多文件顺序生成** | 代码Agent逐文件生成 | 已完成文件的签名和接口 | Backend/Frontend |
| **超长文件分段生成** | 单文件代码超Token预算 | 前段生成的接口和变量 | Backend/Frontend |
| **截断续写** | LLM输出被Token限制截断 | 已输出部分的末尾上下文 | Backend/Frontend |
| **修复循环** | L2修复（P2校验失败） | 原文件+错误上下文 | Backend/Frontend/Validate |
| **并行后校验** | Validate检查双端代码 | 前后端各自契约对齐状态 | Validate |
| **回退后重新生成** | L3回退到上游Agent | 回退原因+原始输入修正 | API/DB/Design |

### 2.2 衔接信息分类

| 类别 | 说明 | 注入位置 | 变量前缀 |
|------|------|---------|---------|
| **前序签名衔接** | 已完成文件的类/方法/组件签名 | JSON_STATE段 | `ctx_` |
| **接口契约衔接** | ABC接口/API端点的精确签名 | NEAR_WINDOW段 | `ctx_` + `nw_` |
| **风格一致衔接** | 命名/导入/异常处理的统一约定 | DECISION_SUMMARY段 | `dec_` |
| **位置衔接** | 当前文件在模块/管线中的位置 | HEAD段 | `task_` |
| **续写衔接** | 截断续写时已输出部分的上下文 | HEAD段 | `cont_`（continuation） |
| **修复衔接** | 修复循环中历史修复尝试 | ERROR_CONTEXT段 | `err_` |

---

## 3. 多文件顺序生成衔接

### 3.1 生成顺序与衔接关系

代码Agent按预定义顺序逐文件生成，每个文件的衔接依赖关系由生成顺序和模块结构决定：

#### 3.1.1 Backend Agent生成顺序

```
S1: app/__init__.py（应用工厂+扩展初始化）
S2: app/extensions.py（db/jwt/socketio等扩展实例）
S3: app/utils/exceptions.py（ServiceException等）
S4: app/utils/decorators.py（@audit_log等）
S5: app/utils/query_parser.py（BaseQueryParser）
S6: app/models/user.py
S7: app/models/role.py
S8: app/models/alert.py
S9: app/models/log.py
S10: app/models/monitor.py
S11: app/models/network.py
S12: app/interfaces/user_interface.py
S13: app/interfaces/role_interface.py
S14: app/interfaces/alert_interface.py
S15: app/interfaces/log_interface.py
S16: app/interfaces/monitor_interface.py
S17: app/interfaces/network_interface.py
S18: app/services/user.py
S19: app/services/role.py
S20: app/services/alert.py
S21: app/services/log.py
S22: app/services/monitor.py
S23: app/services/network.py
S24: app/api/user.py
S25: app/api/role.py
S26: app/api/alert.py
S27: app/api/log.py
S28: app/api/monitor.py
S29: app/api/network.py
S30: app/api/__init__.py（Blueprint注册）
```

#### 3.1.2 Frontend Agent生成顺序

```
S1: src/utils/request.js（axios封装）
S2: src/router/index.js（路由定义）
S3: src/stores/auth.js（认证Store）
S4: src/composables/useAuth.js
S5: src/layouts/MainLayout.vue
S6: src/components/common/...
S7: src/views/login/index.vue
S8: src/api/user.js
S9: src/views/user/index.vue
S10: ...（按模块重复api+views）
```

### 3.2 衔接依赖图

每个目标文件在生成时需要了解的衔接信息：

**Backend Agent示例**：

| 目标文件 | 需要衔接的前序文件 | 衔接类型 |
|---------|-----------------|---------|
| `app/models/user.py` | `__init__.py`(db实例), `extensions.py`(db) | 导入路径+类名 |
| `app/interfaces/user_interface.py` | `models/user.py`(UserModel签名) | 模型字段签名 |
| `app/services/user.py` | `interfaces/user_interface.py`(ABC签名), `models/user.py`(Model), `utils/*`(工具类签名) | 接口实现+依赖签名 |
| `app/api/user.py` | `services/user.py`(Service签名), `interfaces/user_interface.py` | 服务调用签名 |

### 3.3 衔接信息提取

每次文件生成成功后，从生成结果中提取衔接信息，更新到上下文快照：

```python
def extract_handoff_info(file_path: str, file_content: str, language: str) -> dict:
    """
    从已生成的文件中提取衔接信息。

    Returns:
        {
            "file_path": "app/services/user.py",
            "exports": [
                {"name": "UserService", "type": "class", "signature": "class UserService(IUserManager)"},
                {"name": "list_users", "type": "method", "signature": "def list_users(self, page: int, size: int) -> dict", "parent": "UserService"}
            ],
            "imports": [
                {"from": "app.models.user", "import": "User"},
                {"from": "app.interfaces.user_interface", "import": "IUserManager"}
            ],
            "dependencies": ["app.models.user", "app.interfaces.user_interface"],
            "routes": [],
            "decorators_used": ["@audit_log"]
        }
    """
```

**Python文件提取规则**：

| 提取项 | 规则 | AST节点 |
|--------|------|---------|
| class定义 | class名+基类列表 | `ast.ClassDef` |
| 方法签名 | 方法名+参数+返回类型 | `ast.FunctionDef` / `ast.AsyncFunctionDef` |
| 顶层导入 | from X import Y | `ast.ImportFrom` |
| 装饰器 | 装饰器名+参数 | `ast.decorator_list` |
| Flask路由 | @bp.route / @bp.doc 装饰器参数 | 装饰器AST解析 |

**Vue文件提取规则**：

| 提取项 | 规则 |
|--------|------|
| 组件名 | `<script>` 中 `defineComponent` 的 `name` 属性或文件名PascalCase |
| props | `defineProps` 的类型定义 |
| emits | `defineEmits` 的事件列表 |
| API调用 | `request.get/post/put/delete` 的路径和方法 |
| composables | `useXxx()` 调用列表 |
| 子组件 | `<template>` 中使用的PascalCase标签 |

### 3.4 衔接信息注入

衔接信息通过两种途径注入Prompt：

#### 3.4.1 签名级衔接（JSON_STATE段）

已完成的文件签名注入 `ctx_dependency_signatures` 变量：

```
=== 项目状态 ===
已完成文件：
- app/__init__.py
- app/extensions.py
- app/utils/exceptions.py
- app/models/user.py

依赖签名：
- db: SQLAlchemy实例（from app.extensions import db）
- User: class User(db.Model): id:BIGINT, username:VARCHAR(64), role_id:BIGINT
- ServiceException: class ServiceException(Exception): code:int, message:str
- audit_log: @audit_log(action:str)装饰器

当前模块：user_management
待生成文件：
- app/interfaces/user_interface.py  ← 当前
- app/services/user.py
- app/api/user.py
```

**签名格式规范**：

| 语言 | 签名格式 |
|------|---------|
| Python类 | `ClassName(BaseClass): field1:Type, field2:Type` |
| Python方法 | `def method_name(self, param1:Type, param2:Type) -> RetType` |
| Vue组件 | `ComponentName: props={prop1:Type, prop2:Type}, emits=[event1, event2]` |
| API端点 | `METHOD /path → ResponseType` |

#### 3.4.2 全文级衔接（NEAR_WINDOW段）

当前文件的D0直接依赖以全文方式注入（P3第4节定义的近窗口选择策略）：

```
=== 依赖文件 ===
--- app/models/user.py ---
[完整代码]

--- app/utils/exceptions.py ---
[完整代码]
```

### 3.5 衔接声明（HEAD段）

HEAD段增加衔接声明，明确当前文件与前序文件的关系：

```
{{@segment:HEAD}}
你是Flask后端代码生成器。当前任务：生成 {{task_target_file}}
目标：{{task_objective}}
{{#task_methods}}需要实现的方法：{{task_methods}}{{/task_methods}}

{{#task_handoff_info}}
=== 衔接信息 ===
前序文件：{{task_handoff_preceding}}
本文件将使用的已定义符号：{{task_handoff_symbols}}
本文件在模块中的位置：{{task_handoff_position}}
{{/task_handoff_info}}
{{/segment:HEAD}}
```

**衔接声明变量**：

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `task_handoff_preceding` | SNAPSHOT `.state.completed_files` | 列出与当前文件直接相关的前序文件 |
| `task_handoff_symbols` | SNAPSHOT `.signatures` + 依赖距离计算 | 列出当前文件将使用的已定义符号 |
| `task_handoff_position` | SNAPSHOT `.state.module_progress` | 当前文件在模块中的生成序号 |

### 3.6 衔接约束（TAIL段）

TAIL段增加衔接约束，防止LLM违反前序文件的约定：

```
{{#task_handoff_constraints}}
=== 衔接约束 ===
{{#task_handoff_imports}}
- 导入 {{.}} 时使用已在项目中定义的路径
{{/task_handoff_imports}}
{{#task_handoff_inheritance}}
- 继承 {{.}} 的所有抽象方法，签名必须完全一致
{{/task_handoff_inheritance}}
{{#task_handoff_consistency}}
- 保持与已完成文件一致的：命名风格、异常处理方式、装饰器使用
{{/task_handoff_consistency}}
{{/task_handoff_constraints}}
```

**衔接约束变量**：

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `task_handoff_imports` | extract_handoff_info → imports | 需从已定义文件导入的符号列表 |
| `task_handoff_inheritance` | api_def.json → 当前模块ABC接口 | 需实现的ABC接口列表 |
| `task_handoff_consistency` | SNAPSHOT + DECISION | 一致性约束声明 |

---

## 4. 超长文件分段生成衔接

### 4.1 分段触发条件

| 触发条件 | 阈值 | 说明 |
|---------|------|------|
| 估算代码行数 > 阈值 | 标准≥300行 / 宽裕≥500行 | 基于接口方法数量和复杂度估算 |
| 估算Token > 输出预算的80% | P2定义的输出Token预算 | 防止截断 |
| LLM首次输出截断 | finish_reason="length" | 已发生截断，需分段 |

### 4.2 分段策略

超长文件按逻辑边界分段，而非简单按行数切割：

| 文件类型 | 分段边界 | 示例 |
|---------|---------|------|
| Service文件 | 按方法组分段（CRUD方法 / 业务方法 / 辅助方法） | UserService段1: list+create, 段2: update+delete+业务方法 |
| API Route文件 | 按HTTP方法组分段 | UserAPI段1: GET路由, 段2: POST+PUT+DELETE路由 |
| Vue页面 | 按`<template>`/`<script>`/`<style>`分段 | 段1: template+script逻辑, 段2: style+补充方法 |
| Model文件 | 通常不分段（单表模型不超过阈值） | — |

### 4.3 分段衔接变量

分段生成时，每段的衔接变量包含前段已生成的接口和状态：

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `cont_segment_index` | CONFIG（分段计数器） | 当前是第几段（1-based） |
| `cont_total_segments` | CONFIG（分段规划） | 预计总段数 |
| `cont_previous_segment_summary` | 前段生成结果的摘要 | 前段定义了哪些类/方法/变量 |
| `cont_previous_segment_tail` | 前段输出的最后N行 | 用于续写定位 |
| `cont_pending_methods` | 剩余待生成的方法列表 | 本段需要生成的方法 |

**分段Prompt的HEAD段**：

```
{{@segment:HEAD}}
你是Flask后端代码生成器。当前任务：生成 {{task_target_file}}（第{{cont_segment_index}}/{{cont_total_segments}}段）
目标：{{task_objective}}
{{#cont_previous_segment_summary}}
前段已生成：
{{cont_previous_segment_summary}}
{{/cont_previous_segment_summary}}
本段需要生成的方法：{{cont_pending_methods}}
{{/segment:HEAD}}
```

**分段Prompt的TAIL段**：

```
{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：```python:{{task_target_file}}
- 本段仅生成以下方法，不要重复前段已生成的方法：{{cont_pending_methods}}
- 保持与前段一致的导入声明和类结构
- 方法之间不要有空实现（placeholder），每个方法完整实现
{{#cont_is_last_segment}}
- 这是最后一段，确保所有方法都已实现
{{/cont_is_last_segment}}
{{/segment:TAIL}}
```

### 4.4 分段结果合并

多段生成后，自动合并为完整文件：

```python
def merge_segments(file_path: str, segments: list[str]) -> str:
    """
    合并分段生成结果为完整文件。

    合并规则：
    1. 仅保留第一个段落的导入声明和类定义
    2. 后续段落的方法体追加到类定义内
    3. 去除重复的空行和导入
    4. 校验合并后的AST可解析
    """
    # 1. 解析第一段，提取文件骨架（imports + class定义）
    # 2. 解析后续段落，提取方法定义
    # 3. 将方法定义插入文件骨架
    # 4. 去重导入
    # 5. AST校验
    # 6. 返回完整文件
```

**合并校验**：

| 校验项 | 规则 |
|--------|------|
| AST可解析 | `ast.parse(merged_content)` 无语法错误 |
| 无重复方法 | 同一类中无同名方法 |
| 导入完整 | 所有使用的符号均有导入声明 |
| 类结构完整 | class定义包含所有分段的方法 |

---

## 5. 截断续写衔接

### 5.1 截断检测

LLM输出截断通过P2的 `detect_truncation()` 函数检测：

| 检测信号 | 判定规则 |
|---------|---------|
| `finish_reason="length"` | 明确截断 |
| 代码块未闭合 | 末尾缺少 ` ``` ` 标记 |
| 类/方法未闭合 | 末尾的缩进层级 > 0 |
| JSON未闭合 | 末尾缺少 `}` 或 `]` |

### 5.2 续写Prompt策略

截断续写使用专用的衔接Prompt，不重复已输出内容：

```
{{@segment:HEAD}}
你是代码续写器。当前任务：续写 {{task_target_file}}
前文已输出{{cont_truncated_tokens}}个Token，请在以下上下文处继续输出。

=== 已输出的最后部分 ===
{{cont_truncated_tail}}
=== 续写要求 ===
从上次中断处继续，不要重复已输出的内容。
保持与前文一致的缩进、命名风格和代码结构。
{{/segment:HEAD}}

{{@segment:TAIL}}
=== 输出约束 ===
- 输出格式：```python:{{task_target_file}}（续写部分）
- 仅输出续写内容，不要重复前文
- 保持代码结构完整（闭合所有括号、缩进块）
- 若仍有剩余内容未输出，在末尾标注：[CONTINUED]
{{/segment:TAIL}}
```

### 5.3 续写衔接变量

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `cont_truncated_tokens` | LLM响应的usage.total_tokens | 已输出的Token数 |
| `cont_truncated_tail` | 截断输出的最后20行 | 用于定位续写起点 |
| `cont_is_continuation` | 标志位 | 标识本次是续写调用 |

### 5.4 续写拼接与去重

```python
def merge_continuation(original: str, continuation: str) -> str:
    """
    拼接原始输出和续写输出，去除重叠部分。

    算法：
    1. 取original最后5行作为overlap_candidate
    2. 在continuation开头寻找最长匹配
    3. 截去continuation的匹配部分
    4. 拼接original + continuation
    5. 验证拼接结果AST可解析
    """
```

### 5.5 续写限制

| 限制 | 阈值 | 说明 |
|------|------|------|
| 最大续写次数 | 2次 | 超过2次仍截断 → 转为分段生成（第4节） |
| 续写Token预算 | 原输出Token的1.5倍 | 确保续写有足够空间 |
| 续写总Token上限 | P2定义的最大输出Token | 不可超过 |

---

## 6. 修复循环衔接

### 6.1 修复循环的衔接需求

L2修复循环（A5定义）中，每次修复调用需要携带：

1. 原始文件内容（被修复对象）
2. 校验失败的具体错误（err_变量，P3第6节）
3. 历史修复尝试摘要（避免重复策略）
4. 修复约束（P2构造的修复约束变量）

### 6.2 修复轮次衔接变量

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `err_previous_attempts` | error_context.json | 历史修复尝试摘要 |
| `ctx_current_file_content` | 已生成文件 | 原始文件当前版本 |
| `err_is_last_attempt` | 当前轮次 vs MAX_FIX_ROUNDS | 是否最后一次修复 |

### 6.3 修复轮次递进策略

修复Prompt的衔接信息随轮次递进增强：

**第1轮**：
```
=== 错误信息 ===
错误码：OUTPUT_SCHEMA_VIOLATION
错误描述：缺少list_users方法的返回值类型注解
修复建议：添加 → dict 返回类型注解

=== 当前文件 ===
[app/services/user.py 完整代码]

=== 输出约束 ===
- 仅修复上述错误，保持其他部分不变
```

**第2轮**（第1轮修复未通过）：
```
=== 错误信息 ===
[当前错误]

=== 历史修复 ===
第1轮修复结果：添加了返回类型注解，但注解格式不符合Python类型提示规范
⚠️ 避免重复第1轮的修复策略

=== 当前文件 ===
[app/services/user.py 第1轮修复后的代码]

=== 输出约束 ===
- 仅修复上述错误，保持其他部分不变
- 不要重复第1轮的修复方案
```

**第3轮**（熔断预警）：
```
=== 错误信息 ===
[当前错误]

=== 历史修复 ===
第1轮：添加了返回类型注解，但格式不正确
第2轮：修正了类型注解格式，但引入了新的导入错误

⚠️ 这是最后一次自动修复尝试。如果仍然失败将升级为L3回退。
请仔细审视所有历史修复和错误，确保本次修复一次性解决所有问题。

=== 当前文件 ===
[app/services/user.py 第2轮修复后的代码]

=== 输出约束 ===
- 修复所有已知问题，不要引入新问题
- 这是最后机会，请务必仔细检查
```

### 6.4 修复Prompt与生成Prompt的衔接差异

| 维度 | 生成Prompt | 修复Prompt |
|------|-----------|-----------|
| NEAR_WINDOW | 当前文件的D0/D1依赖文件 | 仅当前文件本身+相关依赖 |
| ERROR_CONTEXT | 不存在 | 错误详情+修复历史 |
| RAG_CONTEXT | 代码模式库检索 | Bug-Fix对库检索 |
| HEAD | 生成任务声明 | 修复任务声明+错误概述 |
| TAIL | 输出格式+衔接约束+禁止项 | 修复范围约束+保持不变约束+禁止引入新问题 |

---

## 7. 并行后校验衔接

### 7.1 Validate Agent的衔接需求

Validate Agent检查前后端代码时，需要同时持有两端的上下文：

| 衔接信息 | 来源 | 用途 |
|---------|------|------|
| api_def.json | ARTIFACT | 契约基准 |
| routes.json | ARTIFACT（Backend产出） | 后端路由注册情况 |
| 前端API调用清单 | 前端代码扫描 | 前端契约对齐情况 |
| 后端接口实现清单 | 后端代码扫描 | 后端契约对齐情况 |

### 7.2 契约对齐校验衔接变量

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `ctx_api_def_relevant` | api_def.json | 被检查文件相关的API契约 |
| `chk_has_contract` | 文件类型判定 | 被检查文件是否有契约校验需求 |
| `ctx_routes_manifest` | routes.json | 后端已注册的路由清单 |

### 7.3 双端校验的衔接策略

Validate Agent按文件逐一校验，每个文件的校验Prompt注入：

1. 被检查文件内容（NEAR_WINDOW段）
2. 相关API契约片段（NEAR_WINDOW段）
3. 校验维度和规则（TAIL段）

校验结果中发现的契约不一致问题，构造修复约束注入fix_模板的ERROR_CONTEXT段。

---

## 8. 回退后重新生成衔接

### 8.1 回退衔接场景

| 回退场景 | 重新生成时的衔接需求 |
|---------|-------------------|
| Backend→API回退 | API Agent需了解后端代码发现的具体问题 |
| Backend→DB回退 | DB Agent需了解数据模型不满足后端需求的具体原因 |
| Frontend→API回退 | API Agent需了解前端代码发现的具体问题 |
| Validate→Backend回退 | Backend Agent需了解校验失败的具体问题 |

### 8.2 回退衔接变量

回退衔接变量由Orchestrator在构造回退dispatch消息时注入：

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `rbk_detail` | Orchestrator构造 | 回退的具体问题描述 |
| `rbk_additional_context` | 上游Agent上下文 | 上游Agent的补充上下文 |
| `rbk_affected_downstream` | 依赖图计算 | 受影响的下游Agent列表 |
| `ctx_original_inputs` | 原始dispatch payload | 回退时原始输入产物 |

### 8.3 回退生成的衔接约束

回退后重新生成的Prompt在TAIL段增加回退约束：

```
=== 回退约束 ===
- 本次生成是因为上游回退：{{rbk_detail}}
- 原始生成结果中存在的问题：{{rbk_additional_context}}
- 修改时需考虑的下游影响：{{rbk_affected_downstream}}
- 保持向后兼容：不删除已有接口/字段，仅可新增
```

---

## 9. 衔接变量解析规则

### 9.1 衔接变量的P3解析扩展

衔接变量是P3 ctx_和task_变量的子集，P4为P3提供衔接维度的解析规则：

| 衔接变量 | P3解析来源 | P4补充规则 |
|---------|-----------|-----------|
| `task_handoff_preceding` | SNAPSHOT + 依赖距离 | 仅保留与当前文件有直接依赖的前序文件 |
| `task_handoff_symbols` | SNAPSHOT `.signatures` | 仅保留当前文件将使用的符号 |
| `task_handoff_position` | SNAPSHOT `.state.module_progress` | 计算当前文件在模块中的序号 |
| `task_handoff_imports` | extract_handoff_info → imports | 列出需从已定义文件导入的符号 |
| `task_handoff_inheritance` | api_def.json → ABC接口 | 需实现的ABC接口列表 |
| `task_handoff_consistency` | DECISION + 已完成文件风格 | 一致性约束（从已生成文件中提取风格样本） |
| `cont_segment_index` | CONFIG（分段计数器） | 当前段序号 |
| `cont_total_segments` | CONFIG（分段规划） | 预计总段数 |
| `cont_previous_segment_summary` | 前段生成结果 | 前段定义的类/方法/变量摘要 |
| `cont_previous_segment_tail` | 前段输出的最后N行 | 续写定位用 |
| `cont_pending_methods` | CONFIG（分段方法分配） | 本段待生成方法 |
| `cont_is_last_segment` | `cont_segment_index == cont_total_segments` | 布尔值 |
| `cont_truncated_tokens` | LLM响应 | 已输出Token数 |
| `cont_truncated_tail` | 截断输出末尾 | 最后20行 |
| `cont_is_continuation` | 标志位 | 是否续写调用 |

### 9.2 衔接变量注入优先级

当Token预算紧张时，衔接变量的注入按以下优先级裁剪：

| 优先级 | 变量 | 裁剪原因 |
|--------|------|---------|
| 不裁剪 | `task_handoff_inheritance` | 接口实现是核心约束 |
| 不裁剪 | `task_handoff_imports` | 导入路径错误导致编译失败 |
| 3 | `task_handoff_symbols` | 可通过NEAR_WINDOW全文推断 |
| 2 | `task_handoff_preceding` | 信息冗余（已在JSON_STATE中） |
| 1 | `task_handoff_position` | 仅辅助理解，非必需 |
| 1 | `task_handoff_consistency` | 可从DECISION_SUMMARY推断 |

### 9.3 衔接变量Token预算

衔接变量从其所属段的Token预算中分配，不额外增加：

| 段 | 衔接变量预算占比 | 说明 |
|----|----------------|------|
| HEAD | 衔接声明 ≤ HEAD预算的30% | 衔接信息是HEAD的附加内容 |
| JSON_STATE | 签名衔接已包含在ctx_dependency_signatures中 | 不额外分配 |
| NEAR_WINDOW | 全文衔接已包含在近窗口文件中 | 不额外分配 |
| TAIL | 衔接约束 ≤ TAIL预算的20% | 衔接约束是TAIL的附加内容 |

---

## 10. 风格一致性衔接

### 10.1 风格样本提取

从已生成的文件中提取风格样本，确保后续文件风格一致：

| 风格维度 | 提取规则 | 示例 |
|---------|---------|------|
| 命名风格 | 变量/函数/类的命名模式 | `snake_case`变量/方法，`PascalCase`类名 |
| 导入风格 | 导入顺序和分组 | 标准库→第三方→本地，每组空行分隔 |
| 异常处理 | 异常类的使用模式 | `raise ServiceException(code=400, message="...")` |
| 装饰器使用 | 装饰器的使用模式 | `@audit_log(action="xxx")` 始终在路由装饰器之后 |
| 日志风格 | logger的使用模式 | `logger.info/error/warning` + 统一消息格式 |
| 注释风格 | 注释密度和格式 | docstring格式：Google风格 |
| 类型注解 | 类型注解的完整度 | 方法参数+返回值均有注解 |

### 10.2 风格样本注入

风格样本不直接注入Prompt，而是通过决策摘要间接传递：

1. 第一个文件生成后，提取风格样本写入 `decision_summary.json`
2. 后续文件的DECISION_SUMMARY段包含风格约定
3. TAIL段的衔接约束中强化风格一致性

**决策摘要中的风格约定**：

```json
{
  "key": "code_style",
  "value": "snake_case命名, Google风格docstring, 完整类型注解, ServiceException异常, @audit_log装饰器",
  "updated_by": "backend_agent",
  "scope": "backend",
  "explanation": "从第一个生成的Service文件中提取的风格约定"
}
```

### 10.3 跨语言风格衔接

前后端并行生成时，风格独立维护：

| 维度 | Backend风格 | Frontend风格 |
|------|-----------|-------------|
| 命名 | snake_case | camelCase(变量/方法), PascalCase(组件) |
| 导入 | Python import规范 | ES Module规范 |
| 异常 | ServiceException | try/catch + ElMessage |
| 状态管理 | 无（Service无状态） | Pinia Store |
| API调用 | 路由处理函数 | axios request封装 |

---

## 11. 衔接状态管理

### 11.1 衔接状态存储

衔接信息存储在上下文快照中，每次文件生成成功后更新：

```json
{
  "version": 5,
  "agent": "backend_agent",
  "state": {
    "completed_files": ["app/__init__.py", "app/extensions.py", "app/models/user.py"],
    "pending_files": ["app/interfaces/user_interface.py", "app/services/user.py"],
    "current_module": "user_management",
    "module_progress": {
      "user_management": "in_progress",
      "alert_management": "pending"
    }
  },
  "signatures": {
    "db": "SQLAlchemy实例（from app.extensions import db）",
    "User": "class User(db.Model): id:BIGINT, username:VARCHAR(64), role_id:BIGINT",
    "ServiceException": "class ServiceException(Exception): code:int, message:str"
  },
  "handoff_registry": {
    "app/models/user.py": {
      "exports": ["User"],
      "imports": ["app.extensions.db"],
      "dependencies": [],
      "routes": []
    }
  },
  "style_sample": {
    "naming": "snake_case",
    "docstring": "Google风格",
    "type_annotation": "完整",
    "exception": "ServiceException",
    "decorator": "@audit_log"
  }
}
```

### 11.2 handoff_registry

`handoff_registry` 是衔接信息的结构化存储，记录每个已完成文件的衔接元数据：

| 字段 | 类型 | 说明 |
|------|------|------|
| `exports` | `list[str]` | 文件导出的符号名列表 |
| `imports` | `list[str]` | 文件依赖的内部模块路径列表 |
| `dependencies` | `list[str]` | 文件依赖的其他项目文件路径列表 |
| `routes` | `list[str]` | 文件注册的API路由路径列表（仅API Route文件） |

### 11.3 衔接状态的快照联动

衔接状态与A4的快照版本链联动：

| 事件 | 快照操作 | 衔接状态更新 |
|------|---------|-------------|
| 文件生成成功 | 新建v(N+1)快照 | 更新signatures + handoff_registry + style_sample |
| 文件修复成功 | 新建v(N+1)快照 | 更新signatures（签名可能变化） |
| 回退到v(K) | 恢复v(K)快照 | 删除v(K+1)之后的handoff_registry条目 |
| 截断续写 | 不新建快照（同一次生成） | 不更新handoff_registry |
| 分段生成完成 | 最后一段完成后新建快照 | 合并所有段后更新handoff_registry |

---

## 12. 衔接校验

### 12.1 生成时衔接校验

每次文件生成后，校验生成结果与衔接信息的一致性：

| 校验项 | 规则 | 错误级别 |
|--------|------|---------|
| 导入路径存在 | 所有import的模块在已完成文件中存在 | warning |
| 接口实现完整 | ABC接口的所有抽象方法均已实现 | error |
| 命名风格一致 | 与handoff_registry中的风格样本一致 | warning |
| 无重复定义 | 不定义已在其他文件中定义的同名类/函数 | error |
| 路由与契约对齐 | API路由路径与api_def.json一致 | error |

### 12.2 衔接校验与P2校验的关系

| 维度 | 衔接校验（P4） | 输出校验（P2） |
|------|--------------|--------------|
| 时机 | 生成后立即 | 解析后 |
| 对象 | 代码与衔接信息的一致性 | 输出格式与约束的合规性 |
| 范围 | 跨文件一致性 | 单文件格式 |
| 失败处理 | 标记warning→注入下次衔接约束 | 标记error→触发L2修复 |

### 12.3 衔接校验结果注入

衔接校验的warning项注入后续文件的衔接约束：

```
=== 衔接约束 ===
- 前序文件 app/services/user.py 的命名风格为snake_case，请保持一致
- 前序文件 app/models/user.py 定义了User类，请通过导入使用，不要重新定义
```

---

## 13. 与其他规范的联动

### 13.1 与A4上下文管理的联动

| A4概念 | P4实现 |
|--------|--------|
| 快照版本链 | handoff_registry存储在快照中，随快照更新 |
| 近窗口选择 | P4的衔接依赖图与P3的依赖距离计算对齐 |
| 回滚流程 | 回滚时清除handoff_registry中对应文件的条目 |
| 决策摘要合并 | 风格样本通过decision_summary跨Agent传递 |

### 13.2 与P1模板的联动

| P1概念 | P4实现 |
|--------|--------|
| HEAD段 | P4增加衔接声明变量（task_handoff_*） |
| TAIL段 | P4增加衔接约束变量（task_handoff_*） |
| 段标记 | P4不新增段标记，使用P1已有段 |
| 组装器 | P4的衔接变量通过P3注入P1组装器 |

### 13.3 与P3上下文注入的联动

| P3概念 | P4实现 |
|--------|--------|
| ctx_变量 | P4定义衔接变量的P3解析规则扩展 |
| 近窗口选择 | P4的衔接依赖图指导P3的近窗口选择 |
| Token裁剪 | P4定义衔接变量的裁剪优先级 |
| SNAPSHOT | P4的handoff_registry存储在SNAPSHOT中 |

### 13.4 与P2输出约束的联动

| P2概念 | P4实现 |
|--------|--------|
| TAIL段填充 | P4的衔接约束与P2的输出约束共同填充TAIL段 |
| 修复约束构造 | P4的修复衔接约束补充P2的修复约束 |
| 截断检测 | P4的续写策略处理P2检测到的截断 |

### 13.5 与A5容错的联动

| A5概念 | P4实现 |
|--------|--------|
| 文件级容错 | P4的衔接信息确保重试时上下文不变 |
| LLM输出截断 | P4定义截断续写的衔接Prompt |
| 修复循环 | P4定义修复轮次的衔接信息递进 |
| L3回退 | P4定义回退后的衔接约束注入 |

### 13.6 与C2代码块格式的联动

| C2概念 | P4实现 |
|--------|--------|
| 文件级输出格式 | P4的衔接信息确保跨文件格式一致 |
| 导入声明格式 | P4的导入衔接约束确保导入声明符合C2规范 |
| 类定义格式 | P4的继承衔接约束确保类定义符合C2规范 |

---

## 14. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，定义衔接场景、多文件顺序生成、超长文件分段、截断续写、修复循环、并行校验、回退重新生成的衔接策略 |
| v1.1 | 2026-04-30 | 交叉审查修复：cxt_前缀统一改为cont_消除与ctx_的混淆 |
