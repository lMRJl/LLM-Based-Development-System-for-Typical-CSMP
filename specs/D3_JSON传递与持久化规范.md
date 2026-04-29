# D3 JSON传递与持久化规范

## 1. 总则

### 1.1 目标
定义管线中JSON产物的传递协议、读写时序、原子性保障、缓存策略、持久化层级、故障恢复机制，确保JSON数据在各Agent间流转可靠、持久安全、性能可接受。

### 1.2 定位

| 维度 | 说明 |
|------|------|
| **与A3的关系** | A3定义了通信拓扑和消息格式（路径引用而非内容传递）；D3定义路径引用背后的具体传递协议——何时读、如何写、读写冲突如何避免 |
| **与D1的关系** | D1定义了JSON产物的Schema；D3定义这些JSON在文件系统上的读写行为和持久化规则 |
| **与D2的关系** | D2定义了版本链管理和版本回滚；D3定义版本文件的存储格式、压缩策略、备份机制 |
| **与A5的关系** | A5定义了回退时的产物清理；D3定义清理时文件操作的原子性保障和故障恢复 |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **路径引用，内容不搬家** | Agent间只传递路径，不复制文件内容，避免数据冗余和一致性风险 |
| **写时隔离，读时共享** | 每个Agent只写自己的输出目录，共享产物只读 |
| **原子写入，杜绝半写** | 所有文件写入使用"临时文件→原子重命名"模式，确保下游不会读到不完整的文件 |
| **故障可恢复** | 任何写入操作可回滚，任何读取操作可重试 |
| **存储分级** | 热数据（当前版本）快速访问，冷数据（历史版本）压缩归档 |

---

## 2. 传递协议

### 2.1 传递流程总览

```
Orchestrator                                    Agent
    │                                             │
    │  1. dispatch(inputs={path1, path2, ...})    │
    │─────────────────────────────────────────────→│
    │                                             │
    │                                             2. 校验路径可达性
    │                                             3. 校验Schema版本
    │                                             4. 校验数据版本
    │                                             5. 加载JSON到内存
    │                                             6. 执行生成逻辑
    │                                             7. 原子写入产物
    │                                             8. 原子写入上下文
    │
    │  9. result(artifacts={path_a, path_b})      │
    │←─────────────────────────────────────────────│
    │                                             │
    10. 校验产物路径可达性
    11. 更新pipeline_state.json
    12. 通知下游Agent
```

### 2.2 输入校验协议

Agent收到dispatch消息后，在执行任何生成逻辑之前，必须完成以下校验：

| 序号 | 校验项 | 失败处理 | 错误码 |
|------|--------|---------|--------|
| 1 | 路径可达性 | 文件不存在 → 上报Orchestrator | `INPUT_PATH_NOT_FOUND` |
| 2 | 文件可读性 | 无读取权限 → 上报Orchestrator | `INPUT_READ_DENIED` |
| 3 | JSON解析 | 格式错误 → 上报Orchestrator | `INPUT_JSON_PARSE_ERROR` |
| 4 | Schema版本 | 版本不匹配 → 上报Orchestrator（D2 5.3节） | `VERSION_SCHEMA_MISMATCH` |
| 5 | 数据版本 | 低于最低要求 → 上报Orchestrator（D2 5.3节） | `VERSION_DATA_TOO_LOW` |
| 6 | Schema校验 | 字段缺失/类型错误 → 上报Orchestrator | `INPUT_SCHEMA_VIOLATION` |
| 7 | 业务完整性 | 关键业务字段为空 → 上报Orchestrator | `INPUT_BUSINESS_INCOMPLETE` |

**校验顺序**：路径→可读→解析→版本→Schema→业务。任一步失败立即停止，不继续后续校验。

### 2.3 输出确认协议

Agent完成生成后，在发送result消息之前，必须确认：

| 序号 | 确认项 | 失败处理 |
|------|--------|---------|
| 1 | 产物文件已原子写入完成 | 重试写入（A5文件级容错） |
| 2 | 产物文件可被Orchestrator读取 | 检查文件权限 |
| 3 | 产物JSON可被正确解析 | 文件级修复 |
| 4 | 产物Schema校验通过 | 文件级修复 |
| 5 | 上下文快照已保存 | 重试写入 |

### 2.4 传递模式分类

| 模式 | 触发场景 | 输入来源 | 输出目标 |
|------|---------|---------|---------|
| **标准传递** | 串行阶段（S1→S2→S3→S3'） | 上游Agent的artifacts/目录 | 当前Agent的artifacts/目录 |
| **契约传递** | S4并行阶段 | api_def.json（共享只读） | 各自的artifacts/目录 |
| **回退传递** | A5回退场景 | 回退目标版本（D2 versions/目录） | 当前Agent重新消费 |
| **断点续传** | A2断点续执行 | 已有产物+用户修改 | 继续未完成阶段 |

---

## 3. 文件读写操作规范

### 3.1 原子写入

所有JSON文件写入必须使用"临时文件→原子重命名"模式：

```
写入流程：
  1. 写入临时文件：{target_path}.tmp.{pid}.{timestamp}
  2. 调用 os.replace() / os.rename() 原子重命名
  3. 重命名成功 → 写入完成
  4. 重命名失败 → 临时文件残留，下次启动时清理

读取流程：
  1. 直接读取目标路径
  2. 不存在 → 检查是否有.tmp文件 → 有则表示上次写入中断，不消费
  3. 不存在且无.tmp → 文件未生成
```

**原子重命名的操作系统保证**：

| OS | 系统调用 | 原子性保证 |
|----|---------|-----------|
| Linux | `rename()` | 同一文件系统内原子操作 |
| Windows | `MoveFileEx()` with `MOVEFILE_REPLACE_EXISTING` | NTFS上原子操作 |
| macOS | `rename()` | 同一文件系统内原子操作 |

**Windows特殊处理**：

Windows上`os.replace()`在目标文件已存在时可能失败（文件被占用），需额外处理：

```
1. 尝试 os.replace(tmp_path, target_path)
2. 若失败（PermissionError/FileNotFoundError）：
   a. 重试1次（短暂延迟后）
   b. 仍失败 → 写入 {target_path}.new 标记新版本
   c. 通知Orchestrator文件更新需要手动确认
```

### 3.2 大文件读写

当JSON文件超过一定大小时，需要特殊处理：

| 文件大小 | 策略 | 适用产物 |
|---------|------|---------|
| < 100KB | 标准读写（整体加载） | 大多数产物 |
| 100KB - 1MB | 流式读取，按需解析 | pipeline_state.json（含大量错误记录时） |
| > 1MB | 分片存储+索引 | 不预期出现（上下文快照压缩后通常 < 500KB） |

**流式读取实现**：

```python
import json

def read_large_json(file_path: str, keys_of_interest: list[str] = None):
    """流式读取大型JSON，仅解析感兴趣的字段"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)  # 标准加载（Python json模块已优化）

    if keys_of_interest:
        return {k: data.get(k) for k in keys_of_interest if k in data}
    return data
```

> 注：Python标准库的`json.load()`已足够高效。真正的性能瓶颈在LLM调用而非文件IO，因此不需要引入ijson等流式解析库。

### 3.3 并发读写保护

A3已定义并行Agent写入不同目录（无写冲突），但存在以下并发场景需保护：

| 场景 | 保护机制 | 说明 |
|------|---------|------|
| 并行Agent同时读取api_def.json | 无需保护 | 只读，天然并发安全 |
| Agent写入产物的同时Orchestrator读取 | 原子写入保护 | Orchestrator只读到完整文件或旧文件 |
| Agent写入上下文快照的同时Orchestrator读取 | 原子写入保护 | 同上 |
| Orchestrator写入pipeline_state.json | 单写者 | 只有Orchestrator写入，无冲突 |
| 并行Agent各写独立decision_summary快照 | 目录隔离 | 各写各的快照文件，合并时串行 |

**关键不变量**：同一文件同一时刻最多一个写者。

---

## 4. 持久化层级

### 4.1 三级持久化

| 层级 | 存储位置 | 内容 | 生命周期 | 访问模式 |
|------|---------|------|---------|---------|
| **L1 运行时** | Agent进程内存 | 当前正在处理的JSON解析结果 | 单次LLM调用 | 随机读写 |
| **L2 工作区** | `output/{pipeline_id}/` | 当前管线所有产物和状态 | 管线执行期间 | 文件读写 |
| **L3 归档区** | `archive/{pipeline_id}/` | 压缩后的历史版本和Diff | 永久 | 归档后只读 |

### 4.2 L1→L2 同步规则

| 事件 | L1→L2同步动作 |
|------|-------------|
| Agent完成一个文件生成 | 原子写入产物到L2 |
| Agent完成一个原子步骤 | 原子写入上下文快照到L2 |
| Agent追加决策摘要 | 原子写入到L2 |
| Agent更新管线状态 | 原子写入pipeline_state.json到L2 |
| Agent完成全部任务 | L1内存释放，L2数据完整 |

**同步时机**：每个原子步骤完成后立即同步，不缓存到"步骤组"结束。

### 4.3 L2→L3 归档规则

| 时机 | 动作 |
|------|------|
| 管线状态=COMPLETED | 执行归档（D2第9节） |
| 管线状态=MANUAL_INTERVENTION且用户选择终止 | 执行部分归档（已完成阶段的产物） |
| 管线状态=FAILED | 保留L2数据30天，不归档（供问题诊断） |

**归档后L2的处理**：

| 场景 | L2处理 |
|------|--------|
| 管线成功完成 | L2保留7天（供用户下载产物），之后删除中间文件，仅保留artifacts/ |
| 管线部分完成 | L2保留直到用户确认不再需要 |
| 管线失败 | L2保留30天供问题诊断 |

### 4.4 故障恢复的数据保障

| 故障场景 | L1状态 | L2状态 | 恢复策略 |
|---------|--------|--------|---------|
| Agent进程崩溃 | 丢失 | 最后一次同步时的状态 | 从L2恢复，基于最近快照续执行 |
| Orchestrator进程崩溃 | 丢失 | 最后一次同步时的状态 | 从L2的pipeline_state.json恢复管线状态 |
| 机器断电 | 丢失 | OS页面缓存可能未刷盘 | 重启后校验L2文件完整性 |
| 磁盘故障 | 丢失 | 可能部分丢失 | 从L3归档区恢复（若已归档） |

**页面缓存保障**：

为确保断电后数据完整性，关键文件写入后调用`fsync`：

```python
import os

def atomic_write_json(file_path: str, data: dict):
    """原子写入JSON文件，确保落盘"""
    tmp_path = f"{file_path}.tmp.{os.getpid()}.{int(time.time())}"

    # 1. 写入临时文件
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())  # 确保数据落盘

    # 2. 原子重命名
    os.replace(tmp_path, file_path)

    # 3. 确保目录项落盘（重命名后）
    dir_fd = os.open(os.path.dirname(file_path), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
```

**fsync策略分级**：

| 文件类型 | fsync策略 | 原因 |
|---------|----------|------|
| pipeline_state.json | 每次写入fsync | 管线状态是最关键的恢复依据 |
| {agent}_context.json | 每次写入fsync | 快照是回滚恢复的依据 |
| decision_summary.json | 每次写入fsync | 决策数据难以重建 |
| 代码产物(.py/.vue) | 不强制fsync | 可重新生成，性能优先 |
| 产物JSON(design.json等) | 不强制fsync | 可重新生成，性能优先 |

---

## 5. 目录结构与文件布局

### 5.1 完整目录结构

在A3第4.1节的基础上，结合D2版本管理需求，定义完整的目录结构：

```
output/{pipeline_id}/
├── artifacts/                              # 当前生效产物（L2工作区）
│   ├── docs/                               # 文档产物
│   │   ├── prd.docx
│   │   ├── design.docx
│   │   ├── db.docx
│   │   └── api.docx
│   ├── database/                           # 数据库产物
│   │   ├── init.sql
│   │   └── db_model.json
│   ├── api/                                # 接口产物
│   │   └── api_def.json
│   ├── intermediate/                       # 中间产物
│   │   ├── design.json
│   │   └── routes.json
│   ├── backend/                            # 后端代码
│   │   └── ...
│   ├── frontend/                           # 前端代码
│   │   └── ...
│   └── devops/                             # 部署配置
│       └── ...
├── versions/                               # 版本链（D2第4.2节）
│   ├── design/
│   │   ├── design_dv1.json
│   │   └── design_dv2.json
│   ├── api_def/
│   │   └── api_def_dv1.json
│   ├── decision_summary/
│   │   ├── decision_summary_dv1.json
│   │   └── decision_summary_dv2.json
│   └── ...（每个可版本化产物一个子目录）
├── state/                                  # 管线状态
│   ├── pipeline_state.json                 # 管线整体状态
│   ├── decision_summary.json               # 全局决策摘要
│   ├── version_snapshots/                  # 版本快照（D2第10.2节）
│   │   ├── snapshot_S3'_completed.json
│   │   └── snapshot_S4_completed.json
│   └── agent_contexts/                     # Agent上下文（A4）
│       ├── backend_context_dv1.json
│       ├── backend_context_dv2.json
│       ├── frontend_context_dv1.json
│       └── ...
├── logs/                                   # 日志
│   ├── pipeline.log                        # 管线日志
│   ├── transfer.log                        # 传递日志（本规范定义）
│   └── agent_logs/
│       ├── backend_agent.log
│       └── ...
├── errors/                                 # 错误快照
│   ├── backend_error_001.json
│   └── ...
└── tmp/                                    # 临时文件（原子写入中间态）
    ├── api_def.json.tmp.12345.1746000000   # 写入中的临时文件
    └── ...
```

### 5.2 目录权限模型

| 目录 | Orchestrator | 产出Agent | 其他Agent | 说明 |
|------|-------------|-----------|----------|------|
| artifacts/ | 读写 | 读写自己的子目录 | 只读 | Agent只写自己产出的子目录 |
| versions/ | 读写 | 读写自己的产物子目录 | 只读 | 版本链由产出Agent追加 |
| state/ | 读写 | 读写自己的上下文文件 | 只读decision_summary | pipeline_state仅Orchestrator写入 |
| logs/ | 读写 | 写自己的日志文件 | 只读 | 日志隔离 |
| errors/ | 读写 | 写自己的错误文件 | 只读 | 错误隔离 |
| tmp/ | 读写清理 | 写自己的临时文件 | 不可见 | 临时文件仅写入者可见 |

### 5.3 临时文件清理

```
启动时清理：
  1. 扫描tmp/目录
  2. 对于每个.tmp文件：
     a. 检查关联的目标文件是否存在且完整
     b. 目标文件完整 → 删除.tmp文件（上次写入成功但.tmp未清理）
     c. 目标文件不存在 → 删除.tmp文件（上次写入失败，丢弃不完整数据）
  3. 记录清理日志

运行时清理：
  每次原子写入成功后，.tmp文件已被os.replace()替换
  若os.replace()成功，.tmp文件已不存在
  若os.replace()失败，.tmp文件残留，下次启动时清理
```

---

## 6. 传递日志

### 6.1 日志格式

每次JSON产物的传递（读或写）记录到传递日志：

```
output/{pipeline_id}/logs/transfer.log
```

**日志条目格式**：

```json
{
  "timestamp": "2026-04-30T00:15:00.000+08:00",
  "operation": "read|write|rollback|archive",
  "agent": "backend_agent",
  "artifact": "api_def.json",
  "path": "output/{pid}/artifacts/api/api_def.json",
  "schema_version": 1,
  "data_version": 2,
  "result": "success|failed|not_found|version_mismatch|schema_violation",
  "duration_ms": 12,
  "detail": null
}
```

### 6.2 日志记录时机

| 操作 | 记录时机 | 记录者 |
|------|---------|--------|
| Agent读取输入产物 | 读取完成后 | Agent |
| Agent写入输出产物 | 写入完成后 | Agent |
| Orchestrator读取管线状态 | 读取完成后 | Orchestrator |
| Orchestrator更新管线状态 | 写入完成后 | Orchestrator |
| Orchestrator回滚产物版本 | 回滚完成后 | Orchestrator |
| 归档操作 | 归档完成后 | Orchestrator |

### 6.3 日志用途

| 用途 | 说明 |
|------|------|
| 问题诊断 | 定位"哪个Agent在什么时间读了什么版本的什么产物" |
| 性能分析 | 统计文件IO耗时，识别性能瓶颈 |
| 版本追溯 | 结合D2版本链，重建产物传递时间线 |
| 审计 | 确认数据流转路径符合预期 |

---

## 7. 传递中的数据校验

### 7.1 校验层级

JSON产物在传递过程中经过多层校验：

```
写入方（产出Agent）                    读取方（消费Agent）
  │                                       │
  ├─ 1. 业务逻辑校验                      │
  │    （生成内容是否合理）                │
  │                                       │
  ├─ 2. Schema校验                        ├─ 4. Schema校验
  │    （字段/类型/约束是否满足D1）        │    （同左）
  │                                       │
  ├─ 3. 完整性校验                        ├─ 5. 完整性校验
  │    （必填字段是否存在）                │    （同左）
  │                                       │
  ├─ 原子写入到文件                       │
  │                                       ├─ 6. 版本校验
  │                                       │    （D2版本约束检查）
  │                                       │
  │                                       ├─ 7. 业务校验
  │                                       │    （关键字段值是否合理）
  │                                       │
  │                                       └─ 8. 加载到内存
```

**设计意图**：写入方和读取方都做校验，双重保障。写入方校验确保产出正确，读取方校验确保消费正确（防御性编程）。

### 7.2 Schema校验实现

使用D1定义的JSON Schema进行校验：

```python
import json
from jsonschema import validate, ValidationError

def validate_artifact(data: dict, schema_path: str) -> tuple[bool, str]:
    """校验JSON产物是否符合D1定义的Schema"""
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema = json.load(f)

    try:
        validate(instance=data, schema=schema)
        return True, ""
    except ValidationError as e:
        return False, f"Schema校验失败: {e.message} (path: {'/'.join(str(p) for p in e.absolute_path)})"
```

**Schema文件存储位置**：

```
schemas/
├── design_schema.json
├── db_model_schema.json
├── api_def_schema.json
├── routes_schema.json
├── decision_summary_schema.json
├── pipeline_state_schema.json
├── backend_context_schema.json
├── frontend_context_schema.json
├── validate_context_schema.json
├── validation_report_schema.json
└── error_context_schema.json
```

### 7.3 校验失败处理

| 校验层 | 失败处理 | 错误码 |
|--------|---------|--------|
| Schema校验 | 产出Agent：文件级修复；消费Agent：上报Orchestrator | `INPUT_SCHEMA_VIOLATION` / `OUTPUT_SCHEMA_VIOLATION` |
| 完整性校验 | 同上 | `INPUT_BUSINESS_INCOMPLETE` / `OUTPUT_BUSINESS_INCOMPLETE` |
| 版本校验 | 上报Orchestrator（D2 5.3节） | `VERSION_SCHEMA_MISMATCH` / `VERSION_DATA_TOO_LOW` |
| 业务校验 | 产出Agent：文件级修复；消费Agent：上报Orchestrator | `INPUT_BUSINESS_INVALID` |

---

## 8. 缓存策略

### 8.1 缓存层级

| 缓存层 | 位置 | 生命周期 | 命中场景 |
|--------|------|---------|---------|
| **内存缓存** | Agent进程内 | 单次dispatch任务 | 同一dispatch内多次读取同一产物 |
| **文件缓存** | L2工作区 | 管线执行期间 | 跨dispatch读取（断点续执行时） |
| **Schema缓存** | 进程内 | 进程生命周期 | Schema文件不变，只加载一次 |

### 8.2 内存缓存规则

```python
class ArtifactCache:
    """Agent进程内的产物缓存"""

    def __init__(self):
        self._cache: dict[str, tuple[dict, float]] = {}  # path → (data, mtime)

    def get(self, file_path: str) -> dict | None:
        """获取缓存的产物，若文件已更新则失效"""
        if file_path not in self._cache:
            return None

        cached_data, cached_mtime = self._cache[file_path]
        current_mtime = os.path.getmtime(file_path)

        if current_mtime > cached_mtime:
            # 文件已被更新，缓存失效
            del self._cache[file_path]
            return None

        return cached_data

    def put(self, file_path: str, data: dict):
        """缓存产物"""
        mtime = os.path.getmtime(file_path)
        self._cache[file_path] = (data, mtime)
```

### 8.3 缓存失效场景

| 场景 | 失效机制 | 说明 |
|------|---------|------|
| 上游产物被回滚/更新 | mtime变化 → 缓存失效 | 读取时检查mtime |
| Agent重新dispatch | 清空所有缓存 | 新任务不继承旧缓存 |
| api_def.json版本变更 | mtime变化 → 缓存失效 | S4并行期间的关键场景 |

### 8.4 缓存容量控制

| 产物类型 | 缓存策略 | 原因 |
|---------|---------|------|
| 小型产物（< 50KB） | 全量缓存 | design.json, api_def.json等Schema类产物 |
| 中型产物（50-500KB） | 按需缓存 | decision_summary.json, pipeline_state.json |
| 大型产物（> 500KB） | 不缓存，按需读取 | 上下文快照（可从版本链按需加载） |

---

## 9. 并行阶段的传递保障

### 9.1 契约只读保障

S4并行阶段，api_def.json是共享契约，必须保证只读：

```
保障机制：
  1. Orchestrator在dispatch消息中标记api_def.json为只读
  2. Agent写入时校验：不得写入只读产物的路径
  3. 若Agent需要修改api_def.json → 必须通过A3 error消息上报
     → Orchestrator决策是否回退到API Agent（A5 4.3节）
```

### 9.2 决策摘要并行写入

S4并行阶段，Backend和Frontend各自维护独立的决策摘要快照：

```
state/agent_contexts/
├── decision_summary_backend_dv4.json    # Backend的追加
├── decision_summary_frontend_dv4.json   # Frontend的追加
└── decision_summary.json                # S3'完成时的基线版本（只读）

写入规则：
  - Backend只写decision_summary_backend_dv4.json
  - Frontend只写decision_summary_frontend_dv4.json
  - 两者都不直接修改decision_summary.json
  - Orchestrator在S4完成后合并（D2 6.2节）
```

### 9.3 上下文快照并行写入

Backend和Frontend各写自己的上下文快照，互不干扰：

```
state/agent_contexts/
├── backend_context_dv1.json       # Backend写入
├── backend_context_dv2.json       # Backend写入
├── frontend_context_dv1.json      # Frontend写入
└── frontend_context_dv2.json      # Frontend写入
```

### 9.4 并行阶段的文件可见性

| 文件 | Backend可见 | Frontend可见 | 说明 |
|------|------------|-------------|------|
| api_def.json | 只读 | 只读 | 共享契约 |
| design.json | 只读 | 只读 | 共享上游产物 |
| db_model.json | 只读 | 只读 | 共享上游产物 |
| decision_summary.json | 只读 | 只读 | 基线版本 |
| backend_context_*.json | 读写 | 不可见 | 独有 |
| frontend_context_*.json | 不可见 | 读写 | 独有 |
| artifacts/backend/ | 读写 | 不可见 | 独有 |
| artifacts/frontend/ | 不可见 | 读写 | 独有 |
| routes.json | 读写 | 不可见 | Backend独有 |

---

## 10. 持久化故障恢复

### 10.1 故障场景与恢复策略

| 故障场景 | 影响 | 检测方式 | 恢复策略 |
|---------|------|---------|---------|
| 文件写入中途崩溃 | .tmp残留，目标文件为旧版本 | 启动时扫描tmp/目录 | 删除.tmp，使用旧版本 |
| 文件写入后fsync前断电 | 目标文件可能为空或不完整 | 读取后JSON解析失败 | 从版本链恢复上一版本 |
| 磁盘空间不足 | 写入失败 | write()抛出OSError | 清理归档数据/通知用户 |
| 文件权限错误 | 读写失败 | open()抛出PermissionError | 修正权限/通知用户 |
| 文件被其他进程锁定 | Windows上替换失败 | os.replace()抛出异常 | 重试/使用.new后缀 |

### 10.2 文件完整性校验

关键文件在读取时进行完整性校验：

```python
def read_artifact_safe(file_path: str) -> tuple[dict | None, str]:
    """安全读取产物文件，含完整性校验"""
    # 1. 文件存在性
    if not os.path.exists(file_path):
        return None, "FILE_NOT_FOUND"

    # 2. JSON解析
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        # 3. 解析失败 → 从版本链恢复
        return recover_from_version_chain(file_path), f"JSON_PARSE_ERROR: {e}"

    # 4. 版本字段存在性
    if "version" not in data:
        return None, "MISSING_VERSION_FIELD"

    return data, "OK"

def recover_from_version_chain(file_path: str) -> dict | None:
    """从版本链恢复上一个有效版本"""
    artifact_name = os.path.basename(file_path).replace('.json', '')
    versions_dir = os.path.join(
        os.path.dirname(os.path.dirname(file_path)),
        "versions", artifact_name
    )

    if not os.path.exists(versions_dir):
        return None

    # 从最新版本往前找第一个有效文件
    version_files = sorted(
        [f for f in os.listdir(versions_dir) if f.endswith('.json')],
        reverse=True
    )

    for vf in version_files:
        try:
            with open(os.path.join(versions_dir, vf), 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not data.get("_deprecated", False):
                return data
        except (json.JSONDecodeError, IOError):
            continue

    return None
```

### 10.3 管线状态恢复

Orchestrator崩溃后，从pipeline_state.json恢复管线状态：

```
恢复流程：
  1. 读取 output/{pipeline_id}/state/pipeline_state.json
  2. 校验JSON完整性
  3. 解析当前阶段和状态
  4. 检查各Agent上下文快照的最新版本
  5. 确定恢复点：
     a. pipeline_state.status = RUNNING
        → 检查current_stage对应的Agent是否有完成的上下文快照
        → 有 → 从快照恢复，继续执行
        → 无 → 从当前阶段重新dispatch
     b. pipeline_state.status = COMPLETED / FAILED
        → 不需要恢复
     c. pipeline_state.status = ROLLBACK
        → 检查回退是否已完成
        → 已完成 → 重新dispatch目标Agent
        → 未完成 → 重新执行回退流程
  6. 更新pipeline_state为RECOVERING
  7. 恢复完成后更新为原状态
```

### 10.4 数据备份

关键文件在修改前进行备份：

| 文件 | 备份策略 | 备份位置 |
|------|---------|---------|
| pipeline_state.json | 每次更新前备份 | `state/pipeline_state.json.bak` |
| decision_summary.json | 合并前备份 | `state/decision_summary.json.bak` |
| api_def.json | 回退修改前备份 | `versions/api_def/api_def_dv{N}_pre_rollback.json` |

**备份保留策略**：

| 备份类型 | 保留数量 | 清理时机 |
|---------|---------|---------|
| pipeline_state.json.bak | 1份（始终保留最近一份） | 下次备份时覆盖 |
| decision_summary.json.bak | 1份 | 下次合并时覆盖 |
| 回退前备份 | 永久保留（标记为版本链一部分） | 归档时统一处理 |

---

## 11. 性能优化

### 11.1 读写性能目标

| 操作 | 目标延迟 | 说明 |
|------|---------|------|
| 读取小型JSON（< 50KB） | < 10ms | 包含文件IO+JSON解析 |
| 读取中型JSON（50-500KB） | < 50ms | 包含文件IO+JSON解析 |
| 写入JSON（含fsync） | < 100ms | 包含序列化+写入+fsync |
| 写入JSON（不含fsync） | < 20ms | 代码产物类，可重新生成 |
| Schema校验 | < 30ms | jsonschema库校验 |

### 11.2 优化策略

| 策略 | 适用场景 | 效果 |
|------|---------|------|
| 内存缓存 | 同一产物多次读取 | 避免重复IO和解析 |
| 按需加载 | 仅读取需要的字段 | 减少内存占用 |
| 异步fsync | 代码产物写入 | 写入不阻塞（由后台线程fsync） |
| 批量版本快照 | 多个产物同时版本变更 | 减少文件IO次数 |
| 压缩存储 | 归档版本链 | 减少70-90%存储空间 |

### 11.3 存储空间预估

| 产物 | 单次大小 | 预期版本数 | 总空间 |
|------|---------|-----------|--------|
| design.json | ~3KB | 1-2 | ~6KB |
| db_model.json | ~8KB | 1-2 | ~16KB |
| api_def.json | ~15KB | 1-3 | ~45KB |
| routes.json | ~5KB | 1-2 | ~10KB |
| decision_summary.json | ~4KB | 3-5 | ~20KB |
| pipeline_state.json | ~6KB | 10-30 | ~180KB |
| {agent}_context.json | ~3KB | 5-15/Agent | ~90KB |
| validation_report.json | ~8KB | 1-2 | ~16KB |
| error_context.json | ~2KB | 0-5 | ~10KB |
| **总计（含版本链）** | — | — | **~400KB** |
| **归档后（Diff压缩）** | — | — | **~100KB** |

存储空间不是瓶颈，不需要过度优化。

---

## 12. 传递流程场景详解

### 12.1 标准串行传递（S1→S2→S3→S3'）

```
PRD Agent完成
  │
  ├─ 写入 artifacts/docs/prd.docx
  ├─ 写入 state/decision_summary.json（追加需求约束）
  ├─ 发送 result → Orchestrator
  │
  ▼
Orchestrator
  │
  ├─ 更新 pipeline_state.json（stage_status.prd=completed）
  ├─ 读取 decision_summary.json 确认版本
  ├─ 组装 dispatch 消息（inputs: prd.docx路径）
  ├─ 发送 dispatch → Design Agent
  │
  ▼
Design Agent
  │
  ├─ 校验 prd.docx 路径可达
  ├─ 加载 prd.docx 到内存
  ├─ 执行设计逻辑
  ├─ 原子写入 artifacts/intermediate/design.json
  ├─ 原子写入 artifacts/docs/design.docx
  ├─ 原子追加 state/decision_summary.json（架构决策+技术选型）
  ├─ 发送 result → Orchestrator
  │
  ▼
...（S3, S3' 类似）
```

### 12.2 契约驱动并行传递（S4）

```
API Agent完成
  │
  ├─ 写入 artifacts/api/api_def.json（_data_version=1）
  ├─ 原子追加 state/decision_summary.json
  ├─ 发送 result → Orchestrator
  │
  ▼
Orchestrator
  │
  ├─ 更新 pipeline_state.json
  ├─ 记录版本快照：S3'_completed
  ├─ 组装两个 dispatch 消息：
  │   → Backend: inputs={design, db_model, api_def, decision_summary}
  │   → Frontend: inputs={design, api_def, decision_summary}
  ├─ 同时发送两个 dispatch
  │
  ▼                                           ▼
Backend Agent                            Frontend Agent
  │                                         │
  ├─ 校验3个输入路径                          ├─ 校验3个输入路径
  ├─ 缓存api_def.json(只读)                  ├─ 缓存api_def.json(只读)
  ├─ 逐文件生成后端代码                        ├─ 逐文件生成前端代码
  ├─ 每文件完成后：                            ├─ 每文件完成后：
  │   ├─ 原子写入代码文件                      │   ├─ 原子写入代码文件
  │   └─ 原子写入上下文快照                    │   └─ 原子写入上下文快照
  ├─ 全部完成后写入routes.json                 ├─ 全部完成后写入独立决策快照
  ├─ 写入独立决策快照                          ├─ 发送 result → Orchestrator
  ├─ 发送 result → Orchestrator               │
  │                                            │
  ▼                                            ▼
Orchestrator（两者都完成后）
  │
  ├─ 合并决策摘要快照
  ├─ 更新 pipeline_state.json
  ├─ 记录版本快照：S4_completed
  └─ 调度 Validate Agent
```

### 12.3 回退场景传递（A5 L3回退）

```
Backend Agent上报L3回退到API Agent
  │
  ▼
Orchestrator
  │
  ├─ 1. 评估回退影响（A5 4.3节）
  ├─ 2. 执行版本回滚（D2 4.5节）
  │     ├─ api_def.json回滚到Backend消费前的版本
  │     └─ 标记后续版本为"回退废弃"
  ├─ 3. 执行产物清理（A5 5.3节）
  │     ├─ 删除Backend产物
  │     └─ 回滚决策摘要
  ├─ 4. 组装 rollback 消息 → API Agent
  │     ├─ original_inputs: 原始输入路径
  │     ├─ additional_context: 回退原因
  │     └─ affected_downstream: ["backend", "frontend"]
  ├─ 5. 发送 rollback → API Agent
  │
  ▼
API Agent（重新执行）
  │
  ├─ 读取原始输入（design.json, db_model.json）
  ├─ 读取回退上下文
  ├─ 重新产出 api_def.json（_data_version递增）
  ├─ 原子写入新版本
  └─ 发送 result → Orchestrator
      → Orchestrator重新调度Backend/Frontend
```

---

## 13. 编码与字符集

### 13.1 编码规则

| 维度 | 规则 |
|------|------|
| 文件编码 | UTF-8（无BOM） |
| JSON输出 | `ensure_ascii=False`，保留中文原文 |
| JSON缩进 | 2空格缩进（人类可读） |
| 换行符 | `\n`（LF），不使用`\r\n`（CRLF） |
| 行尾 | 最后一行有换行符 |

### 13.2 路径编码

| 场景 | 规则 |
|------|------|
| 传递路径中的中文 | pipeline_id使用UUID，产物路径不含中文 |
| Windows路径分隔符 | 消息中统一使用`/`，Agent内部转换为`\\` |
| 路径空格 | 产物目录路径不含空格 |

### 13.3 JSON序列化一致性

同一JSON内容多次序列化应产生相同输出（确定性序列化）：

```python
def deterministic_json_dump(data: dict, file_path: str):
    """确定性JSON序列化，确保相同数据产生相同文件"""
    json_str = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,       # key排序确保一致性
        separators=(',', ': ') # 标准分隔符
    )
    # 确保行尾一致
    json_str = json_str.replace('\r\n', '\n')
    if not json_str.endswith('\n'):
        json_str += '\n'

    with open(file_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json_str)
```

> `sort_keys=True`使同一JSON内容多次写入产生相同文件，便于diff比较和版本管理。

---

## 14. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-30 | 初始版本，定义传递协议、原子写入、持久化层级、目录结构、传递日志、数据校验、缓存策略、并行传递保障、故障恢复、性能优化、场景详解、编码规范 |
