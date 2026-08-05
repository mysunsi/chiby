# Assistant 当前系统 · 代码结构与功能说明

版本：v1.0  
日期：2026-07-22  
状态：现行实现对照（随仓库演进请同步修订）

关联：[docs/index.md](./index.md) · [ai-agent-contest-system-gap.md](./ai-agent-contest-system-gap.md) · [adr/0003](./adr/0003-remote-tools-and-ops-coexistence.md) · [adr/0004](./adr/0004-mode-hierarchy.md)

---

## 1. 系统定位

**Assistant** 是「尚思 / 掌上 AI 运维」的执行与编排宿主：

| 能力层 | 职责 |
|--------|------|
| Web 终端 | xterm 交互会话、主机管理、NL→Shell、闭环修复入口 |
| 掌上AI机房 | IM 式对话、三模式 Agent、确认卡、无头 SSH/WinRM、审计 |
| Hermes 桥 | ACP 子进程协议桥；规划默认 plan-only，禁止把凭据交给 Hermes |
| chibycore | 无头执行器、策略网关、闭环/自愈、知识库、脱敏 |

一句话：**Hermes（或规则）负责想；Assistant 负责连主机、确认、执行、审计。**

---

## 2. 顶层目录

```text
Assistant/
├── terminal/           # FastAPI 应用：终端 UI、掌上演示、Hermes 桥
├── chibycore/           # 执行平面、闭环、策略、知识库、LLM 配置
├── data/               # 运行时配置与落盘（hosts、审计、会话、YAML）
├── docs/               # 设计 / ADR / 操作手册
├── deploy/             # Docker Compose 等
├── tests/              # pytest
├── api/ · dashboard/ · remediator/ · scripts/ · web/ …
├── requirements.txt · pyproject.toml · README.md
└── .env.example
```

掌上演示与比赛相关主链：**`terminal/` + `chibycore/` + `data/`**。

---

## 3. 启动与入口

| 入口 | 说明 |
|------|------|
| `uvicorn terminal.main:app --host 127.0.0.1 --port 8000` | 文档推荐 |
| `python -m terminal.main` | 默认端口常受 `OPS_SHELL_PORT` 影响（常见 8022） |
| `GET /api/health` | 健康检查 |
| `deploy/docker-compose.yml` | 容器化（挂载 `./data`） |

应用入口文件：[`terminal/main.py`](../terminal/main.py)（注册 WS、REST、掌上路由、静态页）。

---

## 4. `terminal/` — Web 与编排

### 4.1 结构

```text
terminal/
├── main.py                 # FastAPI app、主机 API、终端 WS、挂载子路由
├── session_manager.py      # 交互式终端会话生命周期
├── llm_shell.py            # 自然语言 → Shell / 危险判定
├── hermes_ws.py            # /ws/hermes
├── hermes_audit_api.py     # Hermes 相关 REST
├── agent_service.py · chain_bridge.py · command_aggregate.py …
├── web/                    # 静态 HTML
│   ├── index.html · standalone_terminal.html
│   ├── mobile_im_demo.html · mobile_audit.html
│   ├── mobile_jobs.html · hermes_lab.html
├── hermes_bridge/          # Hermes ACP 子进程桥
└── mobile/                 # 掌上AI机房（核心编排）
```

### 4.2 主要页面

| URL | 页面 | 功能 |
|-----|------|------|
| `/` · `/terminal` | `index.html` | 主终端 + 主机管理 |
| `/t/{session_id}` | `standalone_terminal.html` | 单会话终端 |
| `/demo/mobile-im` | `mobile_im_demo.html` | 掌上 IM 对话（三模式、确认卡、SSE） |
| `/demo/mobile-audit` | `mobile_audit.html` | 审计浏览 |
| `/demo/mobile-jobs` | `mobile_jobs.html` | 多机 Job |
| `/demo/knowledge-hub` | `knowledge_hub.html` | 本地知识库 CRUD 管理 |
| `/demo/hermes-lab` | `hermes_lab.html` | Skills / MCP / 记忆轻量面板 |
| （文档） | [`knowledge-hub-user-manual.md`](./knowledge-hub-user-manual.md) | 知识库使用手册（生命周期 + 示例） |

### 4.3 `terminal/hermes_bridge/`

| 模块 | 功能 |
|------|------|
| `config.py` | 加载 `data/hermes_bridge.yaml`（plan_only、mobile_demo、remote_tools） |
| `spawn.py` | 启动 Hermes 子进程（支持 `uv` 工程目录） |
| `acp_wire.py` | ACP 方法名、权限体、事件常量 |
| `acp_worker.py` | **无 WS** Worker：掌上 headless `begin_turn` 用 |
| `acp_session.py` | stdio ACP + WebSocket：终端 Hermes Tab |
| `native_workspace.py` | 本机白名单编程路径（ADR-0002） |
| `text_clean.py` · `ws_validate.py` | 思考剥离、出站校验 |
| `skills_introspect.py` · `memory_introspect.py` | Lab 内省 |

**配置要点**（`hermes_bridge.yaml`）：

- `execution_mode: headless_proxy` + `plan_only: true` → Hermes 只规划，不执行本机命令  
- `mobile_demo.executor: real|fake` → 真 SSH/WinRM 或罐头输出  
- `remote_tools`：工具白名单（通道是否开启由**模式**强制，见 ADR-0004）

### 4.4 `terminal/mobile/` — 掌上AI机房

| 模块 | 功能 |
|------|------|
| `api.py` | 演示路由：消息/审批 SSE、模式、选机、审计、取消 |
| `orchestrator.py` | **总编排**：会话、三模式分支、确认卡、A1/A2 闭环、熔断 |
| `agent_mode.py` | `efficient` / `intelligent` / `omnipotent` 策略 |
| `planner_m0.py` | 高效型规则规划（无 Hermes） |
| `hermes_planner.py` | Hermes 回合：`begin_turn`、权限收尾、续接注入 |
| `hermes_protocol.py` | 解析 `OPS_PLAN` / `OPS_JOB` / `REMOTE_TOOL`；会话续接 / Host Snapshot 注入 |
| `remote_tools.py` | **A2** 工具路由与执行（ssh/winrm/文件/备份等） |
| `remote_devtools.py` | grep / diff / backup / logs / syntax_check 等实现 |
| `headless_exec.py` | Fake / Real 无头执行（调 `chibycore` oneshot） |
| `confirm_card_meta.py` | 确认卡风险分级、摘要、本地 AI 解读、YES 二次确认 |
| `exec_guardrails.py` | 主机/Hermes 熔断、自动变更白名单、写文件预览 |
| `audit.py` | `data/mobile_audit.jsonl` |
| `session_store.py` | `data/mobile_sessions/*.json` |
| `transcript.py` | `data/mobile_transcripts/` |
| `host_snapshot.py` | 跨会话主机快照 `data/host_snapshots/` |
| `repair_txn.py` | 可回滚变更事务与回滚预览 |
| `acl.py` · `escalation.py` | ACL、熔断升级通知 |
| `job_*.py` | 多主机 Job 扇出 |
| `im/` | 飞书 / 企微桥 |
| `models.py` | `PermissionCard`、入站出站模型等 |

#### 掌上关键 API

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/mobile/demo/message[/stream]` | 发消息（SSE） |
| POST | `/api/mobile/demo/permission[/stream]` | 确认卡 allow/deny |
| POST | `/api/mobile/demo/agent-mode` | 切换模式 |
| POST | `/api/mobile/demo/targets` | 选择目标主机 |
| POST | `/api/mobile/demo/cancel` | 停止当前流 |
| GET | `/api/mobile/demo/audit` · `status` · … | 审计与状态 |

---

## 5. 三模式与执行通道

| 模式 ID | 中文 | Planner | 通道 | remote_tools | 确认策略（概要） |
|---------|------|---------|------|--------------|------------------|
| `efficient` | 高效型 | 规则 `planner_m0` | **A1** | 关 | **高危必确认**（含任意删除/停服务；只读可直跑） |
| `intelligent` | 智能型 | Hermes | **A1**（`OPS_PLAN`/`OPS_JOB` 闭环） | 关 | 变更确认 + 检查点 |
| `omnipotent` | 全能型 | Hermes | **A2**（`REMOTE_TOOL` 闭环） | 开 | 高危确认；白名单受控变更可自动 |

- **A1**：规划输出 OPS 契约 → 无头执行 → 文本回灌  
- **A2**：结构化远端工具 → ACL/确认 → 执行 → 结构化回灌  

决议见 [ADR-0003](./adr/0003-remote-tools-and-ops-coexistence.md)、[ADR-0004](./adr/0004-mode-hierarchy.md)。

---

## 6. 请求主路径（掌上对话）

```text
mobile_im_demo.html
  → POST .../message/stream
  → MobileSessionOrchestrator.handle_message
       │
       ├─ efficient → planner_m0 →（确认卡?）→ headless_exec
       │
       ├─ intelligent → Hermes(plan_only)
       │       → 解析 OPS_* → A1 闭环 → 变更则确认卡 → 执行 → 回灌
       │
       └─ omnipotent → Hermes + remote_tools
               → 解析 REMOTE_TOOL → A2 闭环
               → 写/删/高危 → 确认卡 v2（风险色 / YES 二次确认 / AI 解读）
               → headless_exec → 回灌 → 多轮 / 检查点 / 熔断
  → append_mobile_audit / transcript / host_snapshot
  → SSE：delta / thought_delta / phase / done(+card)
```

确认卡用户操作：`POST .../permission/stream`（可带 `typed_confirm`、`ai_explanation_viewed`）。

---

## 7. `chibycore/` — 执行与平台能力

```text
chibycore/
├── ssh_oneshot.py · winrm_oneshot.py · local_oneshot.py
├── unified_executor_factory.py · executor_contract.py
├── execution_gateway.py · policy_engine.py · gate.py · risk_heuristic.py
├── closure_service.py · closure_retry_runner.py · closure_llm_*.py
├── healing/ · knowledge_hub/
├── llm_orchestrator.py · llm_providers.py · llm_config.py
├── redaction.py · audit_log.py · transcript.py
├── host_crypto.py · tasks.py · celery_config.py · …
└── rules/
```

| 子系统 | 作用 | 掌上是否强依赖 |
|--------|------|----------------|
| oneshot SSH/WinRM | 单次远程命令 | **是**（`headless_exec`） |
| 脱敏 redaction | 审计/展示去凭据 | **是** |
| 闭环 closure / 自愈 healing | 终端 NL 闭环与修复 | 主终端路径为主 |
| 策略网关 / 风险 | 变更门闩 | 部分启发式复用 |
| 知识库 knowledge_hub | `/api/kb` | 可选 |
| LLM 配置 | 模型提供方 | Hermes 外的 LLM 路径 |

---

## 8. `data/` 关键落盘

| 路径 | 说明 |
|------|------|
| `hosts.json` | 主机与凭据 |
| `hermes_bridge.yaml` | Hermes / 掌上 / remote_tools 配置 |
| `llm_config.json` · `llm_models.json` | LLM |
| `mobile_audit.jsonl` | 掌上审计 |
| `mobile_sessions/` | 会话状态 |
| `mobile_transcripts/` | 对话 transcript |
| `host_snapshots/` | 跨会话主机快照（Phase1） |
| `hermes_audit.jsonl` | Hermes 侧审计 |
| `ops.db` · `knowledge_hub.db` 等 | SQLite |
| `*.example.yaml` | ACL / IM / escalation 示例 |

---

## 9. 安全与护栏（功能视角）

统一模型见 **[TSM-A](./tsm-a-security-model.md)**（按强制强度：L1 软 / L2 硬 / L3 取证）。

| 机制 | TSM-A | 位置 | 作用 |
|------|-------|------|------|
| 确认卡 v2 | L1 | `confirm_card_meta` + Demo UI | 风险分级、解读、高危 YES |
| plan_only / 凭据铁律 | L2 底座 | `hermes_bridge.yaml` + `remote_tools` | Hermes 不持凭据、不本机执行 |
| 主机密文 / 脱敏 | L2 底座 | `host_crypto` + `redaction` | 可选 Fernet；审计/回灌脱敏 |
| SecretStore + ExecTicket | L2 | `chibycore/secret_store` + `exec_ticket` | 确认后短票核销；Vault 占位 |
| OTP（TOTP/Webhook） | L2 | `chibycore/otp` + 确认卡 | 默关；高危叠加 YES |
| SIEM 外送 | L3 | `chibycore/siem_sink` + audit 钩子 | Webhook/文件 + 重试队列 |
| ACL 可见主机 | 横切 | `mobile/acl.py` | 用户只能打白名单主机 |
| 熔断 | 横切 | `exec_guardrails.ExecBreakers` | 主机/Hermes 连续失败冷却；继续可半开 |
| 审计 JSONL + turn_id | L3 | `mobile/audit.py` | 可追溯决策与执行 |
| 写前备份 | 变更工程 | remote_devtools + 环境开关 | 变更可回滚预览 |

设计细节：[confirm-card-design.md](./confirm-card-design.md) · [tsm-a-security-model.md](./tsm-a-security-model.md)。

---

## 10. 测试与文档

| 区域 | 路径 |
|------|------|
| 测试 | `tests/test_*mobile*`、`test_*hermes*`、`test_confirm_card_meta.py`、`test_host_snapshot.py` … |
| 文档索引 | [index.md](./index.md) |
| 操作手册 | [mobile-ai-datacenter-ops-manual.md](./mobile-ai-datacenter-ops-manual.md) |
| 比赛缺口对照 | [ai-agent-contest-system-gap.md](./ai-agent-contest-system-gap.md) |

---

## 11. 与「比赛系统落地」交付物的对应（速查）

| 比赛交付物 | 当前仓库对应 |
|------------|--------------|
| Bridge | `headless_exec` + `chibycore` oneshot + `remote_tools` |
| 确认卡 + 审计 | `confirm_card_meta` + `PermissionCard` + `mobile_audit.jsonl` |
| ACP | `hermes_bridge`（Hermes ACP）；业务 L1 信封见缺口文档 |
| MCP Zabbix/Prometheus | **尚未作为官方 Adapter 落地** |
| 磁盘告警剧本 Demo | **专用场景包尚未落地**（能力上可用全能型手动演示） |

---

## 12. 一句话地图

```text
UI(terminal/web)
  → FastAPI(terminal/main + mobile/api)
      → Orchestrator(mobile)
          → Planner(rules | Hermes via hermes_bridge)
          → Safety(确认卡 / ACL / 熔断)
          → Exec(remote_tools → headless → chibycore oneshot)
          → Audit / Snapshot / Transcript(data/)
```
