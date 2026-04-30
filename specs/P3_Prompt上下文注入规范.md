# P3 Prompt上下文注入规范

## 1. 总则

### 1.1 目标
定义管线中 Prompt 模板变量（P1 占位符）的数据来源、解析逻辑、注入策略与校验机制，确保上下文从原始产物到模板占位符的映射可追溯、可复现、可调试，实现 A4 四层结构化状态注入到 P1 模板变量的精确落地。

### 1.2 定位

| 维度 | 说明 |
|------|------|
| **与A1的关系** | A1定义了四层结构化状态注入（JSON精确状态/决策摘要/近窗口全文/任务Prompt）的概念模型；P3将此模型落地为变量来源映射与解析规则 |
| **与A4的关系** | A4定义了Token三档制、近窗口分级衰减(D0-D3)、上下文快照与版本链；P3基于A4的分级规则实现变量的选择与裁剪，P3是A4的"填充引擎" |
| **与P1的关系** | P1定义了模板占位符语法（`{{var}}`/`{{#block}}`/`{{@segment}}`）和组装流程；P3定义占位符左侧变量的来源解析，P1定义占位符右侧的渲染规则，二者共同完成变量绑定 |
| **与P2的关系** | P2定义Tail段中输出约束变量的填充规则；P3定义其余段（HEAD/JSON_STATE/DECISION_SUMMARY/NEAR_WINDOW/ERROR_CONTEXT/RAG_CONTEXT）的变量填充规则 |
| **与A3的关系** | A3定义Agent间通信消息格式；P3定义dispatch消息中payload如何映射到Agent上下文变量 |
| **与D1的关系** | D1定义了10个JSON产物的Schema；P3定义这些JSON产物如何被解析、提取、精简后注入模板变量 |
| **与D2的关系** | D2定义JSON版本管理；P3从artifacts/目录读取指定版本的JSON产物 |
| **与D4的关系** | D4定义错误数据规范；P3定义ErrorRecordJSON如何映射为`err_`前缀变量注入ERROR_CONTEXT段 |
| **与P4的关系** | P3定义单次Prompt的上下文注入；P4定义跨次Prompt的衔接信息注入，P4的衔接变量同样由P3的解析逻辑提供 |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **来源可追溯** | 每个模板变量均可追溯到其数据源（JSON产物/上下文快照/管线配置/RAG检索），支持审计 |
| **解析确定性** | 变量解析算法无二义性，相同输入必定产出相同变量值，支持幂等重放 |
| **分级适配** | 变量注入量按A4三档制自适应，紧凑档精简、宽裕档充分，无需手动调参 |
| **按需注入** | 只注入当前Agent当前任务需要的变量，避免无关上下文浪费Token |
| **最小变异** | 同类Agent的变量解析逻辑结构一致，仅数据源不同，减少维护成本 |

---

## 2. 变量来源体系

### 2.1 变量来源分类

所有P1模板变量的值来源于6类数据源：

| 来源 | 标识 | 物理存储 | 典型变量前缀 | 更新频率 |
|------|------|---------|-------------|---------|
| **JSON产物** | `ARTIFACT` | `output/{pipeline_id}/artifacts/` | `ctx_` | 每阶段产出 |
| **上下文快照** | `SNAPSHOT` | `output/{pipeline_id}/state/{agent}_context_v{n}.json` | `ctx_` | 每原子步骤 |
| **决策摘要** | `DECISION` | `output/{pipeline_id}/artifacts/decision_summary.json` | `dec_` | 每Agent追加 |
| **错误数据** | `ERROR` | `output/{pipeline_id}/errors/{agent}_errors.json` | `err_` | L2修复循环 |
| **RAG检索** | `RAG` | 运行时检索结果（不持久化为独立文件） | `rag_` | 每次LLM调用 |
| **管线配置** | `CONFIG` | `pipeline_config.json` + 环境变量 | `cfg_` / `task_` | 管线启动时 |
| **衔接续写** | `CONTINUATION` | P4定义，分段/续写状态 | `cont_` | 每次分段/续写调用 |

> **`cont_`前缀说明**：由P4引入，用于分段生成和截断续写的衔接变量（如`cont_segment_index`、`cont_truncated_tail`等），是`ctx_`变量在衔接场景的专用扩展。详见P4 §2.2和§9.1。

### 2.2 来源-段映射

每个数据源对应P1模板中的特定段：

| 数据源 | 段 | 变量前缀 | Agent类型 |
|--------|---|---------|----------|
| SNAPSHOT | `JSON_STATE` | `ctx_` | 所有Agent |
| DECISION | `DECISION_SUMMARY` | `dec_` | 所有Agent |
| ARTIFACT + SNAPSHOT | `NEAR_WINDOW` | `ctx_` + `nw_` | Backend/Frontend/Validate |
| ERROR | `ERROR_CONTEXT` | `err_` | 修复/回退模板 |
| RAG | `RAG_CONTEXT` | `rag_` | 所有Agent（按需） |
| CONFIG | `HEAD` + `TAIL` | `task_` + `cfg_` | 所有Agent |

### 2.3 变量来源清单

按P1定义的语义前缀，逐变量定义其来源：

#### 2.3.1 `ctx_` 上下文变量

| 变量名 | 来源 | 解析规则 | 必需性 |
|--------|------|---------|--------|
| `ctx_completed_files` | SNAPSHOT `.state.completed_files` | 直接引用，数组→列表变量 | 必需 |
| `ctx_pending_files` | SNAPSHOT `.state.pending_files` | 直接引用，数组→列表变量 | 必需 |
| `ctx_current_module` | SNAPSHOT `.state.current_module` | 直接引用 | 必需 |
| `ctx_dependency_signatures` | SNAPSHOT `.signatures` | JSON对象序列化为文本 | 必需 |
| `ctx_module_progress` | SNAPSHOT `.state.module_progress` | JSON对象序列化为文本 | 可选 |
| `ctx_near_window_files` | ARTIFACT + 依赖距离计算 | 见第4节 | 可选（代码Agent必需） |
| `ctx_current_file_content` | ARTIFACT（当前文件） | 读取已生成文件内容 | 修复模板必需 |
| `ctx_prd_summary` | ARTIFACT `prd_summary.json` | 提取关键章节摘要 | Design Agent必需 |
| `ctx_design_summary` | ARTIFACT `design.json` | 提取modules + architecture | DB/API Agent必需 |
| `ctx_db_model_summary` | ARTIFACT `db_model.json` | 提取表名+字段签名列表 | API Agent必需 |
| `ctx_module_list` | ARTIFACT `design.json` `.modules` | 提取模块名列表 | API Agent必需 |
| `ctx_api_def_relevant` | ARTIFACT `api_def.json` | 按当前模块过滤接口 | Frontend/Validate必需 |
| `ctx_component_registry` | SNAPSHOT `.state.frontend_components` | 已注册组件列表 | Frontend Agent必需 |
| `ctx_composables_registry` | SNAPSHOT `.state.frontend_composables` | 已注册composables列表 | Frontend Agent必需 |
| `ctx_api_endpoints_used` | SNAPSHOT `.state.frontend_api_used` | 已使用API端点列表 | Frontend Agent必需 |
| `ctx_backend_entry` | ARTIFACT `design.json` | 推断：`app/__init__.py` | DevOps Agent必需 |
| `ctx_frontend_entry` | ARTIFACT `design.json` | 推断：`src/main.js` | DevOps Agent必需 |
| `ctx_db_sql_path` | ARTIFACT `pipeline_state.json` `.artifacts.db_sql` | 直接引用 | DevOps Agent必需 |
| `ctx_original_inputs` | ARTIFACT（回退时原始输入产物） | 读取原始JSON | 回退模板必需 |
| `ctx_routes_registry` | SNAPSHOT `.routes` | JSON对象序列化 | Backend Agent必需 |

#### 2.3.2 `dec_` 决策变量

| 变量名 | 来源 | 解析规则 | 必需性 |
|--------|------|---------|--------|
| `dec_decisions` | DECISION `decision_summary.json` `.decisions` | 过滤+格式化，见第5节 | 必需 |

**`dec_decisions` 结构**：列表变量，每个元素含 `dec_key`/`dec_value`/`dec_updated_by`，从 `decision_summary.json` 的扁平化决策项提取。

#### 2.3.3 `err_` 错误变量

| 变量名 | 来源 | 解析规则 | 必需性 |
|--------|------|---------|--------|
| `err_error_code` | ERROR `error_context.json` `.error_code` | 直接引用 | 修复模板必需 |
| `err_error_level` | ERROR `error_context.json` `.error_level` | 直接引用 | 修复模板必需 |
| `err_error_message` | ERROR `error_context.json` `.error_message` | 直接引用 | 修复模板必需 |
| `err_error_details` | ERROR `error_context.json` `.details` | 非空则保留条件块 | 可选 |
| `err_location` | ERROR `error_context.json` `.details.location` | 直接引用 | 可选 |
| `err_fix_suggestion` | ERROR `error_context.json` `.details.fix_suggestion` | 直接引用 | 可选 |
| `err_previous_attempts` | ERROR `error_context.json` `.enrichments.previous_attempts` | 格式化修复历史 | 可选 |
| `err_is_last_attempt` | CONFIG（当前修复轮次 vs MAX_FIX_ROUNDS） | `current_round >= MAX_FIX_ROUNDS` | 可选 |

#### 2.3.4 `rag_` RAG变量

| 变量名 | 来源 | 解析规则 | 必需性 |
|--------|------|---------|--------|
| `rag_has_results` | RAG运行时 | 检索结果非空则为true | 可选（控制条件块） |
| `rag_retrieved_patterns` | RAG运行时 | Top-K结果格式化 | 可选 |
| `rag_pattern_name` | RAG结果项 `.name` | 列表内标量 | 可选 |
| `rag_pattern_content` | RAG结果项 `.content` | 列表内标量 | 可选 |
| `rag_similarity` | RAG结果项 `.score` | 格式化为2位小数 | 可选 |
| `rag_fix_pairs` | RAG运行时（Bug-Fix对库） | 检索结果格式化 | 可选 |
| `rag_fix_description` | Bug-Fix对 `.description` | 列表内标量 | 可选 |
| `rag_before_code` | Bug-Fix对 `.before` | 列表内标量 | 可选 |
| `rag_after_code` | Bug-Fix对 `.after` | 列表内标量 | 可选 |

#### 2.3.5 `task_` 任务变量

| 变量名 | 来源 | 解析规则 | 必需性 |
|--------|------|---------|--------|
| `task_target_file` | CONFIG（调度指令payload） | dispatch消息的`target_file`字段 | 必需 |
| `task_objective` | CONFIG（调度指令payload） | dispatch消息的`objective`字段 | 必需 |
| `task_agent_role` | CONFIG（Agent角色映射表） | 按Agent类型硬编码映射 | 必需 |
| `task_methods` | CONFIG（调度指令payload） | dispatch消息的`methods`字段 | 可选 |
| `task_interface_name` | ARTIFACT `api_def.json` | 按当前模块匹配接口名 | Backend Agent必需 |
| `task_output_language` | CONFIG（Agent输出语言映射） | 按Agent类型硬编码映射 | 必需 |
| `task_components` | CONFIG（调度指令payload） | dispatch消息的`components`字段 | 可选 |
| `task_extra_constraints` | CONFIG（调度指令payload + 设备扩展） | 合并基础约束与设备特有约束 | 可选 |
| `task_prohibitions` | CONFIG（调度指令payload + 设备扩展） | 合并基础禁止项与设备特有禁止项 | 可选 |
| `task_constraint_1` | P2填充函数 | 输出格式约束 | P2负责 |
| `task_constraint_2` | P2填充函数 | 结构约束 | P2负责 |
| `task_user_input` | CONFIG（用户原始输入） | dispatch消息的`user_input`字段 | PRD Agent必需 |

#### 2.3.6 `cfg_` 配置变量

| 变量名 | 来源 | 解析规则 | 必需性 |
|--------|------|---------|--------|
| `cfg_token_tier` | CONFIG `pipeline_config.json` | 直接引用（compact/standard/generous） | 必需 |
| `cfg_max_retry` | CONFIG `pipeline_config.json` | 直接引用 | 可选（默认A5配置） |
| `cfg_deploy_platform` | CONFIG `pipeline_config.json` | 直接引用（github/gitlab/jenkins） | DevOps Agent必需 |
| `cfg_deploy_target` | CONFIG `pipeline_config.json` | 直接引用（docker/k8s） | DevOps Agent必需 |

---

## 3. 变量解析流程

### 3.1 整体流程

```
┌────────────────────────────────────────────────────────────────┐
│ Step 1: 读取管线配置，确定Token档位和当前调度信息                    │
│ Step 2: 读取当前Agent最新上下文快照（SNAPSHOT源）                  │
│ Step 3: 读取决策摘要（DECISION源）                                │
│ Step 4: 按当前任务目标计算依赖距离，选择近窗口文件（ARTIFACT源）     │
│ Step 5: 按需执行RAG检索（RAG源）                                  │
│ Step 6: 若为修复/回退，读取错误数据（ERROR源）                     │
│ Step 7: 从调度指令提取任务变量（CONFIG源）                         │
│ Step 8: 解析各变量（来源→变量值映射）                              │
│ Step 8.5: P5变量值净化（安全净化+脱敏+隔离校验，详见P5 §4.1）       │
│ Step 9: 按Token预算裁剪变量值（P5安全裁剪豁免优先，详见P5 §5.2）    │
│ Step10: 构建variables字典，传递给P1组装器                          │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 解析函数签名

```python
def resolve_variables(
    template_id: str,
    pipeline_id: str,
    agent_id: str,
    dispatch_payload: dict,
    token_budget: dict
) -> dict:
    """
    解析模板所需的所有变量，返回variables字典。

    Args:
        template_id: P1模板ID，如"gen_backend_file"
        pipeline_id: 管线实例ID
        agent_id: Agent标识，如"backend_agent"
        dispatch_payload: A3 dispatch消息的payload
        token_budget: Token预算，如{"tier": "standard", "max_tokens": 32768}

    Returns:
        {
            "variables": {
                "ctx_completed_files": [...],
                "dec_decisions": [...],
                ...
            },
            "resolution_log": [
                {"variable": "ctx_completed_files", "source": "SNAPSHOT",
                 "source_path": "state/backend_context_dv3.json", "tokens": 120}
            ],
            "trim_log": [
                {"variable": "ctx_near_window_files", "action": "removed_D2",
                 "tokens_before": 5000, "tokens_after": 3200}
            ]
        }
    """
```

### 3.3 Step详解

#### Step 1: 读取管线配置

```python
config = load_pipeline_config(pipeline_id)
tier = config["token_budget"]["tier"]  # compact / standard / generous
```

从 `pipeline_config.json` 读取Token档位和Agent级覆盖配置。

#### Step 2: 读取上下文快照

```python
snapshot = load_latest_snapshot(pipeline_id, agent_id)
# 读取 output/{pipeline_id}/state/{agent}_context_v{n}.json 的最新版本
```

快照字段映射：

| 快照字段 | 映射变量 | 转换规则 |
|---------|---------|---------|
| `.state.completed_files` | `ctx_completed_files` | 直接引用 |
| `.state.pending_files` | `ctx_pending_files` | 直接引用 |
| `.state.current_module` | `ctx_current_module` | 直接引用 |
| `.signatures` | `ctx_dependency_signatures` | JSON→缩进文本 |
| `.routes` | `ctx_routes_registry` | JSON→缩进文本 |
| `.state.module_progress` | `ctx_module_progress` | JSON→缩进文本 |
| `.handoff_registry` | P4衔接变量（`task_handoff_*`） | 结构化引用，见P4 §11.2 |
| `.style_sample` | `task_handoff_consistency` | 衔接风格约束来源，见P4 §10.2 |
| `.token_usage` | 用于Token估算参考 | 不注入模板 |

#### Step 3: 读取决策摘要

```python
decision_summary = load_artifact(pipeline_id, "decision_summary.json")
```

详见第5节决策摘要解析。

#### Step 4: 近窗口文件选择

详见第4节依赖距离与近窗口选择。

#### Step 5: RAG检索

```python
rag_results = execute_rag_query(
    knowledge_base=agent_rag_config[agent_id]["kb"],
    query=build_rag_query(template_id, dispatch_payload, snapshot),
    top_k=rag_top_k_by_tier[tier]
)
```

RAG检索按Agent类型选择知识库和检索策略：

| Agent | 知识库 | 检索策略 | query构建 |
|-------|--------|---------|----------|
| PRD | 领域知识库 | 广度扩展 | 用户输入关键词 |
| Design | 架构模式库 | 定向补充 | PRD摘要+模块名 |
| DB | MySQL特性库 | 定向补充 | 概要设计摘要 |
| API | RESTful模式库 | 定向补充 | 模块列表+数据模型 |
| Backend | 代码模式库 + Bug-Fix对 | 代码范例 | 目标文件签名+接口定义 |
| Frontend | UI模板库 + 代码模式库 | UI模板 | 组件类型+API端点 |
| Validate | Bug-Fix对 | 修复范例 | 错误码+错误位置 |
| DevOps | 部署模式库 | 定向补充 | 部署平台+技术栈 |

**Top-K按档位**：

| 档位 | Top-K（patterns） | Top-K（fix_pairs） |
|------|-------------------|-------------------|
| 紧凑 | 1 | 1 |
| 标准 | 3 | 2 |
| 宽裕 | 5 | 3 |

#### Step 6: 错误数据读取

```python
if template_id.startswith("fix_") or template_id.startswith("rbk_"):
    error_data = load_error_context(pipeline_id, agent_id)
```

详见第6节错误上下文注入。

#### Step 7: 任务变量提取

```python
task_vars = extract_task_variables(dispatch_payload, agent_id)
```

从dispatch消息payload直接提取 `task_target_file`、`task_objective` 等字段。`task_agent_role` 和 `task_output_language` 按Agent类型硬编码映射（见P1第4.2/4.8节）。

#### Step 8: 变量解析

按2.3节变量清单逐变量解析，记录 `resolution_log`。

#### Step 9: Token裁剪

按第7节Token预算裁剪规则执行。

#### Step 10: 构建variables字典

将解析后的变量值按P1模板要求的格式组装为 `variables` 字典，传递给 `assemble_prompt()`。

---

## 4. 依赖距离与近窗口选择

### 4.1 依赖距离计算

近窗口文件选择基于A4定义的4级依赖距离，P3将其实现为可计算的规则：

| 距离 | 判定规则 | 注入策略 | 计算方式 |
|------|---------|---------|---------|
| **D0** | 当前文件的直接依赖 | 全文注入 | 目标文件import的Model/Interface/Base类所在文件 |
| **D1** | 同模块近邻（同层协作） | 签名+关键方法体 | 与目标文件同模块、同层（service↔service）的已完成文件 |
| **D2** | 跨模块依赖 | 仅签名 | 目标文件import的其他模块类所在文件 |
| **D3+** | 间接/弱依赖 | 不注入 | 上述之外的已完成文件 |

### 4.2 依赖距离计算算法

```python
def calculate_dependency_distances(
    target_file: str,
    completed_files: list,
    design_json: dict,
    api_def_json: dict
) -> dict:
    """
    计算目标文件对所有已完成文件的依赖距离。

    Returns:
        {
            "app/models/user.py": {"distance": "D0", "reason": "direct_import"},
            "app/services/role.py": {"distance": "D1", "reason": "same_module_same_layer"},
            "app/services/log.py": {"distance": "D2", "reason": "cross_module_import"},
            "app/__init__.py": {"distance": "D3", "reason": "indirect"}
        }
    """
    distances = {}

    # D0判定：目标文件的直接import依赖
    target_imports = extract_imports(target_file, design_json, api_def_json)
    for f in completed_files:
        if file_defines_imported_symbol(f, target_imports):
            distances[f] = {"distance": "D0", "reason": "direct_import"}

    # D1判定：同模块同层
    target_module = get_module(target_file, design_json)
    target_layer = get_layer(target_file)  # model/service/api
    for f in completed_files:
        if f not in distances:
            if get_module(f, design_json) == target_module and get_layer(f) == target_layer:
                distances[f] = {"distance": "D1", "reason": "same_module_same_layer"}

    # D2判定：跨模块import
    for f in completed_files:
        if f not in distances:
            if file_defines_cross_module_symbol(f, target_imports, target_module):
                distances[f] = {"distance": "D2", "reason": "cross_module_import"}

    # D3：其余
    for f in completed_files:
        if f not in distances:
            distances[f] = {"distance": "D3", "reason": "indirect"}

    return distances
```

### 4.3 各Agent近窗口选择规则

不同Agent的近窗口选择规则因职责而异：

#### 4.3.1 Backend Agent

| 目标文件类型 | D0（必含） | D1（含签名+关键方法体） | D2（仅签名） |
|------------|-----------|---------------------|------------|
| Service | 对应Model、对应ABC Interface | 同模块其他Service | 跨模块Service |
| API Route | 对应Service、对应ABC Interface | 同模块其他Route | — |
| Model | 基类Model | 同模块其他Model | — |
| `__init__.py` | 无 | — | — |

**特殊规则**：
- `app/__init__.py` 作为基础设施文件，始终作为D0注入（包含db实例、app工厂等）
- `app/utils/` 下已完成的工具类（如 `BaseQueryParser`、`ServiceException`）按D0注入

#### 4.3.2 Frontend Agent

| 目标文件类型 | D0（必含） | D1（含签名+关键方法体） | D2（仅签名） |
|------------|-----------|---------------------|------------|
| 页面组件 | 对应API调用composable、对应子组件 | 同模块其他页面 | — |
| Composable | 对应api_def.json端点定义 | 同模块其他composable | — |
| 通用组件 | props/interface定义 | — | — |
| 路由配置 | 所有已注册页面组件签名 | — | — |

**额外注入**：Frontend Agent的近窗口段始终包含 `ctx_api_def_relevant`（当前模块相关的API契约片段）。

#### 4.3.3 Validate Agent

近窗口只包含被检查文件本身（D0全文），不注入其他依赖文件。API契约作为 `ctx_api_def_relevant` 注入。

#### 4.3.4 其他Agent

PRD/Design/DB/API/DevOps Agent不使用近窗口段（无需注入已完成代码文件），其上下文通过 `JSON_STATE` 段中的产物摘要获取。

### 4.4 近窗口文件内容读取

```python
def load_near_window_file(file_path: str, distance: str, target_task: str) -> dict:
    """
    读取文件并根据依赖距离裁剪内容。

    Returns:
        {
            "nw_file_path": "app/services/user.py",
            "nw_file_content": "...",  # 按距离裁剪后的内容
            "nw_distance": "D0",
            "nw_trim_applied": ["removed_comments", "removed_blank_lines"]
        }
    """
    full_content = read_file(file_path)

    if distance == "D0":
        return {"nw_file_path": file_path, "nw_file_content": full_content, "nw_distance": "D0"}

    if distance == "D1":
        trimmed = extract_signatures_and_key_methods(full_content, target_task)
        return {"nw_file_path": file_path, "nw_file_content": trimmed, "nw_distance": "D1"}

    if distance == "D2":
        trimmed = extract_signatures_only(full_content)
        return {"nw_file_path": file_path, "nw_file_content": trimmed, "nw_distance": "D2"}

    # D3不注入
    return None
```

**签名提取函数**：

```python
def extract_signatures_only(content: str) -> str:
    """
    提取class定义 + 所有public方法签名（名称+参数类型+返回类型）。
    Python: class X(...): + def method(self, ...) -> RetType:
    Vue: <script setup>中defineProps/emits + export function签名
    """

def extract_signatures_and_key_methods(content: str, target_task: str) -> str:
    """
    D1策略：class签名 + public方法签名 + 关键方法体。
    关键方法判定规则（A4第3.3节）：
    1. 包含业务逻辑（数据库查询/计算/条件分支） → 传方法体
    2. 仅简单代理 → 仅传签名
    3. CRUD标准操作 → 仅传签名
    4. 含特殊处理（权限/审计/设备适配） → 传方法体
    """
```

---

## 5. 决策摘要解析

### 5.1 decision_summary.json 结构

```json
{
  "version": 3,
  "schema_version": 1,
  "decisions": [
    {
      "key": "response_format",
      "value": "{\"code\": int, \"data\": any, \"message\": str}",
      "updated_by": "design_agent",
      "updated_at": "2026-04-30T00:10:00+08:00",
      "scope": "global",
      "explanation": "统一前后端响应格式"
    },
    {
      "key": "audit_decorator",
      "value": "@audit_log(action=\"xxx\")",
      "updated_by": "design_agent",
      "updated_at": "2026-04-30T00:10:00+08:00",
      "scope": "backend",
      "explanation": "装饰器替代AOP实现审计日志"
    }
  ]
}
```

### 5.2 决策项过滤

决策项按Agent和任务类型进行相关性过滤，仅注入相关决策：

| Agent | 必含决策key | 可选决策key |
|-------|-----------|-----------|
| PRD | — | — |
| Design | — | — |
| DB | `database_conventions` | `tech_stack` |
| API | `response_format`, `exception_handling`, `pagination` | `audit_decorator`, `device_identification` |
| Backend | `response_format`, `exception_handling`, `pagination`, `audit_decorator` | `database_conventions`, `device_identification`, `websocket` |
| Frontend | `response_format`, `pagination`, `ui_framework`, `state_management` | `websocket`, `chart_library`, `auth_method` |
| Validate | `response_format`, `exception_handling` | `audit_decorator` |
| DevOps | `tech_stack` | `database_conventions` |

### 5.3 决策摘要格式化

```python
def format_decision_variables(
    decision_summary: dict,
    agent_id: str,
    tier: str
) -> list:
    """
    将decision_summary.json过滤并格式化为dec_decisions列表变量。

    Returns:
        [
            {"dec_key": "response_format", "dec_value": "{code, data, message}", "dec_updated_by": "design_agent"},
            ...
        ]
    """
    relevant_keys = DECISION_RELEVANCE_MAP[agent_id]
    decisions = []

    for item in decision_summary["decisions"]:
        if item["key"] in relevant_keys:
            entry = {
                "dec_key": item["key"],
                "dec_value": item["value"],
            }
            # 紧凑档不注入来源标注
            if tier != "compact":
                entry["dec_updated_by"] = item["updated_by"]
            decisions.append(entry)

    # 紧凑档：仅保留核心5条
    if tier == "compact" and len(decisions) > 5:
        decisions = decisions[:5]

    return decisions
```

### 5.4 决策摘要的scope过滤

决策项的`scope`字段控制可见性：

| scope | 注入规则 |
|-------|---------|
| `global` | 所有Agent可见 |
| `backend` | 仅Backend/Validate/DevOps Agent可见 |
| `frontend` | 仅Frontend/Validate Agent可见 |
| `database` | 仅DB/API/Backend Agent可见 |

---

## 6. 错误上下文注入

### 6.1 错误数据来源

修复/回退模板的`err_`变量来源于`error_context.json`（D4定义）：

```python
def load_error_context(pipeline_id: str, agent_id: str) -> dict:
    """
    读取最新错误上下文。
    优先读取 output/{pipeline_id}/errors/{agent}_errors.json 中
    lifecycle_state != "archived" 的最新错误记录。
    """
```

### 6.2 错误变量映射

| error_context.json字段 | 模板变量 | 转换规则 |
|------------------------|---------|---------|
| `.error_code` | `err_error_code` | 直接引用 |
| `.error_level` | `err_error_level` | 直接引用 |
| `.error_message` | `err_error_message` | 直接引用 |
| `.details.location` | `err_location` | 直接引用 |
| `.details.fix_suggestion` | `err_fix_suggestion` | 直接引用 |
| `.details`（非空） | `err_error_details` | 条件块标志=true |
| `.enrichments.previous_attempts` | `err_previous_attempts` | 格式化修复历史 |

### 6.3 修复历史格式化

```python
def format_previous_attempts(previous_attempts: list, current_round: int) -> list:
    """
    格式化修复历史为err_previous_attempts列表变量。

    Returns:
        [
            {"attempt_round": 1, "attempt_result_summary": "修复了语法错误，但引入了导入缺失"},
            {"attempt_round": 2, "attempt_result_summary": "补充了导入，但接口方法签名不匹配"}
        ]
    """
    result = []
    for attempt in previous_attempts:
        if attempt["round"] < current_round:
            result.append({
                "attempt_round": attempt["round"],
                "attempt_result_summary": attempt.get("summary", "修复未成功")
            })
    return result
```

### 6.4 修复轮次Token预算

按A5 MAX_FIX_ROUNDS=3，不同修复轮次注入不同的错误上下文深度：

| 修复轮次 | 注入内容 | ERROR_CONTEXT段Token预算 |
|---------|---------|------------------------|
| 第1轮 | 当前错误详情 | 紧凑≤200 / 标准≤500 / 宽裕≤1000 |
| 第2轮 | 当前错误 + 第1轮修复摘要 | 紧凑≤300 / 标准≤800 / 宽裕≤1500 |
| 第3轮 | 全部历史 + 熔断预警 | 紧凑≤400 / 标准≤1000 / 宽裕≤2000 |

### 6.5 错误上下文的紧凑化

紧凑档下错误注入需压缩到≤200 tokens，采用以下精简策略：

```
错误码：INPUT_SCHEMA_VIOLATION
位置：app/services/user.py:L45
修复：补充缺失的role_id参数
```

- 省略 `err_error_level`（错误码隐含级别）
- 省略 `err_error_message`（修复建议已包含关键信息）
- 省略 `err_previous_attempts`（第1轮不需要）
- `err_fix_suggestion` 压缩为1行

### 6.6 回退模板的错误注入

回退模板（`rbk_*`）注入的错误变量与修复模板不同，包含回退专用变量：

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `rbk_detail` | dispatch消息payload | 回退的具体问题描述 |
| `rbk_additional_context` | 上游Agent的上下文快照 | 上游Agent的补充上下文 |
| `rbk_affected_downstream` | SNAPSHOT + 依赖图 | 受影响的下游Agent列表 |

---

## 7. Token预算与变量裁剪

### 7.1 变量级Token估算

每个变量解析后估算其Token占用：

```python
def estimate_variable_tokens(variable_name: str, variable_value: any) -> int:
    """
    估算变量的Token占用。
    规则：中文/混合文本 ≈ 4字符/token，代码 ≈ 3字符/token，JSON ≈ 4字符/token
    """
    if isinstance(variable_value, str):
        char_count = len(variable_value)
        ratio = 3 if "code" in variable_name or "file_content" in variable_name else 4
        return max(1, char_count // ratio)
    elif isinstance(variable_value, list):
        return sum(estimate_variable_tokens(variable_name, item) for item in variable_value)
    elif isinstance(variable_value, dict):
        text = json.dumps(variable_value, ensure_ascii=False)
        return max(1, len(text) // 4)
    return 1
```

### 7.2 段级Token预算分配

各段Token预算分配继承P1第8.1节和A4三档制：

| 段 | 紧凑(8K) | 标准(32K) | 宽裕(128K) | 裁剪优先级 |
|----|---------|----------|-----------|-----------|
| HEAD | 5%（~400） | 5%（~1600） | 3%（~3800） | 不裁剪 |
| JSON_STATE | 10%（~800） | 15%（~4900） | 15%（~19000） | 不裁剪 |
| DECISION_SUMMARY | 5%（~400） | 10%（~3200） | 10%（~12800） | 4 |
| NEAR_WINDOW | 30%（~2400） | 40%（~13100） | 40%（~51200） | 2 |
| ERROR_CONTEXT | — | ≤500 | ≤1000 | 3 |
| RAG_CONTEXT | — | ≤800 | ≤2000 | 2 |
| TAIL | 55%（~4400） | 35%（~11400） | 35%（~44800） | 不裁剪 |

**注**：P3负责HEAD/JSON_STATE/DECISION_SUMMARY/NEAR_WINDOW/ERROR_CONTEXT/RAG_CONTEXT段的变量Token预算，TAIL段由P2负责。

### 7.3 变量裁剪优先级

当某段内变量Token超预算时，按以下优先级裁剪该段的变量值：

**NEAR_WINDOW段（裁剪优先级2）**：

| 裁剪级别 | 动作 | 触发条件 |
|---------|------|---------|
| L1 | 移除D2文件（仅签名的跨模块依赖） | 近窗口总Token > 段预算 |
| L2 | D1文件的方法体裁剪为仅签名 | L1后仍超 |
| L3 | D0文件移除注释和空行 | L2后仍超 |
| L4 | D0文件移除非核心方法 | L3后仍超 |
| L5 | 移除D1文件（仅保留D0） | L4后仍超 |

**DECISION_SUMMARY段（裁剪优先级4）**：

| 裁剪级别 | 动作 | 触发条件 |
|---------|------|---------|
| L1 | 移除`dec_updated_by`来源标注 | 决策摘要总Token > 段预算 |
| L2 | 仅保留与当前模块相关的决策 | L1后仍超 |
| L3 | 紧凑档仅保留核心5条 | L2后仍超 |

**RAG_CONTEXT段（裁剪优先级2）**：

| 裁剪级别 | 动作 | 触发条件 |
|---------|------|---------|
| L1 | 仅保留Top-1检索结果 | RAG总Token > 段预算 |
| L2 | 裁剪pattern_content至前500字符 | L1后仍超 |
| L3 | 跳过RAG_CONTEXT段 | L2后仍超 |

**ERROR_CONTEXT段（裁剪优先级3）**：

| 裁剪级别 | 动作 | 触发条件 |
|---------|------|---------|
| L1 | 紧凑化错误描述（6.5节策略） | 错误Token > 段预算 |
| L2 | 省略previous_attempts | L1后仍超 |
| L3 | 仅保留error_code + fix_suggestion | L2后仍超 |

### 7.4 裁剪日志

每次裁剪操作记录到 `trim_log`：

```python
trim_log_entry = {
    "variable": "ctx_near_window_files",
    "segment": "NEAR_WINDOW",
    "trim_level": "L2",
    "action": "D1_methods_trimmed_to_signatures",
    "file": "app/services/role.py",
    "tokens_before": 1800,
    "tokens_after": 600,
    "tokens_saved": 1200,
    "trigger": "segment_over_budget",
    "budget": 13107,
    "actual_after_trim": 11800
}
```

### 7.5 裁剪不可越过的底线

以下变量不可裁剪，即使超Token预算也不移除：

| 变量 | 原因 |
|------|------|
| `task_target_file` | 生成目标锚定 |
| `task_objective` | 任务方向声明 |
| `err_error_code` | 修复目标锚定 |
| `err_error_message`（第3轮除外） | 修复方向锚定 |
| `ctx_current_module` | 模块上下文锚定 |
| P5安全裁剪豁免项 | 安全底线不可裁剪（详见P5 §5.2） |

**P5安全裁剪豁免**：P5 §5.2定义了6项SECURITY_CRITICAL_PATTERNS（危险函数禁止、硬编码敏感信息禁止、越权生成禁止、安全提醒块、修复范围约束、熔断安全预警），这些内容即使在Token超预算时也不可裁剪移除。当P3裁剪逻辑与P5安全豁免冲突时，安全豁免优先。

若裁剪到底线后仍超预算，触发 `PromptOverflowError`，记录错误并按A5升级处理。

---

## 8. 各Agent注入规格

### 8.1 PRD Agent

| 段 | 注入变量 | 来源 |
|----|---------|------|
| HEAD | `task_agent_role`, `task_user_input` | CONFIG |
| RAG_CONTEXT | `rag_has_results`, `rag_retrieved_patterns` | RAG（领域知识库） |
| TAIL | P2负责 | — |

**特殊规则**：
- PRD Agent无上游产物，`JSON_STATE` 和 `DECISION_SUMMARY` 段为空
- RAG检索query = 用户输入的关键词提取结果

### 8.2 Design Agent

| 段 | 注入变量 | 来源 |
|----|---------|------|
| HEAD | `task_agent_role` | CONFIG |
| JSON_STATE | `ctx_prd_summary` | ARTIFACT（PRD摘要） |
| RAG_CONTEXT | `rag_has_results`, `rag_retrieved_patterns` | RAG（架构模式库） |
| DECISION_SUMMARY | `dec_decisions` | DECISION |
| TAIL | P2负责 | — |

**`ctx_prd_summary` 解析规则**：
1. 读取PRD文档（.docx）
2. 提取：项目概述、功能需求列表、非功能需求列表、用户角色
3. 紧凑档：仅提取功能需求标题列表
4. 标准/宽裕档：提取功能需求标题+描述

### 8.3 DB Agent

| 段 | 注入变量 | 来源 |
|----|---------|------|
| HEAD | `task_agent_role` | CONFIG |
| JSON_STATE | `ctx_design_summary` | ARTIFACT（design.json） |
| RAG_CONTEXT | `rag_has_results`, `rag_retrieved_patterns` | RAG（MySQL特性库） |
| DECISION_SUMMARY | `dec_decisions`（过滤数据库相关） | DECISION |
| TAIL | P2负责 | — |

**`ctx_design_summary` 解析规则**：
1. 从 `design.json` 提取 `.modules`（模块名+功能列表）和 `.architecture`
2. 紧凑档：仅模块名列表
3. 标准/宽裕档：模块名+功能列表+架构概要

### 8.4 API Agent

| 段 | 注入变量 | 来源 |
|----|---------|------|
| HEAD | `task_agent_role` | CONFIG |
| JSON_STATE | `ctx_db_model_summary`, `ctx_module_list` | ARTIFACT |
| RAG_CONTEXT | `rag_has_results`, `rag_retrieved_patterns` | RAG（RESTful模式库） |
| DECISION_SUMMARY | `dec_decisions`（过滤接口相关） | DECISION |
| TAIL | P2负责 | — |

**`ctx_db_model_summary` 解析规则**：
1. 从 `db_model.json` 提取所有表名+字段签名（`table_name: col1:type, col2:type, ...`）
2. 紧凑档：仅表名列表
3. 标准/宽裕档：表名+字段签名

**`ctx_module_list` 解析规则**：
1. 从 `design.json` `.modules` 提取模块名列表
2. 所有档位格式一致

### 8.5 Backend Agent

| 段 | 注入变量 | 来源 |
|----|---------|------|
| HEAD | `task_agent_role`, `task_target_file`, `task_objective`, `task_methods`, `task_interface_name` | CONFIG + ARTIFACT |
| JSON_STATE | `ctx_completed_files`, `ctx_pending_files`, `ctx_current_module`, `ctx_dependency_signatures`, `ctx_routes_registry` | SNAPSHOT |
| DECISION_SUMMARY | `dec_decisions`（过滤后端相关） | DECISION |
| NEAR_WINDOW | `ctx_near_window_files` | ARTIFACT + 依赖距离计算 |
| RAG_CONTEXT | `rag_has_results`, `rag_retrieved_patterns`, `rag_fix_pairs` | RAG（代码模式库+Bug-Fix对） |
| ERROR_CONTEXT | `err_*`（仅修复模板） | ERROR |
| TAIL | P2负责 | — |

**`task_interface_name` 解析规则**：
1. 从 `api_def.json` 中按当前模块匹配接口名
2. 例如 `target_file = "app/services/user.py"` → 匹配 `IUserManager`

### 8.6 Frontend Agent

| 段 | 注入变量 | 来源 |
|----|---------|------|
| HEAD | `task_agent_role`, `task_target_file`, `task_objective`, `task_components` | CONFIG |
| JSON_STATE | `ctx_completed_files`, `ctx_pending_files`, `ctx_current_module`, `ctx_component_registry`, `ctx_composables_registry`, `ctx_api_endpoints_used` | SNAPSHOT |
| DECISION_SUMMARY | `dec_decisions`（过滤前端相关） | DECISION |
| NEAR_WINDOW | `ctx_near_window_files`, `ctx_api_def_relevant` | ARTIFACT + 依赖距离计算 |
| RAG_CONTEXT | `rag_has_results`, `rag_retrieved_patterns` | RAG（UI模板库+代码模式库） |
| ERROR_CONTEXT | `err_*`（仅修复模板） | ERROR |
| TAIL | P2负责 | — |

**`ctx_api_def_relevant` 解析规则**：
1. 从 `api_def.json` 按当前模块过滤接口
2. 输出格式：每个接口一行（`GET /api/v1/users → {code, data: UserList, message}`）
3. 紧凑档：仅路径+方法
4. 标准/宽裕档：路径+方法+参数摘要+响应摘要

### 8.7 Validate Agent

| 段 | 注入变量 | 来源 |
|----|---------|------|
| HEAD | `task_agent_role`, `task_target_file`, `chk_scope` | CONFIG |
| NEAR_WINDOW | `ctx_current_file_content`, `ctx_api_def_relevant`（条件） | ARTIFACT |
| ERROR_CONTEXT | `chk_issues`（仅修复模板） | CONFIG |
| TAIL | P2负责 | — |

**特殊规则**：
- Validate Agent不注入 `JSON_STATE` 和 `DECISION_SUMMARY` 段（检查不需要项目全局状态）
- `ctx_current_file_content` 为被检查文件的完整内容
- `chk_has_contract` 条件：当被检查文件属于Backend/Frontend代码时为true

### 8.8 DevOps Agent

| 段 | 注入变量 | 来源 |
|----|---------|------|
| HEAD | `task_agent_role`, `cfg_deploy_platform`, `cfg_deploy_target` | CONFIG |
| JSON_STATE | `ctx_backend_entry`, `ctx_frontend_entry`, `ctx_db_sql_path` | ARTIFACT + 推断 |
| TAIL | P2负责 | — |

**特殊规则**：
- DevOps Agent不需要近窗口（无代码依赖）
- 不需要决策摘要（配置生成不依赖架构决策细节）
- 不使用RAG

---

## 9. A3 dispatch消息到变量的映射

### 9.1 dispatch payload结构

Orchestrator通过A3 dispatch消息调度Agent，payload中的字段直接映射为模板变量：

```json
{
  "type": "dispatch",
  "payload": {
    "pipeline_id": "uuid",
    "agent_id": "backend_agent",
    "stage": "backend",
    "target_file": "app/services/user.py",
    "objective": "实现用户管理服务，继承IUserManager接口",
    "methods": ["list_users", "create_user", "update_user", "delete_user"],
    "extra_constraints": ["使用缓存优化查询性能"],
    "prohibitions": [],
    "input_artifacts": {
      "design_json": "output/{pipeline_id}/artifacts/design.json",
      "db_model_json": "output/{pipeline_id}/artifacts/db_model.json",
      "api_def_json": "output/{pipeline_id}/artifacts/api_def.json"
    }
  }
}
```

### 9.2 payload字段→变量映射

| payload字段 | 模板变量 | 转换规则 |
|------------|---------|---------|
| `.target_file` | `task_target_file` | 直接映射 |
| `.objective` | `task_objective` | 直接映射 |
| `.methods` | `task_methods` | 逗号分隔字符串 |
| `.extra_constraints` | `task_extra_constraints` | 列表变量 |
| `.prohibitions` | `task_prohibitions` | 列表变量 |
| `.input_artifacts` | 不直接映射 | 用于定位JSON产物文件 |
| `.agent_id` | 不映射 | 用于加载SNAPSHOT和选择Agent配置 |
| `.stage` | 不映射 | 用于加载阶段级配置 |

### 9.3 回退调度payload

回退场景的dispatch消息payload额外包含：

| payload字段 | 模板变量 | 说明 |
|------------|---------|------|
| `.rollback_reason` | `err_error_message` | 回退原因描述 |
| `.rollback_detail` | `rbk_detail` | 回退具体问题 |
| `.rollback_additional_context` | `rbk_additional_context` | 上游Agent补充上下文 |
| `.rollback_affected_downstream` | `rbk_affected_downstream` | 受影响下游Agent列表 |

---

## 10. 变量解析校验

### 10.1 解析前校验

变量解析前校验数据源的可用性：

| 校验项 | 规则 | 错误级别 |
|--------|------|---------|
| 快照文件存在 | SNAPSHOT文件路径有效 | error（中止） |
| 快照版本合法 | v(N)的N ≥ 1 | error（中止） |
| 决策摘要存在 | decision_summary.json可读 | error（中止） |
| 产物文件可读 | dispatch中引用的所有artifact路径可读 | error（中止） |
| 模板ID合法 | template_id在P1定义的模板清单中 | error（中止） |
| Agent ID合法 | agent_id在A1定义的Agent清单中 | error（中止） |

### 10.2 解析后校验

变量解析后校验变量值的完整性：

| 校验项 | 规则 | 错误级别 |
|--------|------|---------|
| 必需变量非空 | 2.3节标记为"必需"的变量必须非空 | error（中止组装） |
| 变量类型正确 | 列表变量必须为list，标量变量必须为str | error（尝试转换） |
| Token不超预算 | 各段Token估算值 ≤ 段预算 × 1.1（含裁剪后） | error（继续裁剪） |
| 近窗口D0非空 | 代码Agent必须有至少1个D0依赖文件 | warning（仅记录） |
| 决策摘要非空 | 至少1条相关决策项 | warning（仅记录） |
| RAG结果有效 | `rag_has_results=true`时列表非空 | warning（降级为false） |

### 10.3 校验错误处理

| 错误级别 | 处理方式 |
|---------|---------|
| error | 抛出 `ContextResolutionError`，记录到error_context.json，通知Orchestrator |
| warning | 记录到resolution_log，继续执行 |

```python
class ContextResolutionError(Exception):
    """上下文解析错误"""
    def __init__(self, variable: str, reason: str, source: str):
        self.variable = variable
        self.reason = reason
        self.source = source
```

---

## 11. 解析记录与审计

### 11.1 解析记录格式

每次变量解析保存完整记录：

```json
{
  "resolution_id": "uuid",
  "timestamp": "2026-04-30T10:30:00+08:00",
  "pipeline_id": "uuid",
  "agent_id": "backend_agent",
  "template_id": "gen_backend_file",
  "token_tier": "standard",
  "variables_resolved": 18,
  "variables_skipped": 3,
  "resolution_log": [
    {
      "variable": "ctx_completed_files",
      "source": "SNAPSHOT",
      "source_path": "output/{pipeline_id}/state/backend_context_dv3.json",
      "source_field": ".state.completed_files",
      "tokens": 120,
      "trim_applied": null
    },
    {
      "variable": "ctx_near_window_files",
      "source": "ARTIFACT+DISTANCE",
      "source_detail": "D0:2 files, D1:1 file",
      "tokens": 8500,
      "trim_applied": "L1:removed_D2_signatures"
    }
  ],
  "trim_log": [...],
  "total_tokens": 24500,
  "budget_tokens": 32768,
  "utilization_rate": 0.748,
  "validation_warnings": [
    "近窗口无D0依赖文件: app/__init__.py（基础设施文件，降级为D1）"
  ]
}
```

### 11.2 存储路径

```
output/{pipeline_id}/logs/context_resolutions/{agent}_{resolution_id}.json
```

与P1组装记录一一对应（同一pipeline_id + agent + 序号）。

### 11.3 审计指标

| 指标 | 计算方式 | 用途 |
|------|---------|------|
| 变量解析成功率 | `variables_resolved / (variables_resolved + variables_failed)` | 衡量数据源稳定性 |
| Token利用率 | `total_tokens / budget_tokens` | 衡量预算合理性 |
| 裁剪触发率 | `含裁剪的解析次数 / 总解析次数` | 衡量预算充足性 |
| 近窗口D0覆盖率 | `有D0文件的解析次数 / 代码Agent解析次数` | 衡量依赖完整性 |
| RAG注入率 | `rag_has_results=true的解析次数 / 总解析次数` | 衡量RAG有效性 |

### 11.4 审计指标与A5断路器联动

| 指标 | 阈值 | 断路器动作 |
|------|------|-----------|
| 变量解析成功率 < 80% | 连续3次 | 触发Agent级断路器 |
| Token利用率 > 95% | 连续5次 | 触发模块级断路器（Token预算不足） |
| 近窗口D0覆盖率 < 50% | 连续3次 | 触发Agent级断路器（依赖不完整） |

---

## 12. 与其他规范的联动

### 12.1 与A4上下文管理的联动

| A4概念 | P3实现 |
|--------|--------|
| Token三档制 | 变量级Token估算 + 段级预算分配 + 按档裁剪 |
| 四层状态注入 | 变量来源映射：SNAPSHOT→JSON_STATE, DECISION→DECISION_SUMMARY, ARTIFACT→NEAR_WINDOW, CONFIG→HEAD/TAIL |
| D0-D3分级衰减 | 依赖距离计算算法(4.2) + 按距离裁剪策略(4.4) |
| 快照版本链 | load_latest_snapshot读取最新版本 |
| 9步组装流程 | P3的10步解析流程覆盖A4的9步（P3 Step1-7 = A4 Step1-7, P3 Step8-10 = A4 Step8-9的细化） |

### 12.2 与P1模板的联动

| P1概念 | P3实现 |
|--------|--------|
| 模板占位符 `{{var}}` | P3提供var的值，P1负责渲染 |
| 条件块 `{{#block}}` | P3决定block条件变量的真假值 |
| 段标记 `{{@segment:X}}` | P3决定段内变量的Token预算和裁剪策略 |
| 组装器 `assemble_prompt()` | P3的 `resolve_variables()` 为组装器提供variables参数 |
| 裁剪优先级 | P3段级裁剪 + P1段标记裁剪 一致 |

### 12.3 与P2输出约束的联动

| P2概念 | P3实现 |
|--------|--------|
| Tail段约束变量 | P3不负责Tail段填充（P2负责） |
| 修复约束构造 | P3的err_变量来源 + P2的修复约束变量 共同注入fix_模板 |
| 校验-修复闭环 | P3从ERROR源读取错误 → P2构造修复约束 → P3解析错误+P2约束 → P1组装 |

### 12.4 与A3通信的联动

| A3概念 | P3实现 |
|--------|--------|
| dispatch消息 | dispatch payload → task_变量映射 |
| result消息 | 解析结果随result消息回传（含resolution_log摘要） |
| data_request/response | Agent通过data_request获取额外产物 → P3解析为ctx_变量 |

### 12.5 与D1/D2的联动

| D1/D2概念 | P3实现 |
|-----------|--------|
| JSON Schema校验 | 解析前校验SNAPSHOT和ARTIFACT文件的Schema合规性 |
| 版本号 | load_artifact读取指定版本的产物 |
| artifacts目录 | P3从artifacts/目录读取JSON产物 |

### 12.6 与D4错误数据的联动

| D4概念 | P3实现 |
|--------|--------|
| ErrorRecordJSON | `err_` 变量映射到ErrorRecordJSON字段 |
| 错误生命周期 | 仅读取非archived状态的错误记录 |
| Bug-Fix对提取 | 修复成功后P3的解析记录可作为Bug-Fix对入库素材 |
| 错误注入Token预算 | 紧凑档≤200 tokens（D4第15章）直接应用于ERROR_CONTEXT段 |

---

## 13. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，定义变量来源体系、解析流程、依赖距离计算、决策摘要过滤、错误注入、Token裁剪、各Agent注入规格 |
| v1.1 | 2026-04-30 | 交叉审查修复：Step8后补充P5净化步骤引用、变量来源体系补充cont_前缀、快照映射补充handoff_registry/style_sample、裁剪底线补充P5安全豁免 |
