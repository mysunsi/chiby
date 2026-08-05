# TSM-A · 面向生产环境 AI Agent 的三层安全执行模型

**全称**：Three-tier Security Model for Agents（TSM-A）  
**版本**：v1.3  
**日期**：2026-07-23  
**状态**：需求分析 + 开发设计规划；**T1+T2+T3 已落地**（OTP 可开关；SIEM Webhook/文件 + 重试；Vault 真对接仍为后续）  
**产品名**：掌上 AI 机房 / Assistant × Hermes

关联文档：

- [confirm-card-design.md](./confirm-card-design.md) — L1 确认卡 v2（范围 B 已落地）
- [adr/0003-remote-tools-and-ops-coexistence.md](./adr/0003-remote-tools-and-ops-coexistence.md) — 凭据铁律 / A2
- [mobile-modes-to-industrial-milestones.md](./mobile-modes-to-industrial-milestones.md) — 工业级里程碑
- [mobile-hermes-assistant-ha.md](./mobile-hermes-assistant-ha.md) — turn_id / 终态
- [omnipotent-task-state-machine.md](./omnipotent-task-state-machine.md) — 任务状态机（与 L3 可追溯衔接）
- [hermes-credential-profile-script-design.md](./hermes-credential-profile-script-design.md) — Profile / 凭据隔离（L2 设计草案）
- [product-focus-and-depth.md](./product-focus-and-depth.md) — 产品聚焦：信任与安全面优先
- [ai-agent-contest-system-gap.md](./ai-agent-contest-system-gap.md) — 比赛材料中的「三层护栏」表述

---

## 0. 一句话

**TSM-A** 是一种面向生产环境 AI Agent 的三层安全执行模型。  
**核心创新**：以「控制手段的强制强度」作为三层划分的第一维度——

| 层 | 强制强度 | 控制手段（目标形态） |
|----|----------|----------------------|
| **L1 应用层护栏** | 软控制（行为约定） | 确认卡 + AI 解读 + 高危二次确认 |
| **L2 密码层护栏** | 硬控制（密码学 / 隔离） | 凭据保险箱 + 动态口令 + 短时效凭证 |
| **L3 记录层护栏** | 取证控制（事后不可抵赖） | 全量 JSONL 审计 + `trace_id`/`turn_id` 贯通 + 操作可重现 |

L1 可被 Prompt Injection **理论上**影响；L2 使 Agent **拿不到**原始凭据，无法靠话术绕过；L3 **无法绕过**且可对接 SIEM。三层互补，而不是互相替代。

---

## 1. 问题与动机

### 1.1 生产 Agent 的独特风险

传统应用安全（鉴权、WAF、RBAC）不足以覆盖「自然语言驱动的远程变更」：

1. **意图模糊**：模型可能误读「继续」为批准高危变更。  
2. **工具面放大**：一次 `REMOTE_TOOL` / `OPS_PLAN` 即可改配置、删文件、停服务。  
3. **人机混合决策**：最终责任在人，但人容易在疲劳或演示压力下点「允许」。  
4. **事后说不清**：无贯通 ID 与脱敏审计时，事故复盘成本极高。

### 1.2 为何按「强制强度」分层（而非按「技术栈」）

常见分层（网络 / 主机 / 应用）对 Agent 叙事不直观。TSM-A 用运维与安全都能听懂的一句话：

> **软拦一层、硬隔一层、账记一层。**

| 维度 | 传统分层 | TSM-A |
|------|----------|--------|
| 第一划分标准 | 部署位置 / OSI | **强制强度**（软 / 硬 / 取证） |
| 绕过叙事 | 「漏洞」 | 「Prompt 能否绕、凭据能否拿、账能否赖」 |
| 产品可讲性 | 偏基建 | 适合 Demo、投标、开源边界说明 |

### 1.3 与现有能力的关系（基线，2026-07）

| 层 | 已落地（基线） | 缺口（相对目标形态） |
|----|----------------|----------------------|
| **L1** | 确认卡 v2、风险色、点击 AI 解读、高危 `YES`、ACL 超时；**高危禁短句代批**；preamble 强调只认卡；**「记住此类」scoped（默关）**；**批量确认勾选**；**IM 富卡三层对齐** | 批量确认进阶文案；IM 解读按钮深化 |
| **L2** | `host_id`→Assistant；禁工具带密；Fernet；**SecretStore + ExecTicket**；**OTP 可开关** | Vault 真对接；OTP 企业 IdP 深化 |
| **L3** | JSONL + 脱敏；`turn_id`/`trace_id`；Forensic API；任务态；**SIEM Webhook/文件+重试**；**只读半自动重放**；**冷归档** | syslog；租户级留存 UI |
| **L3** | `mobile_audit.jsonl` + 脱敏；`turn_id`/`trace_id` 贯通；timeline；**Forensic Bundle API**；任务态 | SIEM 对接；跨会话留存策略；变更「只读可重跑」深化 |

**对外表述建议（诚实）**：

> L1 已可用于生产演示与试用；L2/L3 已具备隔离与审计底座，保险箱·OTP·短凭证·SIEM·强重现为规划阶段能力。

---

## 2. 目标与非目标

### 2.1 目标

| ID | 目标 | 度量 |
|----|------|------|
| G1 | 变更默认「人知情后执行」 | 写/删/高危路径 100% 经 L1；高危 100% 二次确认 |
| G2 | Agent 永不持有长期原始凭据 | 工具 schema / 回灌 / 审计中零明文密码与私钥 |
| G3 | 任一生产动作可在 5 分钟内按 ID 定位 | 给定 `turn_id`/`trace_id` 可拉齐意图→确认→命令→结果 |
| G4 | 三层可单独讲解、可单独验收 | 每层有 FR + 出门门槛勾选表 |
| G5 | 与开源边界一致 | L1 确认卡/护栏规则宜作开源亮点；L2 生产保险箱与企业适配可闭源 |

### 2.2 非目标（本期明确不做）

- 用 L1 替代操作系统权限或堡垒机。  
- 让 Hermes 直连生产私钥（违背 ADR-0003）。  
- 「完全无人值守且无 L1」作为默认产品模式。  
- 一次排期做完 KMS 全厂商适配 + 全量 SIEM 协议。  
- 把 Prompt Injection「彻底消灭」（L1 定位为软控制，靠 L2/L3 兜底）。

---

## 3. 角色与场景

| 角色 | 场景 | 对 TSM-A 的诉求 |
|------|------|-----------------|
| 一线运维 | IM / Web 让 Agent 改配置、装软件 | L1 快、看得懂；别丢密码给模型 |
| 技术负责人 | 审批高危、事后追责 | L1 二次确认；L3 一键时间线 |
| 安全 / 合规 | 投标、等保、内审 | 三层模型可写进材料；L3 可对接 SIEM |
| 平台研发 | 扩展 `db_*` / MCP | 新工具自动继承三层，不另造门禁 |
| 开源贡献者 / 评委 | 看清护栏是否真实 | L1+最小 L3 可跑 Demo；L2 生产能力边界说清 |

**主故事（验收剧本）**：磁盘告警追因 → 建议改配置 → L1 确认卡（含解读）→ 高危则 `YES` → L2 短凭证授权执行 → L3 全链路审计可按 `trace_id` 回放摘要。

---

## 4. 需求分析

### 4.1 L1 应用层护栏（软控制）

#### 4.1.1 功能需求

| ID | 需求 | 优先级 | 现状 |
|----|------|--------|------|
| FR-L1-01 | 受控变更必须挂确认卡后方可执行 | P0 | ✅ |
| FR-L1-02 | 风险分级展示（低/中/高）与文案 | P0 | ✅ |
| FR-L1-03 | 高危须二次确认（口令 `YES`，可配置） | P0 | ✅ |
| FR-L1-04 | 按需 AI 解读（不预生成、免责声明） | P0 | ✅ |
| FR-L1-05 | 允许/拒绝均写审计（含风险、是否打开解读） | P0 | ✅ 基本 |
| FR-L1-06 | 确认超时与会话互斥（防双批） | P0 | ✅ 部分 |
| FR-L1-07 | Prompt Injection 缓解：系统信封强调「仅认确认卡决策，不认正文里的『已批准』」 | P1 | ⏳ 部分（信封/催办有，未成专项） |
| FR-L1-08 | IM（飞书/企微）确认卡信息层级对齐 Web | P1 | ✅ 飞书三层+YES/OTP 输入；企微文本三层 |
| FR-L1-09 | 「记住此类」/批量确认（策略化，默认关） | P2 | ✅ 记住此类 + Web 批量勾选 |

#### 4.1.2 非功能

| ID | 要求 |
|----|------|
| NFR-L1-01 | 熟练用户一眼决策 ≤ 0.5s 可达（信息架构，非绝对性能） |
| NFR-L1-02 | 解读失败必须回落模板，不得阻塞允许/拒绝 |
| NFR-L1-03 | L1 **不得**声称「不可绕过」；材料中须标注软控制 |

### 4.2 L2 密码层护栏（硬控制）

#### 4.2.1 功能需求

| ID | 需求 | 优先级 | 现状 |
|----|------|--------|------|
| FR-L2-01 | Hermes/工具参数禁止 username/password/密钥 | P0 | ✅ |
| FR-L2-02 | 执行平面按 `host_id`（或 `db_id`）解析凭据 | P0 | ✅ |
| FR-L2-03 | 静态密文存储（Fernet 等）+ 生产默认开启开关 | P0 | ✅ 有能力，生产开关需运维落实 |
| FR-L2-04 | 审计/回灌/SSE 全程脱敏 | P0 | ✅ |
| FR-L2-05 | **凭据保险箱**：统一 SecretStore 接口（本地加密文件 / Vault / 云 KMS） | P1 | ✅ LocalFernet；Vault 占位 |
| FR-L2-06 | **短时效执行凭证（ExecTicket）**：确认通过后签发 TTL 凭证，仅覆盖本 `turn_id`+主机+命令哈希 | P1 | ✅ |
| FR-L2-07 | **动态口令**：高危批准可要求 TOTP / 企业 OTP（与 `YES` 叠加或升级） | P1 | ✅ 默关；可开 |
| FR-L2-08 | 密钥轮换与 Profile `doctor`/`rotate` | P2 | ⏳ rehearsal doctor 已挂 L2 |
| FR-L2-09 | 凭证吊销：用户拒绝/停止/超时立即作废 ExecTicket | P1 | ✅ `revoke_exec_ticket` API；拒绝路径可接 |

#### 4.2.2 非功能

| ID | 要求 |
|----|------|
| NFR-L2-01 | Agent 进程内存与工具 JSON **不可**出现长期明文密码（短时效解密仅在执行器侧、最短窗口） |
| NFR-L2-02 | ExecTicket TTL 默认 ≤ 120s，可配置；过期必须失败且可审计 |
| NFR-L2-03 | Vault 不可达时：失败安全（拒绝执行），可选只读探测降级策略需显式配置 |

### 4.3 L3 记录层护栏（取证控制）

#### 4.3.1 功能需求

| ID | 需求 | 优先级 | 现状 |
|----|------|--------|------|
| FR-L3-01 | 全量安全相关事件 JSONL（确认、执行、拒绝、熔断、催办、任务态） | P0 | ✅ 主体 |
| FR-L3-02 | `turn_id` 贯通用户消息→规划→确认→执行→回灌→结案 | P0 | ✅ |
| FR-L3-03 | 对外统一暴露 `trace_id`（可与 `turn_id` 同值或父子关系） | P1 | ⏳ 内部多用 turn_id |
| FR-L3-04 | 按 turn/host/conversation 检索与时间线 API | P0 | ✅ 基本 |
| FR-L3-05 | **操作可重现**：给定 trace 输出「意图摘要 + 批准记录 + 命令列表 + 出口码/摘要」的取证包；只读半自动重放 | P1 | ✅ Bundle + replay API |
| FR-L3-06 | 任务终态显式（`task_phase`/`end_reason`，禁假完成） | P0 | ✅ P0/P1 状态机 |
| FR-L3-07 | **SIEM 导出**：Webhook / syslog / 文件尾随（JSON Lines） | P1 | ✅ Webhook + 文件；syslog 未做 |
| FR-L3-08 | 留存与归档策略（热数据 N 天、冷归档） | P2 | ✅ 热窗口+按月冷归档 |

#### 4.3.2 非功能

| ID | 要求 |
|----|------|
| NFR-L3-01 | 审计写入失败不得静默丢事件（至少打 ERROR + 内存环形缓冲告警） |
| NFR-L3-02 | 审计载荷必须脱敏；禁止「为了可重现而把密码写入 JSONL」 |
| NFR-L3-03 | SIEM 通道失败不影响主执行路径（异步、可重试） |

### 4.4 跨层需求

| ID | 需求 | 说明 |
|----|------|------|
| FR-X-01 | 新工具族（如 `db_*`）默认挂接三层 | 确认策略、凭据解析、审计事件模板复用 |
| FR-X-02 | 材料与 UI 术语统一为 TSM-A / L1·L2·L3 | Demo、缺口文档、开源 README |
| FR-X-03 | 模式策略表：高效型 / 智能型 / 全能型各自 L1 触发密度 | 已有政策函数，文档化进 TSM-A |
| FR-X-04 | 开源包暴露 L1 + 最小 L3；L2 生产适配器可闭源 | 对齐 open-source-boundary-review |

---

## 5. 架构设计

### 5.1 逻辑视图

```text
用户 / IM / Web
        │
        ▼
┌─────────────────── L1 应用层护栏 ───────────────────┐
│  风险判定 → PermissionCard → (可选) AI 解读        │
│  高危 → YES / OTP 门槛 → allow_once | deny         │
└────────────────────────┬────────────────────────────┘
                         │ 批准
                         ▼
┌─────────────────── L2 密码层护栏 ───────────────────┐
│  SecretStore(host_id) → 可选签发 ExecTicket(TTL)   │
│  执行器持票调用 SSH/WinRM；Hermes 永不持长期密钥     │
└────────────────────────┬────────────────────────────┘
                         │ 结果（已脱敏）
                         ▼
┌─────────────────── L3 记录层护栏 ───────────────────┐
│  append_mobile_audit / transcript / task_status     │
│  turn_id(=trace_id) 贯通 → 时间线 / 取证包 / SIEM   │
└─────────────────────────────────────────────────────┘
```

### 5.2 与现有模块映射

| TSM-A | 代码 / 配置锚点 |
|-------|-----------------|
| L1 | `confirm_card_meta.py`、`PermissionCard`、`orchestrator` 审批路径、`mobile_im_demo.html` |
| L2 | `hosts.json` + `host_crypto` + **`chibycore/secret_store.py`** + **`chibycore/exec_ticket.py`**；确认卡 allow → 签发短票 → `_run_exec` 核销 |
| L3 | `terminal/mobile/audit.py`、`turn_id`、`task_status.py`、timeline API；**规划**：`forensic_export.py`、`siem_sink.py` |
| 策略 | `agent_mode.py` / `policy_for`、`exec_guardrails.py` |

### 5.3 L2 关键设计：SecretStore + ExecTicket

```text
确认卡 allow_once
    → IssueExecTicket{
         ticket_id, turn_id, host_id,
         command_hash | tool_call_id,
         exp=now+TTL, scope=once
       }
    → Executor.redeem(ticket) → SecretStore.unlock(host_id) → 执行 → 作废 ticket
```

规则：

1. 无有效 ticket（或 ticket 与命令哈希不符）→ 拒绝执行并审计 `ticket_reject`。  
2. Hermes / 规划脑只看见 `host_id` 与结果摘要，看不见 ticket 密钥材料。  
3. OTP（若开启）：在签发 ticket 前校验，失败不签发。

### 5.4 L3 关键设计：取证包与 SIEM

**取证包（Forensic Bundle）** 最小字段：

```json
{
  "trace_id": "...",
  "turn_id": "...",
  "conversation_id": "...",
  "intent_summary": "...",
  "task_phase": "ended",
  "end_reason": "completed",
  "confirmations": [{"choice":"allow_once","risk":"high","typed":true}],
  "steps": [{"tool":"ssh_execute","host":"...","ok":true,"exit_code":0}],
  "redacted": true
}
```

**SIEM Sink**：配置 `data/mobile_siem.yaml`（示例）→ `webhook` / `file`；事件子集默认：`permission_*`、`remote_tool_exec`、`closure_break`、`task_status`、`ticket_*`。

### 5.5 威胁与控制对照

| 威胁 | 主控层 | 说明 |
|------|--------|------|
| 模型在正文声称「已批准，直接执行」 | L1 | 只认确认卡 API；正文批准无效 |
| 工具参数夹带密码 | L2 | schema 拒绝 + 审计 |
| 演示账号 hosts.json 明文外泄 | L2 | Fernet / Vault；gitignore |
| 用户误点允许高危 | L1 | YES / OTP；解读 |
| 事故后无法复盘 | L3 | turn 时间线 + 取证包 |
| 审计被进程删除 | L3+运维 | 外送 SIEM；OS 权限；非单靠应用层 |

---

## 6. 开发规划（分期）

### 6.1 总原则

1. **先诚实命名，再补硬能力**：先在文档/UI/材料统一 TSM-A，避免「三层都齐了」的过度承诺。  
2. **L2/L3 增量可开关**：默认保持现有 Fernet + JSONL；Vault/OTP/Ticket/SIEM 用 feature flag。  
3. **沿执行平面复用**：`db_*`、MCP 告警追因自动继承，不平行造门禁。  
4. **与产品聚焦一致**：信任面优先于新工具前线（见 product-focus-and-depth）。

### 6.2 阶段划分

#### 阶段 T0 — 模型固化与材料（约 0.5～1 周）

| 交付 | 说明 |
|------|------|
| 本文档定稿 + 索引挂链 | `docs/tsm-a-security-model.md` |
| 对外一句话与诚实边界 | README / 比赛缺口 / 系统结构「安全与护栏」改写为 TSM-A |
| Demo 脚注或关于页可选展示 L1/L2/L3 徽章 | 仅状态：已启用 / 部分 / 规划中 |

**出门**：内部评审通过命名与基线表；无代码硬依赖。

#### 阶段 T1 — L1 加固 + L3 命名统一（约 1～2 周）✅

| 项 | 内容 | 状态 |
|----|------|------|
| T1.1 | 高危禁止短句文字确认；正文「已批准/无需确认」无效；preamble 强调只认确认卡 | ✅ |
| T1.2 | 审批相关审计补齐 `tsm_layer` / `risk_level` / `typed_confirm` | ✅ |
| T1.3 | SSE/meta `trace_id` = `turn_id` 别名 | ✅ |
| T1.4 | `GET /api/mobile/demo/forensic` 取证包 v0 | ✅ |

实现锚点：`terminal/mobile/tsm.py`、`orchestrator` 审批与短确认路径、`api.py` forensic、`tests/test_tsm_t1.py`。

**出门**：给定一次高危确认，能导出脱敏取证 JSON；材料可写「L1 完整 + L3 可导出」。

#### 阶段 T2 — L2 短时效凭证 + 保险箱接口（约 2～4 周）✅

| 项 | 内容 | 状态 |
|----|------|------|
| T2.1 | `SecretStore`：`LocalFernet` + Vault 占位（失败安全） | ✅ |
| T2.2 | `ExecTicket` 签发/核销；确认卡 allow / repair apply / remote_batch 挂票 | ✅ |
| T2.3 | rehearsal / status doctor：`OPS_ENCRYPT_HOST_SECRETS`、短票 enforcement | ✅ |
| T2.4 | 单测：缺票/过期/哈希不符/吊销 | ✅ |

开关：`OPS_TSM_EXEC_TICKET`（默认 `1`）、`OPS_TSM_EXEC_TICKET_TTL`（默认 120）、`OPS_TSM_SECRET_STORE=local|vault`、`OPS_ENCRYPT_HOST_SECRETS=1`。  
实现：`chibycore/secret_store.py`、`chibycore/exec_ticket.py`、`tests/test_tsm_t2.py`。

**出门**：确认卡批准后「短票核销才执行」；Demo 可讲批准才发短票。

#### 阶段 T3 — 动态口令 + SIEM（约 2～3 周）✅

| 项 | 内容 | 状态 |
|----|------|------|
| T3.1 | 高危可配 `require_otp`；本地 TOTP + 企业 Webhook 占位 | ✅ |
| T3.2 | SIEM Webhook / 文件 sink + 重试队列；audit 钩子异步外送 | ✅ |
| T3.3 | 运维手册 [tsm-a-ops-runbook.md](./tsm-a-ops-runbook.md) | ✅ |

开关：`OPS_TSM_REQUIRE_OTP`、`OPS_TSM_OTP_SECRET` / `OPS_TSM_OTP_WEBHOOK`；`OPS_TSM_SIEM_ENABLED`、`OPS_TSM_SIEM_WEBHOOK`、`OPS_TSM_SIEM_FILE`；示例 `data/mobile_siem.example.yaml`。  
实现：`chibycore/otp.py`、`chibycore/siem_sink.py`、`tests/test_tsm_t3.py`。

**出门**：投标材料可写满三层且每格有实现或明确开关。

#### 阶段 T4 — 深化（按需）

| 项 | 内容 | 状态 |
|----|------|------|
| T4.1 | 「记住此类」scoped（仅 low/medium；高危/OTP/结构化工具不跳过）；`OPS_TSM_REMEMBER_CONFIRM` 默关 | ✅ |
| T4.2 | 取证半自动重放 `POST /api/mobile/demo/forensic/replay`（只读可跑；变更 display_only） | ✅ |
| T4.3 | 审计冷归档 `POST /api/mobile/demo/audit/archive`；`OPS_TSM_AUDIT_HOT_DAYS` | ✅ |
| T4.4 | Web 批量确认勾选（`command_items` + `selected_indices`） | ✅ |
| T4.5 | IM 富卡三层对齐（飞书 interactive + YES/OTP 表单；企微文本卡） | ✅ |
| T4.余 | syslog；租户级留存 UI；IM「AI 解读」原生按钮 | ⏳ |

开关：`OPS_TSM_REMEMBER_CONFIRM`、`OPS_TSM_REMEMBER_TTL_HOURS`、`OPS_TSM_AUDIT_HOT_DAYS`。  
实现：`confirm_pref.py`、`audit_archive.py`、`tsm.py` replay helpers、`tests/test_tsm_t4.py`。

### 6.3 建议排期示意

```text
T0 模型与材料 ──► T1 L1加固+L3导出 ──► T2 ExecTicket+SecretStore ──► T3 OTP+SIEM
     (并行可做：db_* 只读仍挂现有 L1/L3，不阻塞 T2)
```

与「全能型任务状态机 P2 task_id」可并行：状态机服务 L3 可解释终态，不替代 TSM-A。

### 6.4 人力与依赖（粗估）

| 阶段 | 工程重点 | 依赖 |
|------|----------|------|
| T0 | 文档 / 产品 | 无 |
| T1 | 编排 + API + 少量 UI | 现有 audit/timeline |
| T2 | `chibycore` 新模块 + 执行器改造 | 主机表迁移；可选 Vault 环境 |
| T3 | 安全配置 + 集成 | OTP 种子管理；SIEM 地址 |

---

## 7. 验收与测试

### 7.1 分层验收用例（摘要）

| 用例 | 期望 |
|------|------|
| L1-U1 中危写文件 | 出确认卡；无卡则不执行 |
| L1-U2 高危删除 | 须 `YES`；错误口令拒绝 |
| L1-U3 解读 | 点击才请求；失败有模板 |
| L2-U1 工具带 password | 解析拒绝，审计 `forbidden_param` |
| L2-U2（T2）无 ticket 执行 | 拒绝 |
| L2-U3（T2）ticket 过期 | 拒绝 |
| L3-U1 一轮闭环 | 同 `turn_id` 可检索确认+执行 |
| L3-U2 假完成 | `protocol_incomplete` 不得显示「已完成」 |
| L3-U3（T1）取证包 | 无明文密码字段 |

### 7.2 回归锚点

- `tests/test_confirm_card_meta.py`  
- `tests/test_ops_autorun_confirm.py`  
- `tests/test_redaction.py`  
- `tests/test_task_status.py`  
- 新增：`tests/test_tsm_exec_ticket.py`、`tests/test_forensic_bundle.py`（随 T1/T2）

---

## 8. 文档与对外表述模板

**短版（PPT / 封面）**：

> TSM-A：以强制强度划分的 Agent 三层安全执行模型——L1 确认与解读（软）、L2 凭据与短凭证（硬）、L3 审计与可重现（取证）。

**诚实版（技术方案）**：

> 当前交付：L1 完整可用；L2 完成凭据隔离与可选密文存储，保险箱/OTP/短时效凭证按 T2–T3 规划；L3 具备 JSONL 与 turn 贯通，取证包与 SIEM 按 T1/T3 规划。

**禁止表述**：

- 「三层均已不可绕过」  
- 「Prompt Injection 已根治」  
- 「Agent 持有动态口令等同于已上 Vault」（未接线前）

---

## 9. 风险与开放问题

| 风险 | 缓解 |
|------|------|
| ExecTicket 增加执行延迟与复杂度 | TTL 与缓存仅在执行器；feature flag 灰度 |
| OTP 伤害演示流畅度 | 仅高危；Demo 环境可关 |
| Vault 运维成本 | LocalFernet 作为默认；Vault 为企业适配器 |
| 取证「可重现」被理解为自动重放变更 | 文档明确：变更只展示，只读可选手动重跑 |
| 命名与旧稿「三层风险管控」混淆 | 本文为 Agent 强制强度模型；旧 design-ssh 文档加互指 |

**开放问题**：

1. `trace_id` 是否全局唯一跨进程（需 ULID/UUID 规范）？  
2. OTP 首期：本地 TOTP 还是只做「企业 Webhook 校验」占位？  
3. 开源仓是否包含 ExecTicket 实现，或仅 Protocol + mock Store？

---

## 10. 决策记录（初稿）

| 决策 | 结论 |
|------|------|
| 分层第一维度 | **强制强度**（软 / 硬 / 取证） |
| 品牌名 | **TSM-A**（Three-tier Security Model for Agents） |
| L1 二次确认口令 | 保持 `YES`；OTP 为叠加而非替换（可配） |
| L2 第一刀产品化 | ExecTicket + SecretStore 接口，先于全量 Vault UI |
| L3 第一刀 | Forensic Bundle + `trace_id` 别名，先于 SIEM |
| 与任务状态机 | 终态字段进入 L3 取证包；不并入 L1 |

---

## 11. 修订历史

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-07-23 | v1.5 | **T4.4/T4.5**：Web 批量确认勾选；IM 飞书/企微富卡三层对齐 |
| 2026-07-23 | v1.4 | **T4 落地**：记住此类、取证只读重放、审计冷归档 |
| 2026-07-23 | v1.3 | **T3 落地**：OTP（TOTP/Webhook）、SIEM sink+重试、运维手册 |
| 2026-07-23 | v1.2 | **T2 落地**：SecretStore + ExecTicket；确认路径核销；doctor |
| 2026-07-23 | v1.1 | **T1 落地**：L1 卡 API 强制、审计 TSM 字段、`trace_id`、`/api/mobile/demo/forensic` |
| 2026-07-23 | v1.0 | 首版：命名、需求、架构、T0–T4 规划；对照现网基线 |
