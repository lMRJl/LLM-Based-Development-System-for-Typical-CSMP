# P2 Prompt输出约束规范

## 1. 总则

### 1.1 目标
定义管线中 LLM 输出的格式约束、解析规则、校验机制与不合格输出的处理策略，确保 LLM 产出可被下游可靠消费的标准化内容，消除"LLM自由输出→手工解析"的脆弱环节。

### 1.2 定位

| 维度 | 说明 |
|------|------|
| **与P1的关系** | P1定义了Prompt-Tail段的结构和占位符；P2定义Tail段中输出约束变量的具体填充规则、输出格式声明语法和校验逻辑 |
| **与D1的关系** | D1定义了10个JSON产物的Schema；P2定义LLM如何按Schema产出JSON，以及产出后的Schema校验流程 |
| **与A5的关系** | A5定义了L2修复循环；P2定义输出校验失败时如何构造修复约束注入修复Prompt |
| **与D4的关系** | D4定义了错误数据规范；P2定义输出校验失败时产生的错误码和错误记录格式 |
| **与V系列的关系** | V系列校验的是代码质量；P2校验的是LLM输出的格式合规性，发生在V系列之前 |
| **与P3的关系** | P3定义上下文注入变量的来源和解析；P2定义输出约束变量的来源和填充，二者共同完成P1模板的变量绑定 |
| **与C2的关系** | C2定义代码块的输出格式规范（文件级）；P2定义Prompt层面的输出格式约束声明（Prompt级），C2是P2约束的具体化 |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **格式先行** | Prompt中必须先声明输出格式再要求生成内容，利用近因效应收束LLM输出 |
| **结构化优先** | 能用JSON Schema约束的输出不用自然语言描述，能枚举的不开放 |
| **解析确定性** | 每种输出格式必须有确定的解析算法，不留二义性 |
| **校验即反馈** | 校验失败的结果立即反馈给修复Prompt，形成闭环 |
| **最小输出** | 只要求LLM产出下游必需的内容，避免冗余输出浪费Token |

### 1.4 输出约束生命周期

```
约束声明（P2填充Tail段）
     │
     ▼
LLM生成输出
     │
     ▼
输出解析（按声明格式提取）
     │
     ├── 解析成功 → Schema校验（D1）
     │                │
     │                ├── 校验通过 → 交付下游
     │                │
     │                └── 校验失败 → 生成修复约束 → L2修复循环
     │
     └── 解析失败 → 生成修复约束 → L2修复循环
```

---

## 2. 输出格式声明

### 2.1 格式声明语法

在Prompt-Tail段中，输出格式声明使用以下语法：

```
- 输出格式：```{language}:{filepath}
```

其中：
- `{language}` — 输出语言标记，标识LLM应使用的代码/标记语言
- `{filepath}` — 目标文件路径，标识输出属于哪个文件

**格式声明的作用**：

| 作用 | 说明 |
|------|------|
| **锚定语言** | LLM按指定语言生成，避免混淆Python/JavaScript/SQL等 |
| **锚定文件** | LLM只生成目标文件的代码，不"附带"生成其他文件 |
| **解析标记** | 解析器通过 ````{language}:{filepath}`` 标记定位输出内容的起止 |

### 2.2 各Agent输出格式映射

| Agent | `language` | `filepath` 示例 | 输出类型 |
|-------|-----------|----------------|---------|
| PRD Agent | `markdown` | `docs/prd.md` | 文档 |
| Design Agent | `markdown` | `docs/design.md` | 文档 + JSON |
| DB Agent | `sql` | `database/init.sql` | SQL + JSON + 文档 |
| API Agent | `json` | `api/api_def.json` | JSON + 文档 |
| Backend Agent | `python` | `app/services/user.py` | 代码 |
| Frontend Agent | `vue` | `src/views/UserManagement.vue` | 代码 |
| Validate Agent | `json` | `reports/validation_report.json` | JSON |
| DevOps Agent | `yaml` | `deploy/docker-compose.yml` | 配置 |

### 2.3 多段输出声明

当Agent一次Prompt需要产出多个文件时（如DB Agent产出SQL+JSON+文档），使用多段声明：

```
- 输出格式（按顺序输出以下内容）：
  1. SQL文件：```sql:database/init.sql
  2. 数据模型：```json:database/db_model.json
  3. 设计说明：```markdown:docs/db_design.md
```

**单次Prompt输出文件数上限**：

| Agent | 单次最大输出文件数 | 说明 |
|-------|-----------------|------|
| PRD Agent | 1 | 单一文档 |
| Design Agent | 2 | 文档 + JSON |
| DB Agent | 3 | SQL + JSON + 文档 |
| API Agent | 2 | JSON + 文档 |
| Backend Agent | 1 | 单文件逐一生成（防截断，A1策略） |
| Frontend Agent | 1 | 单文件逐一生成 |
| Validate Agent | 1 | 检查报告JSON |
| DevOps Agent | 7 | 多文件配置（Dockerfile×2 + compose + nginx + CI + env + 脚本） |

### 2.4 DevOps多文件输出的特殊处理

DevOps Agent一次性输出多个文件，为避免截断和混淆，采用**标记分隔+序号**策略：

```
- 输出格式（按顺序输出，每个文件用 ``` 标记分隔）：
  [1/7] ```dockerfile:deploy/Dockerfile.backend
  [2/7] ```dockerfile:deploy/Dockerfile.frontend
  [3/7] ```yaml:deploy/docker-compose.yml
  [4/7] ```nginx:deploy/nginx.conf
  [5/7] ```yaml:deploy/.github/workflows/ci.yml
  [6/7] ```env:deploy/.env.example
  [7/7] ```bash:deploy/scripts/start.sh
```

---

## 3. 输出约束类型

### 3.1 约束分类

输出约束分为5类，在Prompt-Tail中按优先级排列：

| 优先级 | 约束类型 | 说明 | 示例 |
|--------|---------|------|------|
| 1 | **格式约束** | 输出的语言/文件/结构 | ````python:app/services/user.py` |
| 2 | **结构约束** | 必须包含的结构元素 | "严格继承IUserManager的所有抽象方法" |
| 3 | **内容约束** | 必须包含或禁止的内容 | "每个方法添加@audit_log装饰器" |
| 4 | **对齐约束** | 与已有产物保持一致 | "API调用必须与api_def.json完全一致" |
| 5 | **禁止项** | 不允许出现的内容 | "不要重复导入已在依赖文件中定义的类" |

### 3.2 格式约束

格式约束是最强的约束类型，直接决定输出的解析方式。

#### 3.2.1 代码类输出

```
- 输出格式：```python:app/services/user.py
- 仅输出该文件的代码，不要包含其他文件
- 不要在代码块外添加说明文字
```

**代码类输出解析规则**：
1. 定位 ````python:app/services/user.py```` 标记
2. 提取标记后至下一个 ``````` `` 或文本末尾的内容
3. 去除首尾空行
4. 写入目标文件路径

#### 3.2.2 JSON类输出

```
- 输出格式：```json:api/api_def.json
- 严格遵循D1 api_def.json Schema
- 不要在JSON代码块外添加说明
```

**JSON类输出解析规则**：
1. 定位 ````json:...```` 标记
2. 提取JSON文本
3. `json.loads()` 解析
4. D1 Schema校验（jsonschema库）
5. 校验通过 → 写入文件

#### 3.2.3 SQL类输出

```
- 输出格式：```sql:database/init.sql
- 每张表一个独立的CREATE TABLE语句
- 语句间用空行分隔
- 不要在SQL代码块外添加说明
```

**SQL类输出解析规则**：
1. 定位 ````sql:...```` 标记
2. 提取SQL文本
3. 按 `;` 分割为独立语句
4. 校验每个语句以 `CREATE`/`INSERT`/`ALTER`/`DROP`/`SET`/`USE` 开头
5. 写入SQL文件

#### 3.2.4 文档类输出

```
- 输出格式：```markdown:docs/prd.md
- 按以下章节结构输出：
  1. 项目概述
  2. 功能需求（按模块分节）
  3. 非功能需求
  4. 用户角色与权限
  5. 业务流程描述
```

**文档类输出解析规则**：
1. 定位 ````markdown:...```` 标记
2. 提取Markdown文本
3. 校验章节结构是否包含所有要求的标题
4. 写入Markdown文件（后续由docx Skill转换为.docx）

#### 3.2.5 配置类输出

```
- 输出格式：```yaml:deploy/docker-compose.yml
- 不要在YAML代码块外添加说明
```

**配置类输出解析规则**：
1. 定位代码块标记
2. 提取文本
3. 对YAML进行 `yaml.safe_load()` 校验语法
4. 写入目标文件

### 3.3 结构约束

结构约束确保输出包含必需的结构元素。

#### 3.3.1 约束声明方式

```
=== 结构约束 ===
- 必须继承 {{task_interface_name}} 的所有抽象方法
- 必须包含类定义和所有方法实现
- 方法顺序与接口定义一致
```

#### 3.3.2 各Agent的结构约束

| Agent | 结构约束 |
|-------|---------|
| PRD Agent | 必须包含5个章节（项目概述/功能需求/非功能需求/用户角色/业务流程） |
| Design Agent | 必须包含架构模式+模块划分+技术选型；JSON必须遵循design.json Schema |
| DB Agent | SQL必须包含所有表的CREATE TABLE；db_model.json必须遵循Schema |
| API Agent | api_def.json必须包含所有模块的接口定义；遵循ABC三层架构 |
| Backend Agent | 必须继承ABC接口的所有抽象方法；必须包含所有路由注册 |
| Frontend Agent | 组件必须包含template+script+style三段；API调用必须对齐api_def.json |
| Validate Agent | 输出必须遵循validation_report.json Schema；每个问题标注严重级别和位置 |
| DevOps Agent | 必须输出7个配置文件；Dockerfile必须指定基础镜像和工作目录 |

#### 3.3.3 结构约束校验

结构约束的校验不是Schema校验（D1负责），而是**结构完整性校验**：

| 校验类型 | 实现方式 | 失败处理 |
|---------|---------|---------|
| 接口方法完整性 | 比对ABC接口的方法列表 vs 实现类的方法列表 | 标记缺失方法，触发L2修复 |
| 章节完整性 | 检查Markdown中是否包含所有要求的标题 | 标记缺失章节，触发L2修复 |
| 文件完整性 | 检查多文件输出中所有文件是否都已产出 | 标记缺失文件，触发L2修复 |
| Schema字段完整性 | jsonschema校验（D1定义） | 标记缺失/多余字段，触发L2修复 |

### 3.4 内容约束

内容约束确保输出中的具体内容符合规范。

#### 3.4.1 约束声明方式

```
=== 内容约束 ===
- 每个方法添加@audit_log装饰器，action参数与方法名对应
- 使用ServiceException(code, message)处理业务异常
- 分页使用BaseQueryParser，返回 {items, total, page, size}
- 设备适配使用 current_device = get_device_type()
```

#### 3.4.2 内容约束的分类

| 约束来源 | 说明 | 示例 |
|---------|------|------|
| **决策摘要** | 来自decision_summary.json的全局约定 | 响应格式{code,data,message}、ServiceException异常类 |
| **接口契约** | 来自api_def.json的接口定义 | 接口路径、HTTP方法、参数、响应格式 |
| **数据模型** | 来自db_model.json的表结构 | 字段类型、外键关系、索引 |
| **代码规范** | 来自C系列规范的编码约定 | 命名规范、导入规范、错误处理规范 |

#### 3.4.3 内容约束的校验

内容约束校验发生在Validate Agent的Layer2（LLM代码审查），不在此规范定义。P2只负责将约束声明注入Prompt，确保LLM在生成时知晓约束。

### 3.5 对齐约束

对齐约束确保输出与已有产物保持一致，是契约驱动并行的核心保障。

#### 3.5.1 契约对齐约束

```
=== 契约对齐 ===
- API调用路径必须与api_def.json完全一致
- 请求参数名和类型必须与api_def.json定义一致
- 响应数据结构必须与api_def.json定义一致
- 不要硬编码API路径，从api_def.json中引用
```

**对齐校验**（Validate Agent Layer2.5执行）：

| 校验对象 | 校验内容 | 不一致处理 |
|---------|---------|-----------|
| 后端routes vs api_def.json | 路由路径、HTTP方法、参数 | 标记L2错误，定向修复后端 |
| 前端API调用 vs api_def.json | 请求路径、方法、参数 | 标记L2错误，定向修复前端 |
| 后端响应格式 vs api_def.json | 响应字段、类型 | 标记L2错误，定向修复后端 |

#### 3.5.2 依赖对齐约束

```
=== 依赖对齐 ===
- 不要重复导入已在依赖文件中定义的类
- 引用依赖文件中的类时，使用与依赖文件一致的导入路径
- 继承的接口方法签名必须与ABC接口定义完全一致
```

### 3.6 禁止项

禁止项是最强的负面约束，明确列出不允许出现的内容。

#### 3.6.1 通用禁止项

适用于所有Agent的生成Prompt：

```
=== 禁止项 ===
- 不要生成非目标文件的代码
- 不要在代码块外添加说明文字（除非输出类型为文档）
- 不要修改依赖文件中的任何内容
- 不要添加TODO/FIXME/HACK等临时标记
- 不要使用eval()、exec()等危险函数
```

#### 3.6.2 各Agent特有禁止项

| Agent | 特有禁止项 |
|-------|-----------|
| PRD Agent | 不做架构决策、不做技术选型、不生成代码 |
| Design Agent | 不修改PRD内容、不做数据库表设计、不生成代码 |
| DB Agent | 不修改概要设计、不生成API接口、不生成代码 |
| API Agent | 不修改数据库设计、不生成业务代码实现、不在接口中暴露实现细节 |
| Backend Agent | 不修改接口定义、不生成前端代码、不做数据库设计、不硬编码配置 |
| Frontend Agent | 不修改后端代码、不修改API路由、不做数据库设计、不直接操作DOM |
| Validate Agent | 不做业务逻辑修改、不修改接口定义、不修改数据库设计 |
| DevOps Agent | 不修改应用代码、不修改数据库设计、不硬编码敏感信息 |

---

## 4. 输出解析

### 4.1 解析器架构

```python
class OutputParser:
    """LLM输出解析器基类"""

    def __init__(self, output_format: str, target_path: str):
        self.output_format = output_format  # python/json/sql/markdown/vue/yaml
        self.target_path = target_path

    def parse(self, llm_output: str) -> ParsedOutput:
        """
        解析LLM输出，提取目标内容

        Returns:
            ParsedOutput {
                success: bool,
                content: str,           # 提取的内容
                target_path: str,       # 目标文件路径
                raw_output: str,        # 原始LLM输出
                parse_errors: list,     # 解析错误列表
                warnings: list          # 警告列表
            }
        """
        raise NotImplementedError

    def validate(self, parsed: ParsedOutput) -> ValidationResult:
        """
        校验解析后的内容

        Returns:
            ValidationResult {
                valid: bool,
                errors: list,           # 校验错误
                warnings: list          # 校验警告
            }
        """
        raise NotImplementedError
```

### 4.2 代码块提取算法

所有输出类型统一使用代码块标记提取：

```python
def extract_code_blocks(llm_output: str) -> list:
    """
    从LLM输出中提取所有代码块

    Returns:
        [
            {
                "language": "python",
                "filepath": "app/services/user.py",
                "content": "...",
                "start_pos": 120,
                "end_pos": 3500
            }
        ]
    """
    import re
    pattern = r'```(\w+)(?::([^\n]+))?\n(.*?)```'
    matches = re.findall(pattern, llm_output, re.DOTALL)

    results = []
    for lang, filepath, content in matches:
        results.append({
            "language": lang,
            "filepath": filepath or None,
            "content": content.strip()
        })
    return results
```

### 4.3 解析策略

| 输出类型 | 解析策略 | 容错处理 |
|---------|---------|---------|
| **代码** (`python`/`vue`) | 提取第一个匹配语言标记的代码块 | 若无标记，全文作为代码内容（发出警告） |
| **JSON** | 提取代码块 → `json.loads()` | 若代码块无标记，尝试全文解析 |
| **SQL** | 提取代码块 → 按`;`分割 | 合并跨行的SQL语句 |
| **Markdown** | 提取代码块（文档整体即内容） | 无代码块标记时全文为文档内容 |
| **YAML** | 提取代码块 → `yaml.safe_load()` | 语法错误时标记解析失败 |
| **Dockerfile** | 提取代码块 | 无特殊解析，直接写入 |

### 4.4 多文件输出解析

DevOps Agent等多文件输出场景，按序号标记解析：

```python
def extract_multi_file_output(llm_output: str, expected_files: list) -> dict:
    """
    解析多文件输出

    Args:
        expected_files: 期望的文件列表，如 ["Dockerfile.backend", "docker-compose.yml", ...]

    Returns:
        {
            "Dockerfile.backend": "FROM python:3.11...",
            "docker-compose.yml": "version: '3.8'...",
            ...
        }
    """
    blocks = extract_code_blocks(llm_output)

    result = {}
    for block in blocks:
        filepath = block["filepath"]
        if filepath:
            # 从完整路径中提取文件名
            filename = filepath.split("/")[-1]
            result[filename] = block["content"]
        else:
            # 无路径标记的代码块，按顺序匹配期望文件
            idx = len(result)
            if idx < len(expected_files):
                result[expected_files[idx]] = block["content"]

    return result
```

### 4.5 解析失败处理

| 失败类型 | 错误码 | 处理策略 |
|---------|--------|---------|
| 未找到代码块标记 | `LLM_FORMAT_ERROR` | L2修复：追加"请使用 ```{language}:{filepath}``` 格式输出" |
| JSON解析失败 | `JSON_FORMAT_ERROR` | L2修复：追加具体解析错误位置 |
| SQL语法错误 | `LLM_FORMAT_ERROR` | L2修复：追加错误语句 |
| 代码块语言标记不匹配 | `LLM_FORMAT_ERROR` | L2修复：提示正确的语言标记 |
| 多文件输出缺少文件 | `CODE_GENERATION_INCOMPLETE` | L2修复：列出缺失文件 |
| 代码块外有重要内容 | — | 警告，不阻断（可能是有用说明） |

---

## 5. 输出校验

### 5.1 校验层次

输出校验分3层，由快到慢、由浅入深：

| 层次 | 校验内容 | 执行时机 | 耗时 |
|------|---------|---------|------|
| **L1 格式校验** | 代码块标记、语言标记、文件路径 | 解析完成后立即执行 | <10ms |
| **L2 Schema校验** | JSON产物的Schema合规性 | L1通过后 | <50ms |
| **L3 结构校验** | 结构完整性、内容约束对齐 | L2通过后，由Agent执行 | <500ms |

### 5.2 L1 格式校验

| 校验项 | 规则 | 失败处理 |
|--------|------|---------|
| 代码块存在 | 至少1个 ````{lang}:{path}```` 标记 | 解析失败，L2修复 |
| 语言标记正确 | 标记与Prompt声明一致 | 警告，继续解析 |
| 文件路径正确 | 标记路径与目标文件一致 | 警告，继续解析 |
| 无额外代码块 | 不含目标文件以外的代码块 | 警告（可能LLM多生成了内容） |
| 代码块外无关键内容 | 代码块外无明显的代码片段 | 警告（可能LLM将代码放在了标记外） |

### 5.3 L2 Schema校验

仅适用于JSON类输出（api_def.json、db_model.json、validation_report.json等）。

```python
def validate_json_schema(json_content: dict, schema_name: str) -> ValidationResult:
    """
    校验JSON输出是否符合D1定义的Schema

    Args:
        json_content: 解析后的JSON对象
        schema_name: Schema名称，如 "api_def", "db_model"

    Returns:
        ValidationResult 包含校验结果和错误详情
    """
    import jsonschema

    schema = load_schema(schema_name)  # 从D1定义加载
    try:
        jsonschema.validate(instance=json_content, schema=schema)
        return ValidationResult(valid=True, errors=[], warnings=[])
    except jsonschema.ValidationError as e:
        return ValidationResult(
            valid=False,
            errors=[{
                "path": ".".join(str(p) for p in e.absolute_path),
                "message": e.message,
                "schema_path": ".".join(str(p) for p in e.schema_path)
            }],
            warnings=[]
        )
```

### 5.4 L3 结构校验

| Agent | 结构校验规则 |
|-------|-------------|
| Backend Agent | 实现类的方法集合 ⊇ ABC接口的方法集合；每个方法含@audit_log装饰器；路由注册完整 |
| Frontend Agent | Vue组件含template+script+style；API端点覆盖api_def.json中该模块的所有接口 |
| DB Agent | SQL包含db_model.json中所有表的CREATE TABLE；外键引用的表已定义 |
| API Agent | api_def.json包含design.json中所有模块的接口；每个接口有path+method+params+response |
| Validate Agent | validation_report.json包含所有已检查文件的条目；每个issue有severity+location |

### 5.5 校验结果与修复Prompt的衔接

校验失败时，校验结果直接构造修复约束变量：

```python
def build_fix_constraints(validation_result: ValidationResult, output_type: str) -> dict:
    """
    从校验结果构造修复Prompt的约束变量

    Returns:
        {
            "err_error_code": "JSON_FORMAT_ERROR",
            "err_error_message": "api_def.json缺少required字段: modules[0].interfaces[0].path",
            "err_fix_suggestion": "每个接口定义必须包含path字段",
            "task_extra_constraints": [
                "确保每个接口都有path字段",
                "确保每个接口都有methods数组"
            ]
        }
    """
```

---

## 6. Tail段填充规则

### 6.1 Tail段的约束填充优先级

Prompt-Tail段的输出约束按5类约束的优先级排列：

```
{{@segment:TAIL}}
=== 输出约束 ===                         ← 格式约束（优先级1）
- 输出格式：```{{task_output_language}}:{{task_target_file}}

{{#task_structure_constraints}}         ← 结构约束（优先级2）
=== 结构约束 ===
{{task_structure_constraints}}
{{/task_structure_constraints}}

{{#task_content_constraints}}           ← 内容约束（优先级3）
=== 内容约束 ===
{{task_content_constraints}}
{{/task_content_constraints}}

{{#task_alignment_constraints}}         ← 对齐约束（优先级4）
=== 对齐约束 ===
{{task_alignment_constraints}}
{{/task_alignment_constraints}}

=== 禁止项 ===                           ← 禁止项（优先级5）
- 不要重复导入已在依赖文件中定义的类
- 不要生成非{{task_target_file}}的代码
- 不要修改依赖文件中的任何内容
{{#task_prohibitions}}
- {{.}}
{{/task_prohibitions}}
{{/segment:TAIL}}
```

### 6.2 约束变量的来源

| 变量 | 来源 | 生成时机 |
|------|------|---------|
| `task_output_language` | Agent配置（2.2节映射表） | Agent初始化 |
| `task_target_file` | 当前生成目标 | 每次Prompt组装 |
| `task_structure_constraints` | Agent类型+目标文件类型 | 每次Prompt组装 |
| `task_content_constraints` | decision_summary.json | 读取决策摘要 |
| `task_alignment_constraints` | api_def.json / db_model.json | 读取契约产物 |
| `task_prohibitions` | Agent类型（3.6节映射表） | Agent初始化 |
| `task_extra_constraints` | 修复循环追加 | L2修复时 |

### 6.3 约束变量的填充逻辑

#### 6.3.1 task_structure_constraints 填充

```python
def fill_structure_constraints(agent_type: str, target_file: str, context: dict) -> str:
    """填充结构约束"""
    constraints = []

    if agent_type == "backend":
        # 从context中获取当前文件对应的ABC接口名
        interface_name = context.get("current_interface_name")
        if interface_name:
            constraints.append(f"- 必须继承 {interface_name} 的所有抽象方法")
        constraints.append("- 每个方法添加@audit_log装饰器")
        constraints.append("- 使用ServiceException处理业务异常")

    elif agent_type == "frontend":
        constraints.append("- 组件必须包含 <template> + <script setup> + <style scoped> 三段")
        constraints.append("- 组件命名使用PascalCase")

    elif agent_type == "db":
        constraints.append("- 每张表独立的CREATE TABLE语句")
        constraints.append("- 必须包含所有外键约束和索引")

    elif agent_type == "api":
        constraints.append("- 严格遵循D1 api_def.json Schema")
        constraints.append("- 每个接口必须包含 path, http_method, params, response")

    elif agent_type == "prd":
        constraints.append("- 按PRD模板5章节结构输出")

    elif agent_type == "design":
        constraints.append("- 必须包含架构模式和模块划分")
        constraints.append("- 结构化JSON严格遵循D1 design.json Schema")

    elif agent_type == "devops":
        constraints.append("- 必须输出7个配置文件")
        constraints.append("- Dockerfile必须指定基础镜像和工作目录")

    return "\n".join(constraints)
```

#### 6.3.2 task_content_constraints 填充

```python
def fill_content_constraints(decision_summary: dict, agent_type: str) -> str:
    """从决策摘要填充内容约束"""
    constraints = []
    decisions = decision_summary.get("decisions", {})

    # 所有代码生成Agent共有的内容约束
    if agent_type in ("backend", "frontend"):
        if "response_format" in decisions:
            fmt = decisions["response_format"]["structure"]
            constraints.append(f"- 响应格式：{fmt}")

        if "pagination" in decisions:
            pag = decisions["pagination"]
            constraints.append(f"- 分页参数：{pag.get('params', {})}")
            constraints.append(f"- 分页响应：{pag.get('response', {})}")

    if agent_type == "backend":
        if "exception_handling" in decisions:
            exc = decisions["exception_handling"]
            constraints.append(f"- 异常类：{exc.get('class')}，用法：{exc.get('usage')}")

        if "audit" in decisions:
            audit = decisions["audit"]
            constraints.append(f"- 审计装饰器：{audit.get('decorator')}")

        if "device_identification" in decisions:
            dev = decisions["device_identification"]
            constraints.append(f"- 设备识别：{dev.get('function')}")

    if agent_type == "frontend":
        if "ui_framework" in decisions:
            constraints.append(f"- UI框架：{decisions['ui_framework'].get('name', 'Element Plus')}")
        if "state_management" in decisions:
            constraints.append(f"- 状态管理：{decisions['state_management'].get('name', 'Pinia')}")

    return "\n".join(constraints)
```

#### 6.3.3 task_alignment_constraints 填充

```python
def fill_alignment_constraints(agent_type: str, context: dict) -> str:
    """填充对齐约束"""
    constraints = []

    if agent_type == "frontend":
        constraints.append("- API调用路径必须与api_def.json完全一致")
        constraints.append("- 请求参数名和类型必须与api_def.json定义一致")
        constraints.append("- 响应数据结构必须与api_def.json定义一致")
        constraints.append("- 不要硬编码API路径，从api配置中引用")

    elif agent_type == "backend":
        constraints.append("- 路由路径和HTTP方法必须与api_def.json完全一致")
        constraints.append("- 请求参数和响应格式必须与api_def.json定义一致")
        constraints.append("- 数据库操作必须与db_model.json表结构一致")

    elif agent_type == "api":
        constraints.append("- 接口中的数据模型引用必须与db_model.json一致")

    return "\n".join(constraints)
```

#### 6.3.4 task_extra_constraints 追加（修复场景）

修复Prompt中，`task_extra_constraints` 追加修复专用约束：

```python
def build_fix_extra_constraints(validation_errors: list, fix_round: int) -> list:
    """从校验错误构造修复额外约束"""
    constraints = []

    for error in validation_errors:
        if error["type"] == "missing_method":
            constraints.append(f"- 补充实现缺失的方法：{error['method_name']}")
        elif error["type"] == "missing_decorator":
            constraints.append(f"- 为方法 {error['method_name']} 添加 {error['decorator']} 装饰器")
        elif error["type"] == "schema_violation":
            constraints.append(f"- 修正字段：{error['field']}，期望：{error['expected']}")
        elif error["type"] == "contract_mismatch":
            constraints.append(f"- 修正API路径/参数与api_def.json的不一致：{error['detail']}")

    if fix_round >= 2:
        constraints.append("- 仅修复上述问题，不要重写整个文件")

    if fix_round >= 3:
        constraints.append("⚠️ 这是最后一次自动修复尝试")

    return constraints
```

---

## 7. 输出Token预算

### 7.1 各Agent输出Token预算

| Agent | 输出类型 | 预期输出Token | 最大输出Token | 说明 |
|-------|---------|-------------|-------------|------|
| PRD Agent | 文档 | 3000-5000 | 8000 | 5章节PRD |
| Design Agent | 文档+JSON | 4000-6000 | 10000 | 架构+模块+JSON |
| DB Agent | SQL+JSON+文档 | 5000-8000 | 15000 | 表结构+索引+ER |
| API Agent | JSON+文档 | 4000-6000 | 12000 | 全模块接口定义 |
| Backend Agent | 代码 | 1500-3000 | 6000 | 单文件（A1防截断） |
| Frontend Agent | 代码 | 1500-3000 | 6000 | 单文件 |
| Validate Agent | JSON | 1000-2000 | 4000 | 检查报告 |
| DevOps Agent | 配置 | 3000-5000 | 10000 | 7个配置文件 |

### 7.2 输出截断检测

当LLM输出被截断时（输出Token达到max_tokens限制），需要检测并处理：

```python
def detect_truncation(llm_output: str, max_tokens: int, expected_end_markers: list) -> dict:
    """
    检测LLM输出是否被截断

    Args:
        llm_output: LLM原始输出
        max_tokens: 最大输出Token数
        expected_end_markers: 期望的结束标记，如 ["```", "}", "</template>"]

    Returns:
        {
            "truncated": bool,
            "confidence": float,       # 0.0-1.0
            "missing_markers": list,   # 缺失的结束标记
            "suggestion": str          # 处理建议
        }
    """
    missing = []
    for marker in expected_end_markers:
        if marker not in llm_output:
            missing.append(marker)

    truncated = len(missing) > 0
    confidence = len(missing) / len(expected_end_markers) if expected_end_markers else 0

    suggestion = "regenerate_with_higher_max_tokens" if truncated else "none"

    return {
        "truncated": truncated,
        "confidence": confidence,
        "missing_markers": missing,
        "suggestion": suggestion
    }
```

### 7.3 截断处理策略

| 截断类型 | 处理策略 | 错误码 |
|---------|---------|--------|
| 代码截断（缺```闭合） | L2修复：追加"上次输出被截断，请从截断处继续" | `CODE_GENERATION_INCOMPLETE` |
| JSON截断（缺}`闭合） | L2修复：追加截断位置，要求完整重输 | `JSON_FORMAT_ERROR` |
| SQL截断（缺;闭合） | L2修复：追加截断位置 | `LLM_FORMAT_ERROR` |
| 文档截断（缺章节） | L2修复：列出缺失章节 | `CODE_GENERATION_INCOMPLETE` |

**防截断最佳实践**：

| 策略 | 说明 | 适用Agent |
|------|------|----------|
| 单文件生成 | 每次Prompt只生成1个文件 | Backend、Frontend |
| 拆分长文件 | 超长文件拆为函数段生成 | Backend（Service类） |
| 提高max_tokens | 对已知输出长的场景提高限制 | DB（SQL）、API（JSON） |
| 续写提示 | 截断后追加续写约束 | 所有Agent |

---

## 8. 输出约束与修复循环的闭环

### 8.1 闭环流程

```
Prompt组装（含输出约束）
     │
     ▼
LLM生成输出
     │
     ▼
输出解析 ──── 解析失败 ──→ 构造修复约束 → 修复Prompt（fix_模板）
     │                                                    │
     │ 解析成功                                            │ LLM重新生成
     ▼                                                    │
L1格式校验 ── 失败 ──→ 构造修复约束 ──────────────────────→│
     │                                                    │
     │ 通过                                               │
     ▼                                                    │
L2 Schema校验 ── 失败 ──→ 构造修复约束 ───────────────────→│
     │                                                    │
     │ 通过                                               │
     ▼                                                    │
L3结构校验 ── 失败 ──→ 构造修复约束 ───────────────────────→│
     │                                                    │
     │ 通过                                               │
     ▼                                                    │
交付下游                                                    │
                                                      MAX_FIX_ROUNDS=3
                                                           │
                                                           ▼
                                                     升级为L3回退
```

### 8.2 修复约束的递进强化

每轮修复循环中，输出约束逐步收紧：

| 修复轮次 | 约束强化策略 |
|---------|-------------|
| 第1轮 | 追加具体错误描述 + 修复建议 |
| 第2轮 | 追加"仅修复错误，不要重写整个文件" + 历史修复摘要 |
| 第3轮 | 追加熔断预警 + 极简约束（只修复，不改其他） |

### 8.3 修复约束的Token预算

修复约束注入ERROR_CONTEXT段，Token预算受D4第15章限制：

| Token档位 | 修复约束Token上限 | 说明 |
|----------|-----------------|------|
| 紧凑 | ≤200 | 仅错误码+位置+单行建议 |
| 标准 | ≤500 | 错误详情+修复建议+约束 |
| 宽裕 | ≤1000 | 完整错误上下文+历史修复+约束 |

---

## 9. 输出约束配置

### 9.1 Agent级输出约束配置

每个Agent可配置输出约束的松紧度：

```json
{
  "output_constraint_config": {
    "backend_agent": {
      "strictness": "strict",
      "format_check": true,
      "schema_check": true,
      "structure_check": true,
      "alignment_check": true,
      "max_output_tokens": 6000,
      "truncation_retry": true
    },
    "prd_agent": {
      "strictness": "moderate",
      "format_check": true,
      "schema_check": false,
      "structure_check": true,
      "alignment_check": false,
      "max_output_tokens": 8000,
      "truncation_retry": true
    },
    "devops_agent": {
      "strictness": "moderate",
      "format_check": true,
      "schema_check": false,
      "structure_check": true,
      "alignment_check": false,
      "max_output_tokens": 10000,
      "truncation_retry": true
    }
  }
}
```

### 9.2 strictness级别

| 级别 | 格式校验 | Schema校验 | 结构校验 | 对齐校验 | 适用Agent |
|------|---------|-----------|---------|---------|----------|
| `strict` | ✅ | ✅ | ✅ | ✅ | Backend、Frontend、API |
| `moderate` | ✅ | 条件性 | ✅ | 条件性 | PRD、Design、DB、DevOps |
| `lenient` | ✅ | ❌ | ❌ | ❌ | 仅调试模式 |

### 9.3 约束降级（A5 D1质量降级）

当A5触发D1质量降级时，输出约束可降级：

| 降级级别 | 约束降级行为 |
|---------|-------------|
| D1-0（正常） | 所有约束完整注入 |
| D1-1 | 移除对齐约束（不再校验与api_def.json的一致性） |
| D1-2 | 移除结构约束（仅保留格式约束和禁止项） |
| D1-3 | 仅保留格式约束（最低保障） |

---

## 10. 输出约束审计

### 10.1 约束执行记录

每次输出约束校验后记录：

```json
{
  "constraint_check_id": "uuid",
  "timestamp": "2026-04-30T10:30:00+08:00",
  "pipeline_id": "uuid",
  "agent": "backend_agent",
  "template_id": "gen_backend_file",
  "target_file": "app/services/user.py",
  "checks": [
    {
      "layer": "L1_format",
      "result": "passed",
      "details": null
    },
    {
      "layer": "L2_schema",
      "result": "skipped",
      "details": "not_json_output"
    },
    {
      "layer": "L3_structure",
      "result": "failed",
      "details": {
        "missing_methods": ["delete_user"],
        "missing_decorators": ["update_user缺少@audit_log"]
      }
    }
  ],
  "overall_result": "failed",
  "fix_triggered": true,
  "fix_round": 1
}
```

### 10.2 约束命中率统计

管线完成后统计各约束类型的命中率：

```json
{
  "constraint_stats": {
    "total_checks": 45,
    "by_layer": {
      "L1_format": {"passed": 42, "failed": 3},
      "L2_schema": {"passed": 8, "failed": 2, "skipped": 35},
      "L3_structure": {"passed": 38, "failed": 7}
    },
    "by_constraint_type": {
      "format": {"triggered": 45, "failed": 3},
      "structure": {"triggered": 45, "failed": 7},
      "content": {"triggered": 30, "failed": 4},
      "alignment": {"triggered": 20, "failed": 2},
      "prohibition": {"triggered": 45, "failed": 1}
    },
    "fix_success_rate": 0.82,
    "avg_fix_rounds": 1.3
  }
}
```

---

## 11. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，定义输出格式声明、5类约束、解析器、3层校验、Tail填充规则、修复闭环 |
