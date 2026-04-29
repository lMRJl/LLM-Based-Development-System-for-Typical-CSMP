# LLM-Based Development System for Typical CSMP

基于 LLM 的典型网络安全设备后台管理系统（CSMP）自动化开发管线。

## 项目简介

本项目构建了一条 **自然语言 → 产品交付物** 的自动化管线，采用 **Agent + Skill + Advanced RAG** 架构。用户以自然语言描述需求，管线自动生成从需求文档到可部署代码的全套交付物。

**目标产品**：网络安全设备后台管理系统 Demo，功能域覆盖系统监控、网络状态监控、用户管理、日志管理、告警管理。

## 架构概览

```
自然语言输入
     │
     ▼
┌──────────┐    ┌──────────┐    ┌──────────┐
│   PRD    │───▶│  Design  │───▶│   DB     │
│  Agent   │    │  Agent   │    │  Agent   │
└──────────┘    └──────────┘    └──────────┘
                                      │
                                      ▼
                               ┌──────────┐
                               │   API    │
                               │  Agent   │
                               └──────────┘
                                      │
                          ┌───────────┼───────────┐
                          ▼                       ▼
                   ┌──────────┐            ┌──────────┐
                   │ Backend  │            │ Frontend │
                   │  Agent   │            │  Agent   │
                   └──────────┘            └──────────┘
                          │                       │
                          └───────────┬───────────┘
                                      ▼
                               ┌──────────┐
                               │ Validate │
                               │  Agent   │
                               └──────────┘
                                      │
                                      ▼
                               ┌──────────┐
                               │  DevOps  │
                               │  Agent   │
                               └──────────┘
```

- **9 个 Agent** 各司其职，Orchestrator 集中调度
- **4 个 Skill** 处理代码生成、检查修复、DevOps 配置
- **契约驱动并行**：`api_def.json` 为前后端唯一契约，前后端可并行开发
- **星型通信拓扑**：Agent 间不直接通信，经由 Orchestrator 中转

## 交付物链条

```
自然语言输入 → PRD文档 → 概要设计 → 数据库设计 → 接口定义 → 项目代码 → 部署配置
```

### 完整交付物清单

| 类别 | 交付物 |
|------|--------|
| 文档 | PRD(.docx)、概要设计(.docx)、数据库设计(.docx)、接口文档(.docx) |
| 数据 | 建库SQL(.sql)、数据模型JSON、接口定义JSON、routes.json |
| 代码 | 后端代码(Flask)、前端代码(Vue3) |
| 部署 | Dockerfile×2、docker-compose.yml、CI/CD配置、辅助脚本、.env.example |

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Flask + Python |
| 数据库 | MySQL 8.0 + SQLAlchemy + Marshmallow |
| 实时通信 | Flask-SocketIO |
| 认证 | JWT (PyJWT) |
| 前端 | Vue 3 + Element Plus + Pinia + ECharts + Socket.IO Client |
| 架构模式 | ABC接口层 → 抽象实现层 → 设备扩展层 |

## 规范体系

`specs/` 目录包含管线的完整规范定义，分为 **Agent 规范（A系列）** 和 **数据规范（D系列）** 两部分：

### Agent 规范（A系列）

| 编号 | 规范 | 说明 |
|------|------|------|
| A1 | [Agent职责划分规范](specs/A1_Agent职责划分规范.md) | 9个Agent的角色、职责边界、输入输出契约 |
| A2 | [Agent编排调度规范](specs/A2_Agent编排调度规范.md) | DAG驱动的调度顺序、并行策略、状态流转 |
| A3 | [Agent间通信规范](specs/A3_Agent间通信规范.md) | 星型拓扑、7种消息类型、18个错误码 |
| A4 | [Agent上下文管理规范](specs/A4_Agent上下文管理规范.md) | Token三档制、快照版本链、分级衰减注入 |
| A5 | [Agent容错与回退规范](specs/A5_Agent容错与回退规范.md) | L1-L4容错、降级策略、断路器、补偿事务 |

### 数据规范（D系列）

| 编号 | 规范 | 说明 |
|------|------|------|
| D1 | [中间产物JSON Schema规范](specs/D1_中间产物JSON_Schema规范.md) | 10个JSON产物的精确字段定义与约束 |
| D2 | [JSON版本管理规范](specs/D2_JSON版本管理规范.md) | 版本号策略、变更检测、版本链管理、回滚恢复 |
| D3 | [JSON传递与持久化规范](specs/D3_JSON传递与持久化规范.md) | 传递协议、原子写入、存储分级、故障恢复 |
| D4 | [错误数据规范](specs/D4_错误数据规范.md) | 错误全生命周期：分类→富化→传播→聚合→归档 |

### 规范间依赖关系

```
A1 (职责划分)
 ├── A2 (编排调度) ── A3 (通信) ── A5 (容错)
 └── A4 (上下文)
D1 (JSON Schema)
 ├── D2 (版本管理)
 ├── D3 (传递与持久化)
 └── D4 (错误数据) ── 依赖 A3, A5, D1, D2, D3
```

## Skill 体系

| Skill | 输入 | 输出 |
|-------|------|------|
| backend-code-gen | 3个JSON（design + db_model + api_def） | Flask代码 + routes.json |
| frontend-code-gen | api_def + routes + UI模板 | Vue3模块代码 |
| code-validate-repair | 代码文件 + 错误上下文 | 修复后代码（Python/JS双适配） |
| devops-gen | 前后端+数据库三方输出 | Dockerfile、CI/CD配置、部署脚本 |

## Advanced RAG 体系

| RAG库 | 用途 |
|-------|------|
| 代码模式库（Code Pattern Library） | 正确代码范例，供代码生成与修复检索 |
| Bug-Fix对库 | bug→fix配对记录，自增长，供修复参考 |
| UI模板库 | 前端生成时检索组件模板 |

各模块 RAG 角色不同：PRD 用广度扩展，概要设计/数据库/接口用定向补充，代码修复用范例检索，前端生成用 UI 模板检索。

## 项目状态

| 模块 | 状态 |
|------|------|
| A系列 Agent规范 (A1-A5) | ✅ 已完成 |
| D系列 数据规范 (D1-D4) | ✅ 已完成 |
| P系列 Prompt规范 | 📋 待开发 |
| C系列 代码规范 | 📋 待开发 |
| V系列 校验规范 | 📋 待开发 |
| R系列 RAG规范 | 📋 待开发 |
| S系列 Skill规范 | 📋 待开发 |
| 管线实现 | 📋 待开发 |

## License

MIT
