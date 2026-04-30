# D1 中间产物JSON Schema规范

## 1. 总则

### 1.1 目标
定义管线中每个JSON中间产物的精确字段名、类型、必填/选填、约束条件，确保各Agent对数据格式的理解零歧义，支持自动化校验。

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **Schema先行** | 每个JSON产物必须先有Schema定义，才能被生产和消费 |
| **严格类型** | 所有字段必须有明确类型，禁止`any`（除data载荷外） |
| **必填最小化** | 只把下游必须消费的字段标记为必填，可选字段标注默认值 |
| **向后兼容** | Schema变更只能新增字段，不能删除或修改已有字段的语义 |
| **单一权威** | 同一产物只在本规范定义一次，其他规范引用本规范 |

### 1.3 JSON产物全景

| 产物 | 产出者 | 消费者 | 定义章节 |
|------|--------|--------|---------|
| design.json | Design Agent | DB / API / Backend / Frontend | 2 |
| db_model.json | DB Agent | API / Backend | 3 |
| api_def.json | API Agent | Backend / Frontend / Validate | 4 |
| routes.json | Backend Agent | Validate | 5 |
| decision_summary.json | 各Agent追加 | 全局共享 | 6 |
| pipeline_state.json | Orchestrator | Orchestrator / 用户 | 7 |
| {agent}_context.json | 各Agent | 自身（A4快照） | 8 |
| validation_report.json | Validate Agent | Orchestrator / 用户 | 9 |
| error_context.json | 失败Agent | Orchestrator / 用户 | 10 |
| frontend_context.json | Frontend Agent | 自身 | 11 |

---

## 2. design.json — 概要设计结构化JSON

**产出者**：Design Agent（S2）
**消费者**：DB Agent（S3）、API Agent（S3'）、Backend Agent（S4）、Frontend Agent（S4）

### 2.1 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DesignJSON",
  "type": "object",
  "required": ["version", "system_overview", "architecture", "modules", "tech_stack"],
  "additionalProperties": false,
  "properties": {
    "version": {
      "type": "integer",
      "const": 1,
      "description": "Schema版本号，当前固定为1"
    },
    "system_overview": {
      "type": "object",
      "required": ["name", "description", "target_users"],
      "additionalProperties": false,
      "properties": {
        "name": {
          "type": "string",
          "description": "系统名称"
        },
        "description": {
          "type": "string",
          "description": "系统简要描述"
        },
        "target_users": {
          "type": "array",
          "items": { "type": "string" },
          "description": "目标用户群列表"
        }
      }
    },
    "architecture": {
      "type": "object",
      "required": ["pattern", "layers", "device_identification", "audit"],
      "additionalProperties": false,
      "properties": {
        "pattern": {
          "type": "string",
          "description": "架构模式名称"
        },
        "layers": {
          "type": "array",
          "items": { "type": "string" },
          "description": "架构层次列表"
        },
        "device_identification": {
          "type": "string",
          "description": "设备识别方案"
        },
        "audit": {
          "type": "string",
          "description": "审计方案"
        }
      }
    },
    "modules": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "features", "dependencies"],
        "additionalProperties": false,
        "properties": {
          "name": {
            "type": "string",
            "description": "模块名（snake_case）"
          },
          "display_name": {
            "type": "string",
            "description": "模块显示名称"
          },
          "features": {
            "type": "array",
            "items": { "type": "string" },
            "description": "功能点列表"
          },
          "dependencies": {
            "type": "array",
            "items": { "type": "string" },
            "description": "依赖的其他模块名列表"
          },
          "is_core": {
            "type": "boolean",
            "default": false,
            "description": "是否为核心模块（影响降级策略）"
          }
        }
      },
      "description": "系统功能模块列表"
    },
    "tech_stack": {
      "type": "object",
      "required": ["backend", "database", "orm", "validation", "websocket", "auth", "frontend"],
      "additionalProperties": false,
      "properties": {
        "backend": { "type": "string" },
        "database": { "type": "string" },
        "orm": { "type": "string" },
        "validation": { "type": "string" },
        "websocket": { "type": "string" },
        "auth": { "type": "string" },
        "frontend": { "type": "string" }
      }
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次修改+1（D2 §2.4）"
    }
  }
}
```

### 2.2 示例

```json
{
  "version": 1,
  "_data_version": 1,
  "system_overview": {
    "name": "网络安全设备后台管理系统",
    "description": "面向防火墙/IDS/VPN等网络安全设备的统一后台管理平台",
    "target_users": ["安全管理员", "系统管理员", "审计员"]
  },
  "architecture": {
    "pattern": "ABC三层架构",
    "layers": ["interface", "abstract", "device"],
    "device_identification": "Flask Config + Factory",
    "audit": "Decorator"
  },
  "modules": [
    {
      "name": "user_management",
      "display_name": "用户管理",
      "features": ["login", "role", "permission"],
      "dependencies": [],
      "is_core": true
    },
    {
      "name": "alert_management",
      "display_name": "告警管理",
      "features": ["alert_list", "alert_detail", "alert_ack", "alert_rule"],
      "dependencies": ["user_management"],
      "is_core": true
    },
    {
      "name": "log_management",
      "display_name": "日志管理",
      "features": ["operation_log", "security_log", "log_export"],
      "dependencies": ["user_management"],
      "is_core": false
    },
    {
      "name": "system_monitor",
      "display_name": "系统监控",
      "features": ["cpu", "memory", "disk", "process"],
      "dependencies": [],
      "is_core": false
    },
    {
      "name": "network_monitor",
      "display_name": "网络状态监控",
      "features": ["traffic", "connection", "interface_status"],
      "dependencies": [],
      "is_core": false
    }
  ],
  "tech_stack": {
    "backend": "Flask + Python",
    "database": "MySQL 8.0",
    "orm": "SQLAlchemy",
    "validation": "Marshmallow",
    "websocket": "Flask-SocketIO",
    "auth": "JWT (PyJWT)",
    "frontend": "Vue 3 + Element Plus + Pinia + ECharts + Socket.IO Client"
  }
}
```

---

## 3. db_model.json — 数据模型JSON

**产出者**：DB Agent（S3）
**消费者**：API Agent（S3'）、Backend Agent（S4）

### 3.1 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DBModelJSON",
  "type": "object",
  "required": ["version", "tables", "relations"],
  "additionalProperties": false,
  "properties": {
    "version": {
      "type": "integer",
      "const": 1
    },
    "tables": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "columns", "indexes", "engine", "charset"],
        "additionalProperties": false,
        "properties": {
          "name": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$",
            "description": "表名（snake_case）"
          },
          "display_name": {
            "type": "string",
            "description": "表显示名称"
          },
          "module": {
            "type": "string",
            "description": "所属模块名（对应design.json中的module.name）"
          },
          "columns": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["name", "type"],
              "additionalProperties": false,
              "properties": {
                "name": {
                  "type": "string",
                  "pattern": "^[a-z][a-z0-9_]*$",
                  "description": "列名（snake_case）"
                },
                "type": {
                  "type": "string",
                  "description": "MySQL数据类型，如BIGINT、VARCHAR(64)、TEXT、DATETIME"
                },
                "pk": {
                  "type": "boolean",
                  "default": false,
                  "description": "是否为主键"
                },
                "auto_increment": {
                  "type": "boolean",
                  "default": false
                },
                "nullable": {
                  "type": "boolean",
                  "default": true
                },
                "unique": {
                  "type": "boolean",
                  "default": false
                },
                "default": {
                  "description": "默认值，类型与type对应",
                  "type": ["string", "integer", "number", "boolean", "null"]
                },
                "comment": {
                  "type": "string",
                  "description": "列注释"
                },
                "fk": {
                  "type": "object",
                  "description": "外键引用",
                  "properties": {
                    "table": { "type": "string" },
                    "column": { "type": "string" }
                  }
                }
              }
            },
            "description": "列定义列表"
          },
          "indexes": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["name", "columns"],
              "additionalProperties": false,
              "properties": {
                "name": {
                  "type": "string",
                  "description": "索引名"
                },
                "columns": {
                  "type": "array",
                  "items": { "type": "string" },
                  "description": "索引列名列表"
                },
                "unique": {
                  "type": "boolean",
                  "default": false
                }
              }
            },
            "description": "索引定义列表"
          },
          "engine": {
            "type": "string",
            "default": "InnoDB"
          },
          "charset": {
            "type": "string",
            "default": "utf8mb4"
          }
        }
      }
    },
    "relations": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["from", "to", "type", "fk"],
        "additionalProperties": false,
        "properties": {
          "from": {
            "type": "string",
            "description": "来源表名"
          },
          "to": {
            "type": "string",
            "description": "目标表名"
          },
          "type": {
            "type": "string",
            "enum": ["one_to_one", "one_to_many", "many_to_one", "many_to_many"],
            "description": "关系类型"
          },
          "fk": {
            "type": "string",
            "description": "外键列名（在from表中）"
          },
          "through_table": {
            "type": "string",
            "description": "多对多中间表名（仅many_to_many时使用）"
          }
        }
      }
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次修改+1（D2 §2.4）"
    }
  }
}
```

### 3.2 约束规则

| 规则 | 说明 |
|------|------|
| 表名snake_case | 全小写+下划线，如`user_roles` |
| 列名snake_case | 全小写+下划线，如`created_at` |
| 主键统一 | 所有表主键为`id`，类型`BIGINT AUTO_INCREMENT` |
| 时间戳标准 | 每张业务表包含`created_at`和`updated_at`，类型`DATETIME` |
| 软删除标准 | 需要软删除的表包含`is_deleted TINYINT DEFAULT 0` |

---

## 4. api_def.json — 接口定义JSON（唯一契约）

**产出者**：API Agent（S3'）
**消费者**：Backend Agent（S4，作为实现规范）、Frontend Agent（S4，作为调用依据）、Validate Agent（S5，契约校验基准）

> ⚠️ 本JSON是前后端并行的唯一契约（A2契约驱动并行），修改需谨慎。

### 4.1 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "APIDefJSON",
  "type": "object",
  "required": ["version", "base_path", "interfaces", "websocket"],
  "additionalProperties": false,
  "properties": {
    "version": {
      "type": "integer",
      "const": 1
    },
    "base_path": {
      "type": "string",
      "default": "/api/v1",
      "description": "API基础路径"
    },
    "global_response_format": {
      "type": "object",
      "required": ["structure"],
      "properties": {
        "structure": {
          "type": "object",
          "required": ["code", "data", "message"],
          "properties": {
            "code": { "type": "string", "description": "状态码字段名" },
            "data": { "type": "string", "description": "数据字段名" },
            "message": { "type": "string", "description": "消息字段名" }
          }
        },
        "success_code": { "type": "integer", "default": 200 },
        "error_codes": {
          "type": "object",
          "description": "业务错误码映射",
          "additionalProperties": { "type": "string" }
        }
      }
    },
    "pagination": {
      "type": "object",
      "required": ["request_params", "response_structure"],
      "properties": {
        "request_params": {
          "type": "object",
          "properties": {
            "page": { "type": "string", "default": "page" },
            "size": { "type": "string", "default": "size" }
          }
        },
        "response_structure": {
          "type": "object",
          "required": ["items", "total", "page", "size"],
          "properties": {
            "items": { "type": "string" },
            "total": { "type": "string" },
            "page": { "type": "string" },
            "size": { "type": "string" }
          }
        }
      }
    },
    "interfaces": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "module", "methods"],
        "additionalProperties": false,
        "properties": {
          "name": {
            "type": "string",
            "pattern": "^I[A-Z][a-zA-Z0-9]*Manager$",
            "description": "接口名（ABC接口层，I前缀+Manager后缀）"
          },
          "module": {
            "type": "string",
            "description": "所属模块名"
          },
          "base_route": {
            "type": "string",
            "description": "模块基础路由，如/users"
          },
          "methods": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["name", "http", "path"],
              "additionalProperties": false,
              "properties": {
                "name": {
                  "type": "string",
                  "description": "方法名（snake_case）"
                },
                "http": {
                  "type": "string",
                  "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                  "description": "HTTP方法"
                },
                "path": {
                  "type": "string",
                  "description": "路由路径，如/users或/users/{id}"
                },
                "description": {
                  "type": "string"
                },
                "params": {
                  "type": "object",
                  "description": "请求参数（query/form/path），key为参数名",
                  "additionalProperties": {
                    "type": "object",
                    "required": ["type"],
                    "properties": {
                      "type": { "type": "string" },
                      "required": { "type": "boolean", "default": false },
                      "description": { "type": "string" },
                      "in": {
                        "type": "string",
                        "enum": ["query", "body", "path"],
                        "default": "query"
                      },
                      "default": {}
                    }
                  }
                },
                "response": {
                  "type": "object",
                  "required": ["structure"],
                  "properties": {
                    "structure": {
                      "type": "object",
                      "description": "响应data字段的结构",
                      "additionalProperties": { "type": "string" }
                    },
                    "paginated": {
                      "type": "boolean",
                      "default": false,
                      "description": "是否为分页响应"
                    }
                  }
                },
                "abstract": {
                  "type": "boolean",
                  "default": true,
                  "description": "是否为ABC接口层的抽象方法"
                },
                "audit_action": {
                  "type": "string",
                  "description": "审计日志动作名，如user_create"
                }
              }
            }
          }
        }
      }
    },
    "websocket": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["namespace", "events"],
        "additionalProperties": false,
        "properties": {
          "namespace": {
            "type": "string",
            "description": "Socket.IO命名空间，如/ws/monitor"
          },
          "description": {
            "type": "string"
          },
          "events": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["name", "direction"],
              "properties": {
                "name": { "type": "string" },
                "direction": {
                  "type": "string",
                  "enum": ["server_to_client", "client_to_server"]
                },
                "payload_type": { "type": "string", "description": "载荷数据结构描述" }
              }
            }
          }
        }
      }
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次修改+1（D2 §2.4）"
    }
  }
}
```

### 4.2 契约校验规则

| 校验项 | 校验内容 | 校验者 |
|--------|---------|--------|
| 路由完整性 | Backend的routes.json中每条路由在api_def.json中有对应定义 | Validate Layer2.5 |
| 参数一致性 | 前端每次API调用的路径/方法/参数与api_def.json一致 | Validate Layer2.5 |
| 方法完整性 | Backend实现了api_def.json中每个abstract=true的方法 | Validate Layer2 |
| WebSocket一致性 | 前端Socket.IO事件名与api_def.json中定义一致 | Validate Layer2.5 |

---

## 5. routes.json — 后端路由验证产物

**产出者**：Backend Agent（S4）
**消费者**：Validate Agent（S5，契约校验）

> routes.json不是前后端契约，仅用于Validate阶段校验"后端是否按契约实现了所有路由"。

### 5.1 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "RoutesJSON",
  "type": "object",
  "required": ["version", "routes"],
  "additionalProperties": false,
  "properties": {
    "version": {
      "type": "integer",
      "const": 1
    },
    "routes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["http", "path", "handler", "module"],
        "additionalProperties": false,
        "properties": {
          "http": {
            "type": "string",
            "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]
          },
          "path": {
            "type": "string",
            "description": "完整路由路径，如/api/v1/users"
          },
          "handler": {
            "type": "string",
            "description": "处理函数的完整路径，如user_view.UserView.list_users"
          },
          "module": {
            "type": "string",
            "description": "所属模块名"
          },
          "interface_name": {
            "type": "string",
            "description": "对应的ABC接口名（如IUserManager）"
          },
          "method_name": {
            "type": "string",
            "description": "对应的接口方法名（如list_users）"
          },
          "audit_action": {
            "type": "string",
            "description": "审计动作名"
          }
        }
      }
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次修改+1（D2 §2.4）"
    }
  }
}
```

---

## 6. decision_summary.json — 决策摘要

**产出者**：各Agent按职责追加（A4 Append-Only+合并规则）
**消费者**：全局共享，所有下游Agent只读

### 6.1 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DecisionSummaryJSON",
  "type": "object",
  "required": ["version", "last_updated_by", "last_updated_at", "decisions"],
  "additionalProperties": false,
  "properties": {
    "version": {
      "type": "integer",
      "minimum": 1,
      "description": "决策摘要版本号，每次追加/合并+1"
    },
    "last_updated_by": {
      "type": "string",
      "description": "最后一次更新的Agent名"
    },
    "last_updated_at": {
      "type": "string",
      "format": "date-time",
      "description": "最后一次更新时间（ISO 8601）"
    },
    "decisions": {
      "type": "object",
      "description": "决策项集合，key为决策领域",
      "additionalProperties": {
        "type": "object",
        "required": ["updated_by"],
        "properties": {
          "updated_by": {
            "type": "string",
            "description": "写入该决策项的Agent名"
          },
          "updated_at": {
            "type": "string",
            "format": "date-time"
          }
        },
        "description": "具体决策内容，各领域自定义字段"
      }
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次追加/合并+1（D2 §2.4）"
    },
    "_data_version_log": {
      "type": "array",
      "description": "版本变更日志（D2 §2.4），决策摘要保留完整版本日志",
      "items": {
        "type": "object",
        "required": ["version", "timestamp", "agent", "change_type"],
        "properties": {
          "version": { "type": "integer", "description": "变更后的数据版本号" },
          "timestamp": { "type": "string", "format": "date-time" },
          "agent": { "type": "string", "description": "发起变更的Agent名" },
          "change_type": {
            "type": "string",
            "enum": ["initial", "append", "modify", "rollback", "merge", "degradation"]
          },
          "change_detail": { "type": "string" },
          "previous_version": { "type": "integer" }
        }
      }
    }
  }
}
```

### 6.2 标准决策领域

| 决策领域 | 写入者 | 内容示例 |
|---------|--------|---------|
| `architecture` | Design Agent | pattern, layers |
| `tech_stack` | Design Agent | backend, database, orm, ... |
| `database_conventions` | DB Agent | engine, charset, pk_type, ... |
| `response_format` | API Agent | structure {code, data, message} |
| `exception_handling` | API Agent | class, usage |
| `pagination` | API Agent | parser, params, response |
| `audit` | Design Agent | method, decorator |
| `device_identification` | Design Agent | method, function |

### 6.3 更新规则

1. 每个Agent只能新增或更新自己职责范围内的决策项
2. 不得删除或修改其他Agent写入的决策项
3. 通过`updated_by`字段追踪决策来源
4. 下游Agent必须遵守上游Agent已做出的决策
5. 并行阶段各写独立快照，Orchestrator在阶段完成后合并（A4第5.3节）

---

## 7. pipeline_state.json — 管线状态

**产出者**：Orchestrator
**消费者**：Orchestrator（调度决策）、用户（进度查询）

### 7.1 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "PipelineStateJSON",
  "type": "object",
  "required": ["pipeline_id", "status", "current_stage", "stage_status", "artifacts", "errors", "created_at", "updated_at"],
  "additionalProperties": false,
  "properties": {
    "pipeline_id": {
      "type": "string",
      "format": "uuid",
      "description": "管线唯一标识"
    },
    "status": {
      "type": "string",
      "enum": ["PENDING", "RUNNING", "COMPLETED", "FAILED", "RETRYING", "ROLLBACK", "MANUAL_INTERVENTION", "SKIPPED"],
      "description": "管线整体状态（A2第3.1节）"
    },
    "current_stage": {
      "type": "string",
      "enum": ["prd", "design", "db", "api", "backend", "frontend", "validate", "devops"],
      "description": "当前执行阶段"
    },
    "stage_status": {
      "type": "object",
      "description": "各阶段状态",
      "properties": {
        "prd":       { "type": "string", "enum": ["pending", "running", "completed", "failed"] },
        "design":    { "type": "string", "enum": ["pending", "running", "completed", "failed"] },
        "db":        { "type": "string", "enum": ["pending", "running", "completed", "failed"] },
        "api":       { "type": "string", "enum": ["pending", "running", "completed", "failed"] },
        "backend":   { "type": "string", "enum": ["pending", "running", "completed", "failed"] },
        "frontend":  { "type": "string", "enum": ["pending", "running", "completed", "failed"] },
        "validate":  { "type": "string", "enum": ["pending", "running", "completed", "failed"] },
        "devops":    { "type": "string", "enum": ["pending", "running", "completed", "failed"] }
      }
    },
    "artifacts": {
      "type": "object",
      "description": "已产出物路径映射",
      "properties": {
        "prd_doc":           { "type": ["string", "null"] },
        "design_doc":        { "type": ["string", "null"] },
        "design_json":       { "type": ["string", "null"] },
        "db_doc":            { "type": ["string", "null"] },
        "db_sql":            { "type": ["string", "null"] },
        "db_model_json":     { "type": ["string", "null"] },
        "api_doc":           { "type": ["string", "null"] },
        "api_json":          { "type": ["string", "null"] },
        "backend_code_dir":  { "type": ["string", "null"] },
        "frontend_code_dir": { "type": ["string", "null"] },
        "routes_json":       { "type": ["string", "null"] },
        "validation_report": { "type": ["string", "null"] }
      }
    },
    "errors": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["timestamp", "agent", "level", "error_code", "message"],
        "properties": {
          "timestamp":   { "type": "string", "format": "date-time" },
          "agent":       { "type": "string" },
          "level":       { "type": "string", "enum": ["L1", "L2", "L3", "L4"] },
          "error_code":  { "type": "string" },
          "message":     { "type": "string" },
          "action_taken": { "type": "string" },
          "resolved":    { "type": "boolean", "default": false }
        }
      }
    },
    "circuit_breakers": {
      "type": "object",
      "description": "断路器状态（A5第6.6节）",
      "additionalProperties": {
        "type": "object",
        "properties": {
          "state":          { "type": "string", "enum": ["closed", "open", "half_open"] },
          "failure_count":  { "type": "integer" },
          "opened_at":      { "type": ["string", "null"], "format": "date-time" },
          "half_open_at":   { "type": ["string", "null"], "format": "date-time" },
          "last_failure_at":{ "type": ["string", "null"], "format": "date-time" },
          "degraded":       { "type": "boolean", "default": false }
        }
      }
    },
    "retry_counters": {
      "type": "object",
      "description": "各Agent的重试/修复计数",
      "additionalProperties": {
        "type": "object",
        "properties": {
          "retry_count":  { "type": "integer", "default": 0 },
          "fix_rounds":   { "type": "integer", "default": 0 },
          "rollback_count": { "type": "integer", "default": 0 }
        }
      }
    },
    "degradation_status": {
      "type": "object",
      "description": "降级状态（A5第3节）",
      "properties": {
        "rag_degraded":          { "type": "boolean", "default": false },
        "quality_degraded":      { "type": "boolean", "default": false },
        "degraded_modules":      { "type": "array", "items": { "type": "string" } },
        "degraded_agents":       { "type": "array", "items": { "type": "string" } },
        "compensation_applied":  { "type": "array", "items": { "type": "string" } }
      }
    },
    "artifact_versions": {
      "type": "object",
      "description": "各产物当前版本追踪（D2 §12），使Orchestrator无需遍历文件系统即可了解版本状态",
      "properties": {
        "design.json":             { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } },
        "db_model.json":           { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } },
        "api_def.json":            { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } },
        "routes.json":             { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } },
        "decision_summary.json":   { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } },
        "backend_context.json":    { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } },
        "frontend_context.json":   { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } },
        "validation_report.json":  { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } },
        "error_context.json":      { "type": ["object", "null"], "properties": { "schema_version": { "type": "integer" }, "data_version": { "type": "integer" } } }
      }
    },
    "version_snapshots": {
      "type": "array",
      "description": "跨产物版本快照（D2 §10.2），在关键节点保存产物版本一致性快照",
      "items": {
        "type": "object",
        "required": ["snapshot_point", "timestamp", "artifact_versions"],
        "properties": {
          "snapshot_point": { "type": "string", "description": "快照点，如S3'_completed、S4_completed、S5_completed" },
          "timestamp": { "type": "string", "format": "date-time" },
          "artifact_versions": {
            "type": "object",
            "description": "快照时刻各产物版本",
            "additionalProperties": {
              "type": "object",
              "properties": {
                "schema_version": { "type": "integer" },
                "data_version": { "type": "integer" }
              }
            }
          }
        }
      }
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次状态变更+1（D2 §2.4）"
    },
    "_data_version_log": {
      "type": "array",
      "description": "版本变更日志（D2 §2.4），管线状态保留完整版本日志",
      "items": {
        "type": "object",
        "required": ["version", "timestamp", "agent", "change_type"],
        "properties": {
          "version": { "type": "integer", "description": "变更后的数据版本号" },
          "timestamp": { "type": "string", "format": "date-time" },
          "agent": { "type": "string", "description": "发起变更的Agent名" },
          "change_type": {
            "type": "string",
            "enum": ["initial", "append", "modify", "rollback", "merge", "degradation"]
          },
          "change_detail": { "type": "string" },
          "previous_version": { "type": "integer" }
        }
      }
    },
    "created_at": {
      "type": "string",
      "format": "date-time"
    },
    "updated_at": {
      "type": "string",
      "format": "date-time"
    }
  }
}
```

---

## 8. {agent}_context.json — Agent上下文清单

**产出者**：各Agent自身维护
**消费者**：Agent自身（A4快照版本链）

### 8.1 Backend Agent上下文Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "BackendContextJSON",
  "type": "object",
  "required": ["version", "agent", "pipeline_id", "timestamp", "state", "signatures", "routes", "token_usage"],
  "additionalProperties": false,
  "properties": {
    "version": {
      "type": "integer",
      "minimum": 1,
      "description": "快照版本号"
    },
    "agent": {
      "type": "string",
      "const": "backend_agent"
    },
    "pipeline_id": {
      "type": "string",
      "format": "uuid"
    },
    "timestamp": {
      "type": "string",
      "format": "date-time"
    },
    "trigger": {
      "type": "string",
      "enum": ["file_completed", "module_completed", "error", "rollback"],
      "description": "快照触发原因"
    },
    "trigger_detail": {
      "type": "string",
      "description": "触发详情，如具体文件名"
    },
    "state": {
      "type": "object",
      "required": ["completed_files", "pending_files", "current_module", "module_progress"],
      "properties": {
        "completed_files": {
          "type": "array",
          "items": { "type": "string" },
          "description": "已生成完成的文件路径列表"
        },
        "pending_files": {
          "type": "array",
          "items": { "type": "string" },
          "description": "待生成文件路径列表"
        },
        "current_module": {
          "type": "string",
          "description": "当前正在处理的模块名"
        },
        "module_progress": {
          "type": "object",
          "description": "各模块进度",
          "additionalProperties": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed", "degraded"]
          }
        }
      }
    },
    "signatures": {
      "type": "object",
      "description": "已生成类的签名字典，key为类名",
      "additionalProperties": { "type": "string" }
    },
    "routes": {
      "type": "object",
      "description": "各模块已注册路由，key为模块名",
      "additionalProperties": {
        "type": "array",
        "items": { "type": "string" }
      }
    },
    "decision_summary_ref": {
      "type": "string",
      "description": "决策摘要文件路径引用"
    },
    "token_usage": {
      "type": "object",
      "required": ["total_input", "total_output", "budget_tier"],
      "properties": {
        "total_input":  { "type": "integer" },
        "total_output": { "type": "integer" },
        "budget_tier":  { "type": "string", "enum": ["compact", "standard", "spacious"] }
      }
    },
    "lifecycle": {
      "type": "string",
      "enum": ["created", "updating", "frozen", "archived"],
      "default": "updating",
      "description": "上下文生命周期状态（A4第6节）"
    },
    "frozen_at": {
      "type": ["string", "null"],
      "format": "date-time",
      "description": "冻结时间戳"
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次快照+1（D2 §2.4）"
    }
  }
}
```

### 8.2 Frontend Agent上下文Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "FrontendContextJSON",
  "type": "object",
  "required": ["version", "agent", "pipeline_id", "timestamp", "state", "token_usage"],
  "additionalProperties": false,
  "properties": {
    "version":       { "type": "integer", "minimum": 1 },
    "agent":         { "type": "string", "const": "frontend_agent" },
    "pipeline_id":   { "type": "string", "format": "uuid" },
    "timestamp":     { "type": "string", "format": "date-time" },
    "trigger":       { "type": "string" },
    "trigger_detail":{ "type": "string" },
    "state": {
      "type": "object",
      "required": ["completed_files", "module_progress"],
      "properties": {
        "completed_files": {
          "type": "array",
          "items": { "type": "string" }
        },
        "pending_files": {
          "type": "array",
          "items": { "type": "string" }
        },
        "current_module": { "type": "string" },
        "module_progress": {
          "type": "object",
          "additionalProperties": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed", "degraded"]
          }
        },
        "component_registry": {
          "type": "object",
          "description": "已注册Vue组件，key为组件名，value为文件路径",
          "additionalProperties": { "type": "string" }
        },
        "composables_registry": {
          "type": "object",
          "description": "已注册composables，key为名称，value为文件路径",
          "additionalProperties": { "type": "string" }
        },
        "api_endpoints_used": {
          "type": "object",
          "description": "已使用的API端点，key为路径，value为使用该端点的模块",
          "additionalProperties": { "type": "string" }
        }
      }
    },
    "decision_summary_ref": { "type": "string" },
    "token_usage": {
      "type": "object",
      "required": ["total_input", "total_output", "budget_tier"],
      "properties": {
        "total_input":  { "type": "integer" },
        "total_output": { "type": "integer" },
        "budget_tier":  { "type": "string", "enum": ["compact", "standard", "spacious"] }
      }
    },
    "lifecycle": { "type": "string", "enum": ["created", "updating", "frozen", "archived"] },
    "frozen_at":  { "type": ["string", "null"], "format": "date-time" },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次快照+1（D2 §2.4）"
    }
  }
}
```

### 8.3 Validate Agent上下文Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ValidateContextJSON",
  "type": "object",
  "required": ["version", "agent", "pipeline_id", "timestamp", "state", "token_usage"],
  "additionalProperties": false,
  "properties": {
    "version":       { "type": "integer", "minimum": 1 },
    "agent":         { "type": "string", "const": "validate_agent" },
    "pipeline_id":   { "type": "string", "format": "uuid" },
    "timestamp":     { "type": "string", "format": "date-time" },
    "state": {
      "type": "object",
      "required": ["checked_files", "issues_found", "fix_history"],
      "properties": {
        "checked_files": {
          "type": "array",
          "items": { "type": "string" }
        },
        "issues_found": {
          "type": "integer",
          "default": 0
        },
        "fix_history": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "file":        { "type": "string" },
              "layer":       { "type": "string", "enum": ["L1_static", "L2_llm", "L2.5_contract", "L3_runtime"] },
              "issue_count": { "type": "integer" },
              "fix_round":   { "type": "integer" },
              "result":      { "type": "string", "enum": ["fixed", "still_has_issues", "escalated"] }
            }
          }
        },
        "current_layer": {
          "type": "string",
          "enum": ["L1_static", "L2_llm", "L2.5_contract", "L3_runtime", "completed"]
        },
        "contract_check_results": {
          "type": "object",
          "description": "契约一致性校验结果",
          "properties": {
            "routes_vs_api_def": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "route":  { "type": "string" },
                  "status": { "type": "string", "enum": ["matched", "missing_in_api_def", "missing_in_routes"] }
                }
              }
            },
            "frontend_vs_api_def": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "api_call": { "type": "string" },
                  "status":   { "type": "string", "enum": ["matched", "method_mismatch", "param_mismatch", "not_in_api_def"] }
                }
              }
            }
          }
        }
      }
    },
    "decision_summary_ref": { "type": "string" },
    "token_usage": {
      "type": "object",
      "properties": {
        "total_input":  { "type": "integer" },
        "total_output": { "type": "integer" },
        "budget_tier":  { "type": "string" }
      }
    },
    "lifecycle": { "type": "string", "enum": ["created", "updating", "frozen", "archived"] },
    "frozen_at":  { "type": ["string", "null"], "format": "date-time" },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次快照+1（D2 §2.4）"
    }
  }
}
```

---

## 9. validation_report.json — 检查报告

**产出者**：Validate Agent（S5）
**消费者**：Orchestrator（决策是否继续）、用户（查看代码质量）

### 9.1 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ValidationReportJSON",
  "type": "object",
  "required": ["version", "pipeline_id", "timestamp", "summary", "layers", "overall_result"],
  "additionalProperties": false,
  "properties": {
    "version": { "type": "integer", "const": 1 },
    "pipeline_id": { "type": "string", "format": "uuid" },
    "timestamp": { "type": "string", "format": "date-time" },
    "summary": {
      "type": "object",
      "required": ["total_files_checked", "total_issues", "critical_count", "warning_count", "info_count", "fix_rounds_used"],
      "properties": {
        "total_files_checked": { "type": "integer" },
        "total_issues":        { "type": "integer" },
        "critical_count":      { "type": "integer" },
        "warning_count":       { "type": "integer" },
        "info_count":          { "type": "integer" },
        "fix_rounds_used":     { "type": "integer" },
        "quality_degraded":    { "type": "boolean", "default": false },
        "skipped_checks":      { "type": "array", "items": { "type": "string" } }
      }
    },
    "layers": {
      "type": "object",
      "required": ["L1_static", "L2_llm", "L2.5_contract", "L3_runtime"],
      "properties": {
        "L1_static": {
          "type": "object",
          "required": ["passed", "issues"],
          "properties": {
            "passed": { "type": "boolean" },
            "issues": {
              "type": "array",
              "items": {
                "type": "object",
                "required": ["file", "line", "severity", "rule", "message"],
                "properties": {
                  "file":     { "type": "string" },
                  "line":     { "type": "integer" },
                  "severity": { "type": "string", "enum": ["CRITICAL", "WARNING", "INFO"] },
                  "rule":     { "type": "string", "description": "触发的规则名，如E0501、F821" },
                  "message":  { "type": "string" },
                  "fix_applied": { "type": "boolean", "default": false },
                  "fix_detail":  { "type": "string" }
                }
              }
            }
          }
        },
        "L2_llm": {
          "type": "object",
          "required": ["passed", "issues"],
          "properties": {
            "passed": { "type": "boolean" },
            "issues": {
              "type": "array",
              "items": {
                "type": "object",
                "required": ["file", "severity", "category", "message"],
                "properties": {
                  "file":     { "type": "string" },
                  "severity": { "type": "string", "enum": ["CRITICAL", "WARNING", "INFO"] },
                  "category": { "type": "string", "enum": ["logic", "framework", "security", "interface_consistency"] },
                  "message":  { "type": "string" },
                  "suggestion": { "type": "string" }
                }
              }
            }
          }
        },
        "L2.5_contract": {
          "type": "object",
          "required": ["passed", "backend_check", "frontend_check"],
          "properties": {
            "passed": { "type": "boolean" },
            "backend_check": {
              "type": "object",
              "required": ["total_routes", "matched", "mismatched"],
              "properties": {
                "total_routes": { "type": "integer" },
                "matched":      { "type": "integer" },
                "mismatched":   { "type": "integer" },
                "details": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {
                      "route":    { "type": "string" },
                      "status":   { "type": "string", "enum": ["matched", "missing_in_api_def", "missing_in_routes", "method_mismatch"] },
                      "detail":   { "type": "string" }
                    }
                  }
                }
              }
            },
            "frontend_check": {
              "type": "object",
              "required": ["total_api_calls", "matched", "mismatched"],
              "properties": {
                "total_api_calls": { "type": "integer" },
                "matched":         { "type": "integer" },
                "mismatched":      { "type": "integer" },
                "details": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {
                      "api_call": { "type": "string" },
                      "status":   { "type": "string", "enum": ["matched", "method_mismatch", "param_mismatch", "not_in_api_def"] },
                      "detail":   { "type": "string" }
                    }
                  }
                }
              }
            }
          }
        },
        "L3_runtime": {
          "type": "object",
          "required": ["passed", "steps"],
          "properties": {
            "passed": { "type": "boolean" },
            "steps": {
              "type": "array",
              "items": {
                "type": "object",
                "required": ["step", "status"],
                "properties": {
                  "step":    { "type": "string", "enum": ["import", "app_creation", "db_init", "route_registration", "api_endpoint_test"] },
                  "status":  { "type": "string", "enum": ["passed", "failed", "skipped"] },
                  "detail":  { "type": "string" }
                }
              }
            }
          }
        }
      }
    },
    "overall_result": {
      "type": "string",
      "enum": ["PASS", "PASS_WITH_WARNINGS", "FAIL", "FAIL_REQUIRES_MANUAL"],
      "description": "综合判定结果"
    },
    "requires_manual_intervention": {
      "type": "boolean",
      "default": false
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号，每次修改+1（D2 §2.4）"
    }
  }
}
```

---

## 10. error_context.json — 错误快照

**产出者**：失败Agent
**消费者**：Orchestrator（错误路由）、用户（问题诊断）

### 10.1 Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ErrorContextJSON",
  "type": "object",
  "required": ["error_id", "timestamp", "pipeline_id", "agent", "level", "error_code", "message"],
  "additionalProperties": false,
  "properties": {
    "error_id": {
      "type": "string",
      "format": "uuid"
    },
    "timestamp": {
      "type": "string",
      "format": "date-time"
    },
    "pipeline_id": {
      "type": "string",
      "format": "uuid"
    },
    "agent": {
      "type": "string",
      "description": "出错的Agent名"
    },
    "stage": {
      "type": "string",
      "description": "出错阶段"
    },
    "level": {
      "type": "string",
      "enum": ["L1", "L2", "L3", "L4"]
    },
    "error_code": {
      "type": "string",
      "description": "A3定义的18个错误码之一"
    },
    "message": {
      "type": "string"
    },
    "retry_count": {
      "type": "integer",
      "default": 0
    },
    "fix_rounds": {
      "type": "integer",
      "default": 0
    },
    "affected_files": {
      "type": "array",
      "items": { "type": "string" }
    },
    "context_snapshot_path": {
      "type": "string",
      "description": "出错时的Agent上下文快照路径"
    },
    "attempted_actions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "action":  { "type": "string" },
          "count":   { "type": "integer" },
          "result":  { "type": "string", "enum": ["success", "failed"] }
        }
      },
      "description": "已尝试的容错动作列表"
    },
    "suggestion": {
      "type": "string",
      "description": "建议处理方式，如rollback_to_api_agent"
    },
    "blast_radius": {
      "type": "string",
      "enum": ["file", "module", "agent", "pipeline"],
      "description": "错误影响范围（A5第10.3节）"
    },
    "_data_version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "数据内容版本号（D2 §2.4），error_context为一次性产物通常为1"
    }
  }
}
```

> **D4扩展说明**：本Schema定义error_context.json的基础字段。D4 §3定义了完整的ErrorRecordJSON，在基础字段上扩展了source_layer、business_domain、cluster_id、enrichments、lifecycle、llm_context、input_artifacts等字段。管线实现中，D4的ErrorRecordJSON是错误数据的权威Schema，本节定义的error_context.json作为Orchestrator错误路由的轻量快照格式。

---

## 11. 通用约束

### 11.1 字段命名规范

| 规则 | 说明 | 示例 |
|------|------|------|
| snake_case | 所有JSON字段名使用snake_case | `total_input`, `module_progress` |
| 避免缩写 | 除行业通用缩写外，使用完整单词 | `pagination`而非`pgn` |
| 布尔前缀 | 布尔字段可用`is_`/`has_`前缀 | `is_core`, `has_submodules` |
| 时间后缀 | ISO 8601时间戳字段以`_at`结尾 | `created_at`, `frozen_at` |
| 计数后缀 | 计数字段以`_count`结尾 | `retry_count`, `fix_rounds` |

### 11.2 通用元数据字段

每个JSON产物应包含以下元数据字段（如适用）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `version` | integer | 是 | Schema版本号 |
| `_data_version` | integer | 否 | 数据内容版本号（D2 §2.4），默认1 |
| `_data_version_log` | array | 否 | 版本变更日志（D2 §2.4），仅pipeline_state和decision_summary保留 |
| `pipeline_id` | string(uuid) | 条件 | 属于哪条管线（状态/上下文类必填，产物类可选） |
| `timestamp` | string(date-time) | 条件 | 产出时间（上下文/错误类必填） |

### 11.3 Schema版本策略

| 变更类型 | 版本动作 | 兼容性 |
|---------|---------|--------|
| 新增可选字段 | version不变 | 完全向后兼容 |
| 新增必填字段（有默认值） | version不变 | 向后兼容（旧数据补充默认值） |
| 新增必填字段（无默认值） | version+1 | 不兼容，需迁移 |
| 修改字段语义 | version+1 | 不兼容 |
| 删除字段 | 禁止 | —（保留为deprecated） |

### 11.4 空值处理

| 场景 | 处理方式 |
|------|---------|
| 字段尚未有值 | 设为`null`（类型定义为`["string", "null"]`） |
| 数组为空 | 设为`[]`，不设为`null` |
| 对象为空 | 设为`{}`，不设为`null` |
| 不适用的字段 | 不包含该字段（而非设为null） |

---

## 12. 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.1 | 2026-04-30 | A+D系列交叉审查修复：各Schema添加_data_version/_data_version_log字段（#D-1）、pipeline_state添加artifact_versions/version_snapshots字段（#D-2）、error_context添加D4扩展引用说明（#D-3） |
| v1.0 | 2026-04-30 | 初始版本，定义10个JSON产物的Schema、通用约束、命名规范、版本策略 |
