# 主机插件契约（Host Plugin Contract）

> 状态：**Phase 2 契约已定，Phase 3–5 已按契约落地**（batch 刻意暂留）  
> 背景：[tool-plugin-architecture.md](./tool-plugin-architecture.md) Phase 1 已落地本地插件。  
> **交付收成**：[tool-plugin-delivery-handbook.md](./tool-plugin-delivery-handbook.md)（契约摘要 + 迁移清单 + 工具表）。  
> 目标：在**不拆碎执行内核**的前提下，为 `ssh_*` / `winrm_*` / `remote_*` 定义可迁入 `tools/plugins/` 的契约。

---

## 1. 架构决策（不可倒车）

| 原则 | 含义 |
|------|------|
| 执行内核统一 | SSH/WinRM 命令编译、多机 batch、流式 stdout、附件、确认卡字段拷贝 → **仍在** `terminal/mobile/remote_tools.py`（及关联模块） |
| 插件只做入口 | `tools/plugins/<id>/` 提供 manifest + **薄 handler**；handler **委托**内核，不复制命令生成逻辑 |
| 凭据不出插件 | handler / manifest **永不**接收 password、私钥、token；只拿 `host_id` 与业务参数 |
| 先契约后搬运 | Phase 2 定接口 → Phase 3 只读文件 → Phase 4 写入 → Phase 5 命令面 |

**硬编码主机工具不是「没做好」，而是「抽象边界未到」。** Phase 1 只迁无主机绑定的本地工具是正确顺序。

---

## 2. 分期

| 阶段 | 内容 | 风险 |
|------|------|------|
| **Phase 1**（已完成） | 本地插件 loader、`host_required: false`、kb/doc/orch/host_list | 低 |
| **Phase 2**（本文） | 主机插件 manifest / context / 返回值 / 与确认卡·审计对接 | 中（定接口，不大改执行） |
| **Phase 3** | 只读文件面全部迁入 plugins（含 search/diff/logs/backup/syntax_check） | **已完成** |
| **Phase 4** | 写入文件面全部迁入（write / mkdir / remove / restore / rollback） | **已完成** |
| **Phase 5** | 命令面：`remote_run` / `ssh_execute` / `winrm_execute` 已迁；batch 暂留内核 | **主体完成** |

本地侧遗留（智能型旁路、市场分层等）仍可并行，**不阻塞**本文契约。

---

## 3. Manifest 扩展（相对 Phase 1）

Phase 1 字段继续有效。主机插件**额外**约定：

```yaml
name: remote_read_file          # 与目录名一致；与现网 tool 名一致
title: 读取远端文件
description: …
version: "1.0.0"
author: chiby-maintainers
category: remote_fs             # remote_fs | remote_shell | remote_batch
type: host_readonly             # host_readonly | host_write | host_command
host_required: true             # Phase 3+ 必须为 true（host_list 例外已是 false）
status: approved
parameters:
  - name: host
    type: string
    description: 主机 id 或 hostname（与现网 JSON 一致）
    required: true
  - name: path
    type: string
    required: true
  # … 其它与现网 RemoteToolCall 字段对齐
security:
  risk_level: low               # low | medium | high | critical
  needs_confirmation: false
  # 命令面可用 confirm_mode: command_content — 确认卡按 command 内容判定（不强制工具级）
  read_only: true
executor:
  type: python_function
  entry: run                    # handler.run
  # 逻辑归属声明（文档约定，非强制 import 路径）
  delegates_to: terminal.mobile.remote_tools
usage_example: |
  {"tool":"remote_read_file","host":"web1","path":"/var/log/nginx/error.log","tail_lines":100}
```

### 加载门禁（Phase 2+ loader 将增加）

| 条件 | 结果 |
|------|------|
| `host_required: true` 且 `type` 不以 `host_` 开头 | 拒绝加载 |
| `host_required: true` 但缺少 `host` 参数声明（batch 类除外） | 警告或拒绝 |
| `status != approved` | 不加载 |
| 与仍硬编码且**未**列入 `MIGRATED_HOST_PLUGIN_TOOLS` 的同名冲突 | 拒绝覆盖（迁移名单与本地 `MIGRATED_LOCAL_PLUGIN_TOOLS` 同理） |

Phase 2 **契约已定**；Phase 3 起 loader **允许** `host_required: true` + `type: host_readonly`（见实现）。

---

## 4. Handler 签名与 Context

### 4.1 统一签名

与本地插件保持同一入口形状，**能力靠 context 注入**：

```python
def run(params: dict, context: dict) -> dict:
    ...
```

可选：`async def arun(params, context) -> dict`（流式/长命令优先）。

### 4.2 Context 注入（编排器提供，插件只读）

| 键 | 类型 | 说明 |
|----|------|------|
| `agent_mode` | str | omnipotent / intelligent / … |
| `raw` | dict | 原始工具 JSON |
| `host_id` | str | 已解析的目标主机 id（单机） |
| `host_ids` | list[str] | batch 时的目标列表 |
| `conn_type` | str | `ssh` / `winrm` / … |
| `executor` | object | 无凭据；仅暴露 `run_command` 类能力（由现网 Fake/Real executor 适配） |
| `resolve_host` | callable | `(host_key) -> meta`；**不含**密码字段或已脱敏 |
| `host_allowed` | callable | `(host_id) -> bool` ACL |
| `stream_chunk` | callable \| None | `(stream_id, text) -> None` |
| `list_visible_hosts` | callable \| None | 仅列表类需要 |
| `attachment_store` | object \| None | `remote_write_file` 附件 |

**禁止**出现在 context / params：`password`、`secret`、`token`、`private_key`、`api_key`（loader 已对部分键过滤，主机插件同样适用）。

### 4.3 薄 handler 伪代码

```python
# tools/plugins/remote_read_file/handler.py
def run(params: dict, context: dict) -> dict:
    from terminal.mobile.remote_tools import (
        RemoteToolCall,
        execute_host_tool_core,  # Phase 3 抽出的内核入口（命名示意）
    )
    call = RemoteToolCall(tool="remote_read_file", raw={**context.get("raw") or {}, **params})
    # 实际 Phase 3：复用 execute_remote_tool_call 的「host 分支」，或显式 core 函数
    return execute_host_tool_core(call, context)
```

要点：plugins **不**实现 `build_file_tool_command`；只组装 `RemoteToolCall` + 转调。

---

## 5. 返回值

与本地插件、`execute_plugin` 打包格式对齐，便于 `RemoteToolResult` 映射：

```json
{
  "ok": true,
  "error": "",
  "error_code": "",
  "stdout": "…",
  "data": { },
  "duration_ms": 0
}
```

主机工具额外建议（放入 `data` 或顶层，内核已有则透传）：

- `exit_code`、`command`（脱敏后的预览命令）
- `host_id` / `results`（batch）

`format_result(data) -> str` 可选；缺省用 `stdout` / `error`。

---

## 6. 与现网机制对接

### 6.1 白名单

- 工具名继续出现在 `DEFAULT_ALLOWED_TOOLS`（迁移期）。
- 迁入后列入 `MIGRATED_HOST_PLUGIN_TOOLS`，允许插件注册同名；`execute_remote_tool_call`：**插件优先，失败/关闭则回退内核硬编码分支**（与 kb 相同策略）。

### 6.2 确认卡

- `security.needs_confirmation` → `call_needs_confirmation` / `plugin_needs_confirmation`。
- `security.is_mutate: true`（或 `type: host_write`）标记变更类；多机展开仍由 orchestrator 按工具名调用 `expand_batch_mutate_to_per_host`（首迁不改展开名单）。
- **`confirm_fields`**：manifest 声明审批展示字段清单（文档/市场）；**pending 字典仍由** `remote_tool_call_to_pending_dict` 组装（含 path/preview/attachment/content 预览），避免 Phase 4 首工具改两套逻辑。
- 写入类字段拷贝不得省略；orchestrator：`host_required: true` 的插件**绑定** `mut_host`。

### 6.3 审计 / TSM

- 执行前后审计字段（tool、host、command preview、exit_code）由内核或 orchestrator 统一写；插件不直接打 SIEM。

### 6.4 多机 batch（刻意暂留内核）

- **现状**：`ssh_batch` / `winrm_batch` 及协议过滤、扇出收集 **仍在** `remote_tools.py`。
- **判断**：单机工具入口已全部插件化；batch 与内核耦合更深，拆解收益暂低于风险。
- **后续可选**（非阻塞）：
  1. 继续作为内核共享展开器；
  2. 上移编排层，由工具声明批量行为；
  3. 独立 `host_batch` 工具类型。
- 在成为瓶颈前 **不急着迁**。

### 6.5 Preamble

- manifest `usage_example` 并入 `remote_tools_preamble_addon`（与 Phase 1 相同）。
- 主机工具示例必须含 `host` 字段。

---

## 7. 折中落位（推荐目录）

```text
tools/plugins/remote_read_file/
  manifest.yaml          # 契约与示例
  handler.py             # 薄封装 → remote_tools 内核

terminal/mobile/remote_tools.py
  build_file_tool_command / execute_remote_tool_call / confirm …
  # Phase 3 可抽出 execute_host_tool_core 供 handler 与回退路径共用
```

**不**新建第二套 SSH 实现；**不**把 WinRM 适配搬进各个 handler。

---

## 8. Phase 3 验收清单（只读文件面）

- [x] loader 允许 `host_required: true` 且 `type: host_readonly`
- [x] 只读文件工具全部 `is_plugin_tool`：read / list_dir / grep / search / diff / logs / backup / syntax_check
- [x] `OPS_TOOL_PLUGINS=0` 时硬编码回退仍可用
- [x] 确认卡对只读为 false；executor 被调用；无凭据进入 handler params
- [x] 单测覆盖完整只读面链路
- [x] 文档与 `tools/plugins/README.md` 更新

---

## 9. 明确不做（本期）

- 不在 Phase 2 改 orchestrator 主路径（仅文档约束）
- 不上 weighted RRF / rerank（与 DocHub 观测无关）
- 不把 `permissions: ["cat {path}"]` 做成独立策略引擎（可先作为 manifest 文档字段，执行仍走 `build_file_tool_command`）

---

## 10. 相关文档

| 文档 | 关系 |
|------|------|
| [tool-plugin-delivery-handbook.md](./tool-plugin-delivery-handbook.md) | **交付一册**（首选验收入口） |
| [tool-plugin-architecture.md](./tool-plugin-architecture.md) | 总架构与 Phase 1 |
| [extending-agent-tools.md](./extending-agent-tools.md) | 主机工具显式注册 Checklist（迁移完成前仍适用） |
| [confirm-card-design.md](./confirm-card-design.md) | 确认卡字段与风险 |
| ADR-0003 | remote_tools 与 OPS 共存 |
