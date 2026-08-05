# 开源边界评审意见（切分执行约束）

版本：v1.4  
日期：2026-08-04  
状态：**切分执行约束**（对齐产品架构；非正式仓已抽出）

对照产品分层（定稿草案）：[oss-pro-saas-architecture.md](./oss-pro-saas-architecture.md)  
对照结构：[system-code-structure.md](./system-code-structure.md)  
对照缺口：[ai-agent-contest-system-gap.md](./ai-agent-contest-system-gap.md)  
对照安全：[tsm-a-security-model.md](./tsm-a-security-model.md)  

**命名**：开源执行门面统一称 **`ops-bridge` / `chibyterm`**（Git 公开面）/ **`ops_bridge`**（Python 包，本地可放 `packages/ops_bridge/`）。  
**产品品牌**：**Chiby（赤壁）**（≠ 给 Hermes 改名）。  
**Pro 闭源核心**：C++ **`pro_core`**（Chiby 中枢门闸 + 许可 + 专利）可选；现网过渡期闭源以 **Python 包**（`chiby_hermes_bridge` / `chiby_mobile`）为主。上游 **Hermes Agent 为 MIT**（Nous Research），可作运行时依赖并保留声明——**勿叙事成「整棵 Hermes 归我司闭源」**。详见 [oss-pro-saas-architecture.md](./oss-pro-saas-architecture.md) §0。

---

## 0.1 决议锁定（2026-08-04）

| 议题 | 决议 |
|------|------|
| 开源核范围 | **最新 Web/ops 终端全量**，含 **闭环治理 + KnowledgeHub + DocHub**（社区可见完整终端价值） |
| 闭源范围 | **掌上 AI 机房**（`terminal/mobile/**`）+ **Hermes/Chiby ACP 桥**（`hermes_bridge/**`、智能型/全能型中枢胶水） |
| 仓形态 | **Monorepo 过渡**：先 `packages/` + `proprietary/`；公开镜像再滤 proprietary（P2） |
| 顺序 | **绝对先拆代码（P0）**，再考虑多仓/公开镜像（P2）；禁止「先拆仓、代码仍互 import」 |

**修订公式（相对 v1.3）**

- 开源 ≠ 仅「薄 Demo + 最小 SSH」；开源 = **现网终端主链能力（去掌上 / 去 Hermes 桥）**。  
- 仍禁止：开源 import 闭源；`main` 硬挂 mobile/hermes；原样勾选文件即发版。  

---

## 0. 与最新状态的关系（先读）

| 文档 | 管什么 | 本文件角色 |
|------|--------|------------|
| [oss-pro-saas-architecture.md](./oss-pro-saas-architecture.md) | 产品三层：开源终端 / Pro / 掌上 SaaS；C++ 边界 | **产品定稿** |
| **本文** | 从现网单体仓**怎么切**才不违反依赖单向；Demo 如何自洽 | **切分执行约束**（v1.0 评审结论仍成立，已按 v1.1 架构改写闭源侧表述） |

**v1.0 → v1.1 变更摘要**

- 闭源侧由「整份 Python orchestrator/Hermes」升级为：**决策与 Hermes 交互进 C++ `pro_core`**；Python Pro 为 shim + 企业辅模块。  
- 开源亮点对齐已落地的 **TSM-A**（确认卡 / YES / 审计 / ExecTicket 等叙事），但**切仓仍未做**——现网仍是单体。  
- 明确：掌上 `terminal/mobile/*` 现网用于孵化 Demo；交付上归 SaaS/Pro，开源仓只留自洽 Demo 薄层。  
- 文件级「拟开源硬依赖拟闭源」矛盾**仍然成立**，不可用「架构写清楚了」代替重构。

---

## 1. 总评

| 维度 | 评价 |
|------|------|
| 产品方向 | **已定稿**：开源 ops-bridge → Pro（`pro_core`）→ 掌上 SaaS；见架构文档 |
| 可执行性 | **「按文件原样搬运」仍不可行**——多处开源候选硬依赖编排/oneshot |
| Demo 故事 | **仍冲突**：5 分钟体验依赖编排与 HTTP API；开源仓必须自带 Demo Orchestrator/API |
| 诚实度 | 材料写「抽取 + Facade + Demo 薄层 + Pro C++ 核心」，勿写「直接勾选现仓文件即开源」 |
| Stars 等 | 营销叙事，勿写入技术规格 |

**一句话**：产品分层已对齐；切分清单仍须按 **import 图重画**，并补开源 **Demo Orchestrator + Demo API**，否则「可复现」不成立。

---

## 2. 原则层：必须坚持

1. **依赖单向**：开源不 import 闭源；Pro/SaaS pip 或服务依赖开源。  
2. **最小开源 + 诚实边界**：有限度开源；全能型/写工具全量/许可不进开源仓。  
3. **生产 oneshot / 网关 / 知识库 / IM / Job / 掌上 SaaS 后端闭源**。  
4. **开源 `ssh_minimal` / `winrm_minimal`，企业 Backend 插件挂 Protocol**。  
5. **MCP + disk_alert 剧本**可新建开源（缺口 S4/S5）。  
6. **ACP 分 L0/L1**（缺口 S2）。  
7. **Pro 决策进 C++ `pro_core`**：许可与 `omni_*` 同信任边界，禁止「删 Python 绕过全能型」。  

---

## 3. 致命矛盾（切仓前必须先改）

### 3.1 「开源文件」实际依赖「闭源模块」

| 拟开源文件 | 现状依赖（抽样） | 冲突 |
|------------|------------------|------|
| `headless_exec.py` | `chibycore.unified_executor_factory`、`RunOptions` | 工厂与 oneshot 拟闭源 → **不能原样开源** |
| `confirm_card_meta.py` | 惰性 import `orchestrator` 风险正则；可选 `chibycore.risk_heuristic` | 须把风险规则抽到开源 `guardrails` |
| `audit.py` | `chibycore.redaction.redact_payload` | 须开源最小脱敏或内联 stub |
| `session_store.py` | 反序列化 import `PendingPermission` / `ConversationState` | 状态模型下沉开源 `models` |
| `remote_tools.py` | 确认判定耦合 orchestrator | 拆协议/判定到开源契约层 |
| `mobile_im_demo.html` | `/api/mobile/demo/*` | 开源须有 `demo_api`，或页面改对接 Demo 端点 |

结论：清单不是「勾选现有文件」，而是 **抽接口 + 重写薄实现 + 开源 Demo 编排**。

### 3.2 5 分钟 Demo 与闭源编排冲突

体验路径：自然语言 → 确认卡 → 执行 → 报告 → 审计页。

若完整编排 / Hermes / 现网 `api.py` 全部闭源，仅靠 `planner_m0` **无法**在浏览器跑通。

**修订要求（P0）**：开源仓增加：

- `demo/demo_orchestrator.py`：固定 DAG / 轻量规则循环（**无 Hermes、无 `pro_core`**）  
- `demo/demo_api.py`：最小 FastAPI（message / permission / audit）  
- `demo_runner.py`：CLI 无头跑通同一剧本  

闭源路径：

- **Pro**：Python shim → C++ `pro_core`（`omni_*` + `license_*`）→ Hermes Worker；pip 复用开源 confirm/audit/executor  
- **掌上 SaaS**：独立后端，不依赖本地 ops-bridge 安装  

### 3.3 「原样开源 remote_tools + headless」工作量被低估

| 建议 | 内容 |
|------|------|
| 开源 | 工具**契约** + **Demo 子集**（只读为主；至多一条演示变更） |
| 闭源 / Pro | 全量工具面；写工具放行门闸在 **`pro_core`** |

材料：「完整工具套件在商业版」；开源：「可扩展协议 + 演示实现」。

---

## 4. 分模块修订建议

### 4.1 开源或拆出后开源

| 项 | 建议 | 理由 |
|----|------|------|
| 风险/变更判定正则 | → `ops-bridge/guardrails` | 解耦 confirm ↔ orchestrator |
| `PendingPermission` 等 | → 开源 `models` | 解耦 session_store |
| 最小 `redaction` | 开源 stub | 解耦 audit |
| **Demo Orchestrator + Demo API** | **新增开源** | 可复现 |
| `executor` Protocol + minimal SSH/WinRM | 开源 | Facade |
| 审计样例 + query CLI | 开源 | 证据链；链式哈希属 Pro 专利，不开源 |
| TSM-A L1 确认卡 / YES / 解读模板 | 开源亮点 | 与现网一致 |
| TSM-A L3 基础 JSONL + `trace_id` | 开源 | forensic/链式增强归 Pro |
| `acp-spec` L0 Schema（文档级抽取） | 开源 | 非仅 `acp_wire` 常量 |
| `planner_m0` + `agent_mode`（指令型/分析型） | 开源 | **不含**全能型 |

### 4.2 保持闭源（对齐 2026-08 决议）

| 项 | 落点 |
|----|------|
| 全能型 ↔ Hermes 交互核心、许可、三专利 | **`pro_core`（远期）** / 过渡期 **`chiby_hermes_bridge`** |
| 掌上编排 / 回灌 / Job / IM / escalation | **`chiby_mobile` / 掌上 SaaS** |
| `repair_txn`、全量 `acl`、`job_*`、`im/` | Pro / 掌上 SaaS |
| `hermes_bridge` spawn/session/worker、`hermes_ws` | **proprietary `chiby_hermes_bridge`**（禁止开源仓打包） |
| 企业 healing / 堡垒机 / 计费 | Pro / SaaS |
| 真实 `hosts.json`、凭据、生产 compose | 不进开源 |

> **已改入开源核（相对 v1.3）**：终端 oneshot / `execution_gateway` / `knowledge_hub` / `doc_hub` / `llm_*` / 闭环治理 REST。

### 4.3 边界微调（2026-08）

| 草案/旧说 | 现建议 |
|-----------|--------|
| 开源仅薄 Demo | **开源 = 最新终端全量**（闭环 + KB/Doc）；掌上 Demo 页闭源 |
| 整份 `remote_tools` 开源 | 终端侧工具契约 + 社区插件；**掌上 A2 全量编排闭源** |
| 主站 `index.html` 可选 | **开源**（Hermes Tab 可保留「未安装中枢」壳） |
| `hermes_protocol` 全闭源 | 开源可抽契约文档；完整续接在桥包 |
| 先拆仓再拆代码 | **禁止**；必须先 P0 代码解耦 |

### 4.4 `acp-spec` / `mcp-adapters`

- ACP：L0/L1 + mapping + SDK + jsonl；L1 与 SSE/`PermissionCard` 字段对照表可执行。  
- MCP：fixture 刚需；写清与 ops-bridge Demo 的集成路径（优先最简）。

---

## 5. 推荐仓职责（Monorepo 过渡 · 2026-08）

```text
Assistant/   # 内网 monorepo（开发主仓）
  packages/
    chibycore/           # 开源：执行/闭环/KB/Doc/LLM
    chibyterm/       # 开源：最新 Web 终端（默认入口）
  proprietary/          # 不进公开镜像
    chiby_hermes_bridge/
    chiby_mobile/
  terminal/ …           # P0 期间可仍为源码位置；移动按入口插件化

公开镜像（P2）：仅 packages/ops_* + docs 开源子集 + NOTICE（Hermes MIT）
闭源 wheel：pip install chiby-mobile chiby-hermes-bridge  # 依赖 chibyterm
```

依赖：`chiby_* → chibyterm/chibycore`；**禁止**开源 import proprietary。

---

## 6. 开源叙事评审（更新）

| 叙事点 | 意见 |
|--------|------|
| 最新终端全量（闭环 + KB/Doc）开源 | **已决议**；社区看到完整终端价值 |
| 掌上 / Hermes 桥闭源 | **正确**；安装闭源 wheel 后启用 |
| 确认卡三级 + YES + 解读 | 开源亮点（TSM-A L1） |
| 审计 `trace_id` / forensic | 基础开源；**链式哈希 / 专利 B 仅 Pro** |
| 「开源 import 闭源」 | **禁止** |
| 运维中枢 / 智能型·全能型闭源 | **正确**；上游 Hermes 保持 MIT 声明 |
| 先拆仓再拆代码 | **禁止**；先 P0 |

---

## 7. 风险清单

1. **重构量**远大于「复制 N 个文件」。  
2. **许可证与脱敏**：开源前清注释、内网主机名、客户痕迹。  
3. **semver**：`COMPATIBILITY.md` 锁开源 API；闭源锁 major。  
4. **测试**：开源路径在 **无 proprietary 包** 下必须绿。  
5. **过度承诺**：勿写「开源含全能型/掌上机房」。  
6. **泄露**：发布脚本勿 `COPY` 整仓；CI 过滤 `proprietary/`。

---

## 8. P0 执行清单（先拆代码 · 必须完成后再谈 P2）

验收：**默认零闭源包** 时，`uvicorn` 可跑 Web 终端 + SSH/WinRM + 闭环 + KB/Doc；`pytest -m "not proprietary"` 全绿；源码树无顶层 `import terminal.mobile` / `hermes_bridge`。

| # | 任务 | 完成标准 |
|---|------|----------|
| P0-1 | `main.py` 插件化注册 | `OPS_MOBILE_DEMO=0` / 无 bridge 包 → **不** `import`、**不**注册 `/api/mobile/*`、`/ws/hermes` |
| P0-2 | 抽出共享模型/护栏 | `PendingPermission`/`ExecResult`/风险正则 → `chibycore` 或 `ops_bridge.models`；mobile 改引用 |
| P0-3 | 断 `acp_worker → mobile` | preamble/工具白名单经 `RemoteToolRegistry` 注入；bridge 包可独立 |
| P0-4 | 断 `knowledge_orchestrator → mobile` | 直接调 `knowledge_hub`/`doc_hub` |
| P0-5 | 断 `main → orchestrator` 解读 | 开源 `llm_explain` 或 entry_point 可选闭源 |
| P0-6 | 配置开关 + entry_points | 开关短路；`chiby.plugins` 占位；`proprietary_plugins` 优先 EP 再 importlib |
| P0-7 | Monorepo 目录草创 | `packages/chibycore` + `packages/chibyterm`；`proprietary/chiby_*` 占位；**闭源真迁入 = P1** |
| P0-8 | CI / 脱敏 | `scripts/check_oss_boundary.py`；wheel exclude `mobile*` / `hermes_bridge*`；marker `proprietary` |

**P0-6/7/8 草创说明（2026-08-04）**：开源主体已 `svn move` 至 `packages/`；闭源真源暂仍在 `packages/chibyterm/mobile|hermes_bridge`，公开 wheel 通过 setuptools `exclude` 剔除；`proprietary/chiby_*` 为占位，P1 再物理迁入并改 import。

**P1 完成说明（2026-08-04）**：`mobile/` / `hermes_bridge/`（及 `hermes_ws` / `hermes_audit_api`）已物理迁入 `proprietary/chiby_*/src/`；独立 wheel + `chiby.plugins` entry_points；`packages/chibyterm` 不再含闭源子树。

**P2-1 完成说明（2026-08-04）**：根目录 `LICENSE`（Apache-2.0）、`NOTICE`、品牌更名版 `README.md` 快速开始、`CONTRIBUTING.md`；`pyproject.toml` license=`Apache-2.0`；门禁 `check_oss_boundary.py` 通过。

**包名统一（2026-08-04）**：`ops_terminal`/`ops-terminal` → **`chibyterm`**；`ops_core` → **`chibycore`**（SVN move + 全仓替换）。过渡别名：`terminal` / `ops_terminal` / `ops_core` 仍可导入。

**P2**：公开 Git 滤 proprietary；TestPyPI / PyPI（P2-3，包名 `chibyterm`）；可选独立掌上 SaaS 仓。

---

## 9. 一句话收

**产品品牌：Chiby（赤壁）**。**开源 = 最新终端全量（闭环 + KB/Doc）**；**闭源 = 掌上机房 + Hermes/Chiby 桥（中枢）**。**先 P0 代码解耦，再 P2 仓/镜像**。上游 Hermes 保持 MIT 声明。按此排期动刀。

---

## 10. 修订历史

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-08-04 | v1.4 | 锁定：终端全量开源、掌上/桥闭源、Monorepo 过渡、先 P0 后 P2；修订 §4.2/§5/§8 |
| 2026-07-23 | v1.3 | 固化产品名 Chiby（赤壁）；上游 Hermes 仍为技术依赖 |
| 2026-07-23 | v1.2 | 对齐架构 §0：上游 Hermes=MIT；闭源=自有中枢 |
| 2026-07-23 | v1.1 | 对齐 oss-pro-saas-architecture：`pro_core`、掌上 SaaS、TSM-A 现状；升格为切分执行约束 |
| 2026-07-22 | v1.0 | 首版：针对「精准文件级切分」草案的评审意见 |
