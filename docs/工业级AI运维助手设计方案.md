## 🏭 工业级 AI 运维助手 — 完整设计方案

> **与代码同步**：本文档描述目标能力与当前 `ai-ops-assistant` 实现的对照；实现细节以 `terminal/main.py`、`terminal/session_manager.py`、`chibycore/` 为准。

---

### 一、现状盘点（已按仓库 2026-05 更新）

| 组件 | 状态 | 说明 |
|------|------|------|
| Web 终端 UI (xterm.js) | ✅ 可用 | 多 Tab、ResizeObserver、剪贴板、非活跃 Tab 不发极小 resize 等已处理；仍随浏览器演进持续加固 |
| 多会话管理 (`session_manager.py`) | ✅ 可用 | 本地 PTY（Unix）/ `WindowsPipeShell`（Windows）、**SSH paramiko**、**WinRM + PowerShell**（单线程协议 I/O + stdin 合并） |
| 任务链引擎 (`chibycore/chains.py`) | ✅ 成熟 | TaskChain、拓扑排序、`ChainExecutor` |
| 自然语言解析 (`chibycore/parser.py`) | ✅ 可用 | 关键词 + 正则 → ActionType |
| LLM Shell (`terminal/llm_shell.py`) | ✅ 可用 | LLM / 规则 fallback、危险模式；终端 NL 默认 **计划模式** |
| **任务链 ↔ 终端** | ✅ 已接 | `terminal/chain_bridge.py`：plan 模式下 **优先** `ChainPlanner` 命中则下发预置链步骤 |
| **命令预览确认** | ✅ 已有 | `llm` + `mode: plan` → `llm_plan`；`approve_plan`（`gated`/`batch`）、`step_ok`、`plan_danger`、`cancel_plan`；前端计划浮层（类 OrcaTerm） |
| **执行网关 + 策略 + 审计** | ✅ 部分 | `chibycore/execution_gateway.py`、`policy_engine.py`、`audit_log.py`、`redaction.py`；经网关：`ws_exec` / `ws_llm_auto` / `ws_confirm` / `ws_plan`（**原始键盘 `input` 不经网关**） |
| **结果检查/校验** | ⚠️ 未接终端流 | `script_generator.build_verify_command` 已有；**每步后自动 verify**、`verification` WS 消息 **未实现** |
| **会话全量输出持久化 / replay** | ❌ 未做 | 仅有回显尾部缓冲供 LLM 上下文 + JSONL **网关审计**；无完整终端录屏与回放 DB |
| **Guacamole RDP/VNC** | ❌ 未开始 | 方案仍为旁路集成，见历史设计说明 |

---

### 二、工业级能力清单

腾讯 OrcaTerm 的核心能力 + 业界标配，以下是必须具备的功能：

```
┌─────────────────────────────────────────────────────────┐
│              工业级 AI 运维平台能力矩阵                    │
├─────────────────────────────────────────────────────────┤
│  1. 自然语言意图理解                                       │
│     - LLM 解析用户自然语言 → 结构化意图                     │
│     - 意图分类：查询类 / 操作类 / 破坏性操作                 │
│     - 上下文记忆（多轮对话）                                │
│                                                         │
│  2. 命令链生成 (Plan Generation)                          │
│     - 基于意图匹配 TaskChain 模板                         │
│     - LLM 生成可执行命令序列                               │
│     - 危险命令识别 + 告警                                   │
│                                                         │
│  3. 执行前确认 (Pre-execution Review)  ← 核心！            │
│     - 显示即将执行的命令序列（编号列表）                    │
│     - 用户逐条/批量确认                                   │
│     - 可编辑/删除/新增命令                                │
│     - 高亮危险操作                                        │
│                                                         │
│  4. 终端执行 + 实时回显                                    │
│     - 命令逐条发送到终端执行                               │
│     - 输出实时流式回显到 xterm.js                         │
│     - 执行状态（成功/失败/超时）标注                       │
│                                                         │
│  5. 结果检查 (Post-execution Verification)  ← 核心！        │
│     - 执行后自动运行验证命令                               │
│     - 关键指标阈值比对                                     │
│     - 异常模式检测                                        │
│                                                         │
│  6. 自动回滚 (Rollback)                                   │
│     - 每步操作前自动记录快照                               │
│     - 失败后自动/手动回滚                                  │
│                                                         │
│  7. 审计日志 (Audit Trail)                               │
│     - 完整操作记录（用户、时间、命令、结果、截图）           │
│     - 命令 replay / 历史回放                              │
│     - 合规报告导出                                        │
│                                                         │
│  8. 资产与凭据管理                                         │
│     - 主机资产库（IP/端口/OS/角色）                        │
│     - SSH 密码 + SSH Key 双支持                           │
│     - 凭据加密存储                                        │
│                                                         │
│  9. 多会话并发                                            │
│     - 多主机同时操作                                       │
│     - 批量执行（命令广播到多终端）                         │
│                                                         │
│ 10. 远程桌面 (RDP/VNC) — 可选但有价值                    │
│     - Guacamole 协议桥接                                  │
│     - Windows 服务器支持                                  │
└─────────────────────────────────────────────────────────┘
```

**与实现对齐的进度简述**：(3) 预览确认、(4) 实时回显、(2) 中链模板匹配 **已具备**；(5)(6)(7) 全链路审计与 replay、(8)(9) 增强 **未完备**。

---

### 三、核心架构设计

```
用户自然语言输入
       │
       ▼
┌──────────────┐
│  意图理解层   │  ← LLM 解析意图 → ActionType + 参数
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  任务链匹配层  │  ← 匹配 TaskChain 或 LLM 生成新链
└──────┬───────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  ⚡ 预览确认层（PRE-EXECUTION REVIEW）    │  ← 已实现（llm_plan + 浮层）
│  显示命令列表 → 用户确认 → 逐条执行       │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  🛡 执行网关（可选策略 + 审计）            │  ← 已实现（AI/计划路径）
│  PolicyEngine + JSONL Audit             │
└──────┬──────────────────────────────────┘
       │
       ▼
┌──────────────┐
│  命令执行层   │  ← PTY / SSH / WinRM → 终端回显
└──────┬───────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  ✅ 结果检查层（POST-EXECUTION VERIFY）   │  ← 规划中
│  验证命令 → 阈值比对 → 异常检测            │
└──────┬──────────────────────────────────┘
       │
       ▼
┌──────────────┐
│  回滚决策层   │  ← 失败? → 自动/手动回滚
└──────────────┘
```

---

### 四、Phase 划分与优先级（与实现同步标注）

#### **Phase 1（立即）：终端 + 命令确认流**

| 优先级 | 任务 | 产出 | 实现状态 |
|--------|------|------|----------|
| P0 | 修复终端已知关键 bug | 可用的 Web 终端 | ✅ 持续迭代 |
| P0 | 命令预览确认面板 | 待执行命令列表 + 危险高亮 | ✅ `llm_plan` + 浮层 |
| P0 | 危险命令阻断 + 二次确认 | 计划内 `plan_danger` + LLM `confirm` | ✅ |
| P1 | LLM 增强解析（MiniMax 等） | 自然语言→命令 | ✅ `llm_providers` + 规则 fallback |
| P1 | 结果检查反馈 | 每步通过/失败/警告 | ❌ 待接 `verify_command` / WS |

#### **Phase 2（强化）：审计 + 多会话**

| 优先级 | 任务 | 实现状态 |
|--------|------|----------|
| P1 | 会话历史持久化（SQLite/文件） | ⚠️ 网关 JSONL 已有；**全会话 transcript** 未做 |
| P1 | 多主机并发 + 批量广播 | ⚠️ 多 Tab 已有；**@group 语法** 未做 |
| P2 | 命令 replay | ❌ |
| P2 | 资产库 + 凭据管理 UI | ⚠️ `hosts.json` + UI；**Key / 加密** 未完备 |

#### **Phase 3（高级）：回滚 + 远程桌面**

| 优先级 | 任务 | 实现状态 |
|--------|------|----------|
| P2 | 自动快照 + 回滚引擎 | ❌ |
| P2 | LLM 生成新 TaskChain（非模板） | ⚠️ 仅模板链 + LLM 多行计划 |
| P3 | Guacamole RDP/VNC | ❌ |
| P3 | 批量工作流编排 | ❌ |

---

### 五、Phase 1 详细设计（命令预览确认流）

#### 5.1 交互原型（目标体验，与当前浮层一致）

```
┌─ 终端窗口 ─────────────────────────────────────────────┐
│  $ 查看服务器资源                                       │
│                                                       │
│  ╔═══════════════════════════════════════════════════╗  │
│  ║  🔍 意图识别: 主机资源监控                         ║  │
│  ║  📋 将执行以下命令:                                ║  │
│  ║  [1] … [2] … [执行全部] [逐条] [取消]              ║  │
│  ╚═══════════════════════════════════════════════════╝  │
└──────────────────────────────────────────────────────┘
```

#### 5.2 WebSocket：目标协议 ↔ **当前实现命名**

下列 **「目标协议」** 为理想 JSON 形状；仓库已用右侧类型实现 **等价流程**（见 `terminal/main.py` 文档字符串）。

| 方向 | 目标（本文草稿） | **当前实现** |
|------|------------------|----------------|
| C→S 自然语言 | `{"type":"nl","data":"…"}` | `{"type":"llm","data":"…","mode":"plan"}`（底部 NL 栏） |
| S→C 预览 | `plan_preview` | `llm_resp`（说明）+ **`llm_plan`**（`plan_id`、`steps[]`） |
| C→S 确认执行 | `plan_confirm` + `step_ids` / `mode` | **`approve_plan`**：`plan_id`、`style`: `gated` \| `batch` |
| C→S 单步确认 | （草案未单列） | **`step_ok`**：`verdict`: `continue` \| `retry` \| `abort` |
| C→S 编辑步骤 | `plan_edit` | ⚠️ 仍为浮层「编辑」后重发 NL；**未**单独 WS 类型 |
| S→C 逐步闸门 | `step_start` / `step_done` / `verification` | **`plan_step`**（`awaiting_user`）、`plan_progress`、`plan_finished`；**无** `verification` 消息 |

**仍规划中的消息**：`step_output` 独立通道（当前命令输出走通用 `output`）；`verification` 结构化结果。

---

### 六、关键技术决策（与代码一致）

| 决策点 | 选择 | 理由 |
|--------|------|------|
| LLM 解析 | 可配置 Provider（含 MiniMax 等） | `chibycore/llm_providers.py` |
| 意图分类 | **TaskChain 优先** + LLM 兜底 | `chain_bridge` + `llm_shell` |
| 命令安全 | 危险模式库 + LLM 标记 + **可选** `OPS_POLICY_ENABLED` 网关硬拦 | 多层 |
| 执行网关 | AI/计划路径统一 `gateway_evaluate` | 原始键盘直连 PTY，避免每键开销 |
| 审计存储 | **JSONL 只追加**（`OPS_AUDIT_FILE`），可轮转 | 已实现；**SQLite 全量审计表** 为 Phase2 可选项 |
| 脱敏 | 口令 / Bearer 等写入审计前 redact | `chibycore/redaction.py` |
| 回滚策略 | 写前快照（cp/tar）| 未实现，仍推荐 Linux 场景 |
| 远程桌面 | noVNC / Guacamole 旁路 | 未集成 |

**环境变量摘要**：`OPS_POLICY_ENABLED`、`OPS_AUDIT_FILE`、`OPS_AUDIT_MAX_MB`、`OPS_AUDIT_ALWAYS`、`OPS_POLICY_EXTRA_DENY`（见 `chibycore/policy_engine.py`、`audit_log.py`）。

---

### 七、风险与挑战

1. **xterm.js**：非活跃 Tab、隐藏容器导致 resize/光标异常 — 已用布局就绪判断 + 避免误 `fit`；需持续回归。
2. **PTY / WSL**：ioctl 行为差异 — 以本机实测为准。
3. **LLM 延迟**：NL 走异步 WS，不阻塞终端键盘 `input`。
4. **命令注入**：LLM 输出必须经过危险检测 + **网关策略**（启用时）。
5. **WinRM**：NTLM 会话需单线程协议访问；Receive 超时与 stdin 合并影响手感 — 已通过 worker 队列与短 `operation_timeout` 优化。

---

### 八、测试与运维

```bash
python -m pytest tests/ -q
```

覆盖：脱敏、策略引擎、审计写入、执行网关与指标（`tests/`）。

---

### 九、同目录其他格式

- `工业级AI运维助手设计方案.docx` / `.htm`：可作为排版或归档；**以本 `.md` 与代码为同步主线**。
- 从 `.htm` 抽取纯文本可用：`python scripts/extract_docx_text.py`（若需对接旧 HTML 导出可再扩展）。

---

### 附录 A：WebSocket 消息 JSON 样例（当前实现）

端点：`/ws/terminal/{session_id}`。下例中 `sess_xxx` 为路径中的会话 ID；`plt_` 前缀计划 ID 由服务端 `new_plan_id()` 生成。字段名与 `terminal/main.py` 中 `send_json` / 解析逻辑一致。

#### A.1 客户端 → 服务端

**键盘 / 尺寸**

```json
{"type": "input", "data": "uname -a\n"}
```

```json
{"type": "resize", "width": 120, "height": 32}
```

**自然语言（计划模式：只生成计划，不自动执行）**

```json
{"type": "llm", "data": "查看磁盘与内存", "mode": "plan"}
```

**自然语言（自动模式：非危险且 `should_execute` 时直接下发终端）**

```json
{"type": "llm", "data": "显示当前目录", "mode": "auto"}
```

**批准计划**（`style`：`gated` 每步后需 `step_ok`；`batch` 连续执行非危险步骤并在步间发 `plan_progress`）

```json
{"type": "approve_plan", "plan_id": "plt_a1b2c3d4e5f6", "style": "gated"}
```

**单步闸门**（仅在 `plan_step` 的 `phase` 为 `awaiting_user` 之后发送；`verdict` 亦接受 `ok` / `yes` / `next` 等同 `continue`）

```json
{"type": "step_ok", "plan_id": "plt_a1b2c3d4e5f6", "step_index": 0, "verdict": "continue"}
```

```json
{"type": "step_ok", "plan_id": "plt_a1b2c3d4e5f6", "step_index": 0, "verdict": "retry"}
```

```json
{"type": "step_ok", "plan_id": "plt_a1b2c3d4e5f6", "step_index": 0, "verdict": "abort"}
```

**取消计划**（`plan_id` 可省略，表示取消当前会话计划）

```json
{"type": "cancel_plan", "plan_id": "plt_a1b2c3d4e5f6"}
```

**危险二次确认**（计划内某步触发 `plan_danger` 后；`data` 为肯定答复之一）

```json
{"type": "confirm", "data": "yes", "plan_id": "plt_a1b2c3d4e5f6"}
```

**兼容：自动模式 LLM 危险命令后的确认**（无 `plan_id`）

```json
{"type": "confirm", "data": "yes"}
```

**显式执行一行或多行**（经网关 `ws_exec`）

```json
{"type": "exec", "command": "df -h\nfree -m"}
```

**心跳**

```json
{"type": "ping"}
```

#### A.2 服务端 → 客户端

**终端 ANSI 流 / 欢迎语**（与 PTY 回显共用同一类型）

```json
{"type": "output", "session_id": "sess_xxx", "data": "\r\n\x1b[32m$ df -h\x1b[0m\r\n"}
```

**会话状态**

```json
{"type": "status", "session_id": "sess_xxx", "status": "connected"}
```

**错误 / 策略拒绝**

```json
{"type": "error", "session_id": "sess_xxx", "data": "策略拒绝执行"}
```

**LLM 运行配置（概要）**

- 合并来源：`data/llm_config.json` + 环境变量（`LLM_MODE`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_DISPLAY_NAME` / `LLM_NAME`、`LLM_BUILTIN_PROVIDER`）；非空环境变量覆盖文件。
- `LLM_MODE=custom` 且配置了 `base_url` 与 `model` 时，仅走 OpenAI 兼容 HTTP（如 Ollama：`http://127.0.0.1:11434/v1`，API Key 可空）；否则为内置链 DeepSeek → OpenAI → MiniMax。
- ChibyTerm：`GET/PUT /api/llm/config`（返回脱敏 Key）、`/api/health` 含 `llm_display_name`；前端状态栏 LLM 徽章可打开设置。
- 变量示例见项目根 `.env.example`。

**LLM 解析结果**（计划模式或链命中时，常在 `llm_plan` 之前出现）

```json
{
  "type": "llm_resp",
  "session_id": "sess_xxx",
  "explanation": "已匹配预置任务链「…」：…",
  "command": "df -h\nfree -m",
  "dangerous": false,
  "warning": "",
  "confirm_required": false,
  "should_execute": true,
  "chain_id": "host_inspect"
}
```

**执行计划载荷**（`source`：`chain` | `llm`；`steps` 每项形状见下）

```json
{
  "type": "llm_plan",
  "session_id": "sess_xxx",
  "plan_id": "plt_a1b2c3d4e5f6",
  "explanation": "…",
  "steps": [
    {
      "index": 0,
      "title": "df -h",
      "command": "df -h",
      "dangerous": false,
      "confirm_required": false,
      "warning": ""
    },
    {
      "index": 1,
      "title": "rm -rf /tmp/foo",
      "command": "rm -rf /tmp/foo",
      "dangerous": true,
      "confirm_required": true,
      "warning": "…"
    }
  ],
  "warning": "",
  "source": "llm"
}
```

**链模板命中的 `llm_plan`**（在上一结构基础上增加 `chain_id`，且 `source` 为 `chain`）

```json
{
  "type": "llm_plan",
  "session_id": "sess_xxx",
  "plan_id": "plt_a1b2c3d4e5f6",
  "explanation": "已匹配预置任务链「…」：…",
  "steps": [{ "index": 0, "title": "…", "command": "df -h", "dangerous": false, "confirm_required": false, "warning": "" }],
  "warning": "",
  "source": "chain",
  "chain_id": "host_inspect"
}
```

**计划已批准**

```json
{
  "type": "plan_status",
  "session_id": "sess_xxx",
  "plan_id": "plt_a1b2c3d4e5f6",
  "phase": "approved",
  "style": "gated",
  "total": 2
}
```

**gated：本步已发往 shell，等待用户 `step_ok`**

```json
{
  "type": "plan_step",
  "session_id": "sess_xxx",
  "plan_id": "plt_a1b2c3d4e5f6",
  "step_index": 0,
  "total": 2,
  "command": "df -h",
  "phase": "awaiting_user"
}
```

**batch：本步已执行（非危险路径）**

```json
{
  "type": "plan_progress",
  "session_id": "sess_xxx",
  "plan_id": "plt_a1b2c3d4e5f6",
  "step_index": 0,
  "total": 2,
  "command": "df -h",
  "phase": "executed"
}
```

**本步被标为危险 / 需确认：在发往终端前暂停，等待 `confirm`**

```json
{
  "type": "plan_danger",
  "session_id": "sess_xxx",
  "plan_id": "plt_a1b2c3d4e5f6",
  "step_index": 1,
  "total": 2,
  "command": "rm -rf /tmp/foo",
  "warning": "危险操作需确认后才会发往终端"
}
```

**计划正常结束**

```json
{
  "type": "plan_finished",
  "session_id": "sess_xxx",
  "plan_id": "plt_a1b2c3d4e5f6",
  "reason": "completed",
  "total_steps": 2
}
```

**计划中止**（用户 abort、策略拒绝、运行中取消等，`reason` 随场景变化）

```json
{
  "type": "plan_aborted",
  "session_id": "sess_xxx",
  "plan_id": "plt_a1b2c3d4e5f6",
  "reason": "user_abort"
}
```

**仅在「待批准」阶段取消**

```json
{"type": "plan_cancelled", "session_id": "sess_xxx", "plan_id": "plt_a1b2c3d4e5f6"}
```

**心跳应答**

```json
{"type": "pong", "session_id": "sess_xxx"}
```

#### A.3 说明

- 命令的**实际 stdout/stderr** 仍通过 **`output`** 推送，无独立的 `step_output` 类型。
- `plan_finished` 的 `reason` 以代码分支为准（如 `completed` 等），集成测试可断言子串或枚举值。
- 审计 JSONL 由服务端网关写入，**不**经 WebSocket 下发；见 `chibycore/audit_log.py`。
