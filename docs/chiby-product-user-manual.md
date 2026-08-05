# Chiby（赤壁）产品全功能使用说明书

| 项 | 内容 |
|----|------|
| 产品 | **Chiby（赤壁）** — 运维智能中枢 / 通用 AI 助手宿主 |
| 文档版本 | **v1.0**（全功能合订） |
| 日期 | 2026-07-29 |
| 适用范围 | 演示操作员、运维用户、应用管理员、联调研发 |
| 代码对齐 | 现网单体仓 `Assistant`（掌上三模式、工具插件、知识双轨、TSM-A 等） |
| 关联 | [index.md](./index.md) · [chiby-strategy-v3.md](./chiby-strategy-v3.md) · [chiby-technical-whitepaper.md](./chiby-technical-whitepaper.md) · [mobile-ai-datacenter-ops-manual.md](./mobile-ai-datacenter-ops-manual.md) |

> **怎么读**：日常使用看 **§2～§8**；安全看 **§9**；知识库看 **§10**；扩展与开发看 **§11**；**规划中**能力见 **§12**（勿与已落地混淆）。

---

## 1. 产品是什么

**Chiby（赤壁）** 让你用自然语言管理多台主机、查阅企业知识，并在变更前强制确认、事后可审计。

| 角色分工 | 说明 |
|----------|------|
| **你** | 选主机、提需求、点确认卡允许/拒绝 |
| **规划脑** | 高效型用规则；智能型/全能型用 Hermes + LLM「想怎么做」 |
| **Assistant 宿主** | 连主机、出确认卡、无头执行、回灌、审计——决定「敢不敢做、怎么做、如何追溯」 |

**一句话**：Hermes / 规则负责「想」；赤壁负责「做与护栏」。

---

## 2. 功能总览（已落地 / 规划中）

### 2.1 已落地（可演示、可日常使用）

| 能力域 | 你能做什么 | 主入口 |
|--------|------------|--------|
| **掌上 IM** | 微信式对话、SSE 流式回复、选机、三模式 | `/demo/mobile-im` |
| **三模式** | 高效型 / 智能型 / 全能型 | IM 顶栏或口令切换 |
| **选机 CDU** | 顶栏勾选目标主机（可多台），按用户记住 | IM 顶栏 |
| **确认卡** | 风险色、展开详情、高危 YES、可选 OTP、AI 解读 | 对话内卡片 |
| **远端执行结果** | 折叠查看命令输出；展开/收起**不**强制滚到底 | 对话气泡 |
| **多机 Job** | 勾选主机批量跑只读/任务 | `/demo/mobile-jobs` |
| **知识库** | 症状→根因→修复短经验 CRUD + Agent 检索 | `/demo/knowledge-hub` |
| **DocHub** | 长文档上传、语义检索、Agent `doc_*` | `/demo/doc-hub` |
| **工具插件** | 主机读写/命令面、知识工具标准化 | 全能型自动调用；市场 `/demo/tools-marketplace` |
| **审计** | 对话与执行留痕；审计大屏 | `/demo/mobile-audit` |
| **Turn Trace** | 单回合意图/切机/工具/审计事件 | API `turn-trace` |
| **Hermes Lab** | Skills / MCP / 记忆轻量面板 | `/demo/hermes-lab` |
| **Web 终端** | 传统终端 + NL 运维能力 | `/` 或终端路由（见部署） |
| **TSM-A 护栏** | 确认、脱敏、可选 OTP/SIEM/短票 | 默认演示友好；生产可开 |

### 2.2 规划中（设计已定稿，功能开关默认关 / 待实现）

| 能力 | 说明 | 设计文档 |
|------|------|----------|
| 统一登录 | 公众号手机号 / 邮箱 OTP | [chiby-app-platform-design.md](./chiby-app-platform-design.md) |
| 应用平台 | `application/` 多应用 + 事务优先路由 | 同上 |
| 小思应用 | 关键字事务 → 标准 `tools/plugins/xs_*`；数据在 `Database/xiaosi` | 同上 §6 / §6.12 |
| Free/Pro 闸门 | 套餐控制高级/Hermes | [mobile-plan-tier-advanced-design.md](./mobile-plan-tier-advanced-design.md) |

---

## 3. 5 分钟快速开始

### 3.1 启动（Windows PowerShell）

```powershell
cd D:\Open\Assistant

$env:OPS_MOBILE_EXECUTOR = "real"   # 真机执行；演练可改 mock
$env:OPS_MOBILE_PLANNER  = "auto"

python -m uvicorn terminal.main:app --host 0.0.0.0 --port 8000
```

日志出现掌上演示路由注册即可。改后端代码后需**重启** uvicorn；前端请 **Ctrl+F5**。

### 3.2 先打开这些页

| 用途 | 地址 |
|------|------|
| 自检 | http://127.0.0.1:8000/api/mobile/demo/rehearsal （期望 `"ready": true`） |
| **主操作 · 模拟 IM** | http://127.0.0.1:8000/demo/mobile-im |
| 审计旁白屏 | http://127.0.0.1:8000/demo/mobile-audit |
| 健康 | http://127.0.0.1:8000/api/health |

`ready: false` 时多半是 `data/hosts.json` 无主机或 ACL 不可见——先配主机。

### 3.3 第一条指令

1. 顶栏**勾选一台主机**  
2. 选 **高效型**（或默认）  
3. 发送：「查看内存使用情况」  
4. 只读类可能直接执行；变更类会出现**确认卡** → 允许后再跑  
5. 右侧可开审计页对照事件  

---

## 4. 全部演示入口一览

| 页面 | 路径 | 用途 |
|------|------|------|
| 掌上 IM | `/demo/mobile-im` | 日常对话、确认、选机、模式 |
| 审计 | `/demo/mobile-audit` | 审计浏览 |
| 多机 Job | `/demo/mobile-jobs` | 勾选主机批量任务 |
| 知识库 | `/demo/knowledge-hub` | 短经验 CRUD |
| DocHub | `/demo/doc-hub` | 长文档入库与试搜 |
| 工具市场 | `/demo/tools-marketplace` | 插件/技能包目录 |
| Hermes Lab | `/demo/hermes-lab` | Skills / MCP / 记忆 |
| API 文档 | `/docs` | Swagger（含 kb / docs / mobile） |

演示身份：常用 `external_user_id`（如演示用户）决定**可见主机**；正式统一登录见 §12。

---

## 5. 三模式怎么用

在 IM 顶栏切换，或发送模式相关口令（以界面文案为准）。

| 模式 | 适合场景 | 行为摘要 |
|------|----------|----------|
| **高效型** `efficient` | 常见巡检、固定话术 | 规则规划 → 确认（高危必卡）→ 无头执行 |
| **智能型** `intelligent` | 需要 Hermes 分析再执行 | Hermes 出 `OPS_PLAN`/`OPS_JOB` → A1 闭环 → 确认后执行 → 回灌 |
| **全能型** `omnipotent` | 读写文件、结构化工具、复杂变更 | Hermes + **REMOTE_TOOL**（A2）→ 确认卡 → 执行 → 结构化回灌 |

**注意**：

- 全能型才会广泛使用主机类插件工具（读文件、写文件、`remote_run` 等）。  
- 智能型偏「命令计划」协议；不要在模式理解上与「运维/高级」旧称呼混淆——现行产品以**三模式**为准（见 ADR-0004）。  
- 高级闭环可点 **停止**；会话忙碌时并发第二请求会提示稍后。

---

## 6. 掌上 IM 详细操作

### 6.1 界面结构

| 区域 | 作用 |
|------|------|
| 顶栏左 | **选机**（CDU HostTargets；可多选；切换主机会作废旧机续接结论并提示） |
| 顶栏右 | 模式、状态、审计入口等 |
| 对话区 | 用户/助手气泡、思考折叠、确认卡、远端执行结果 |
| 底栏 | 输入；左侧 ＋ 快捷提问；可附件（若开启） |

### 6.2 选机（上下文数据单元）

- 选机**不是工具**，是执行前注入的上下文。  
- 换机后：请对本机**重新查询**，勿沿用上一台的内存/磁盘结论。  
- Agent 若显式指定未选中主机，默认会 **硬拦截**（`host_selection_violation`；可用环境变量降级，生产慎关）。

### 6.3 确认卡（变更必经）

出现确认卡时：

1. **一眼区**：主机、风险色、摘要 → 「允许本次」/「拒绝」  
2. **展开详情**：完整命令、diff、影响说明  
3. **AI 解读**：点「解读变更」才生成（默认不预跑）  
4. **高危**：允许后需再输入 **`YES`**；若开启 OTP 再填动态口令  
5. 超时无操作 → 视为拒绝并审计  

飞书/企微通道另有文本或富卡对齐（以部署为准）。

### 6.4 远端执行结果

- 灰色「远端执行结果」块可展开/收起查看输出。  
- **展开/收起不会把整页滚到底**；只有你在底部附近且有新内容时才自动贴底。  
- 用户刚发送消息会强制滚到底，便于看回复。

### 6.5 流式与停止

- 发送后走 SSE：阶段提示 → 正文增量。  
- 处理中可点停止；上一轮未完成时勿连点允许/拒绝（会提示忙碌）。

### 6.6 常用话术示例

| 目的 | 示例 |
|------|------|
| 巡检 | 「查看磁盘」「nginx 是否在跑」 |
| 日志 | 「看一下最近 error 日志」 |
| 文档 | 「查询文档里堡垒机有哪些角色用户」（文档意图优先走知识，而非乱扫多机） |
| 知识沉淀 | 「把刚才的修复步骤记进知识库」 |
| 多机 | 顶栏多选后问「这几台都查一下 uptime」；或用 Job 页 |

---

## 7. 多主机 Job

入口：`/demo/mobile-jobs`

1. 选择可见主机（受 ACL）  
2. 选预设或填写命令  
3. 预览 → 执行 → 查看每机状态与输出  

说明：

- **主路径在本仓**，不依赖小思。  
- 小思若对接：审批通过后调同一 `jobs/*` API（可选）。  
- 变更类 Job 默认更严格；只读任务更适合演示。

---

## 8. Agent 工具能力（用户侧感知）

全能型下，助手可能调用标准化工具（你通过确认卡知情同意）：

| 类别 | 示例工具 | 典型用途 |
|------|----------|----------|
| 知识 | `kb_search` / `kb_get` / `kb_ingest` | 查/写短经验 |
| 文档 | `doc_search` / `doc_get` | 查手册片段 |
| 统一检索 | `search_knowledge` / `get_content` | 调度知识入口 |
| 主机只读 | `remote_read_file`、`list_dir`、`grep`、`logs`… | 看配置与日志 |
| 主机写入 | `remote_write_file`、`mkdir`、`remove`、`restore`… | 改文件（必确认） |
| 命令面 | `remote_run` / `ssh_execute` / `winrm_execute` | 跑命令（按内容风险确认） |
| 目录 | `host_list` | 列出**可见**主机（不是选机） |

工具目录与契约：`tools/plugins/`；浏览：`/demo/tools-marketplace`。  
扩展开发见 [extending-agent-tools.md](./extending-agent-tools.md)。

---

## 9. 安全与合规（使用者须知）

| 机制 | 你需要知道的 |
|------|----------------|
| **确认卡** | 变更默认要人点头；高危要 YES |
| **ACL** | 只能操作被授权的主机 |
| **凭据** | 不进聊天、不进模型；执行用托管凭据 |
| **脱敏** | 审计/回灌会去掉密钥痕迹 |
| **OTP** | 可开：高危在 YES 外再验动态口令（默认关） |
| **审计** | 允许/拒绝/执行均留痕；可查 Turn Trace |
| **熔断** | 连续失败会冷却，避免打爆主机 |

运维开关细则：[tsm-a-ops-runbook.md](./tsm-a-ops-runbook.md)。

---

## 10. 知识双轨

### 10.1 KnowledgeHub（短经验）

| 项 | 说明 |
|----|------|
| 场景 | 「nginx 502 怎么修」 |
| 管理 | `/demo/knowledge-hub` |
| Agent | `kb_search` / `kb_get` / `kb_ingest` |
| 库文件 | `data/knowledge_hub.db` |

完整说明：[knowledge-hub-user-manual.md](./knowledge-hub-user-manual.md)。

### 10.2 DocHub（长文档）

| 项 | 说明 |
|----|------|
| 场景 | 制度、手册、PDF/Word |
| 管理 | `/demo/doc-hub` 上传与试搜 |
| Agent | `doc_search` / `doc_get` |
| 数据 | `data/doc_hub/`（含向量；无 embedding 时可 hash 降级） |

完整说明：[doc-hub-user-manual.md](./doc-hub-user-manual.md)。

**分工**：故障经验 → KnowledgeHub；企业长文 → DocHub。二者不混库。

---

## 11. 配置、数据与扩展（管理员）

### 11.1 关键数据文件

| 路径 | 说明 |
|------|------|
| `data/hosts.json` | 主机与凭据 |
| `data/hermes_bridge.yaml` | Hermes / 掌上 / 工具开关 |
| `data/llm_config.json` 等 | 模型 |
| `data/mobile_demo_acl*.yaml` | 演示用户可见主机 |
| `data/mobile_audit.jsonl` | 掌上审计 |
| `data/mobile_sessions/` · `mobile_transcripts/` | 会话与 transcript |
| `data/host_snapshots/` | 主机快照 |
| `data/turn_traces/` | Turn Trace |
| `data/context_units/` | CDU（如选机）服务端态 |
| `data/knowledge_hub.db` · `data/doc_hub/` | 知识双轨 |

### 11.2 常用环境变量（摘录）

| 变量 | 作用 |
|------|------|
| `OPS_MOBILE_EXECUTOR` | `real` / mock |
| `OPS_MOBILE_PLANNER` | `auto` 等 |
| `OPS_HOST_SELECTION_STRICT` | 选机硬拦截（默认开） |
| `OPS_TSM_REQUIRE_OTP` | 高危 OTP |
| `OPS_TOOL_PLUGINS` | `0` 可关插件发现回退 |
| `DOC_HUB_EMBEDDING_*` | DocHub 向量后端 |

以代码与 `.env.example` 为准。

### 11.3 扩展工具

1. 在 `tools/plugins/<name>/` 放 `manifest.yaml` + `handler.py`  
2. `status: approved` 后重启加载  
3. 市场页可浏览目录  

社区提案进 `tools/contrib/`（不自动加载）。

---

## 12. 规划中：通用应用平台与小思（预告）

设计已定稿，**默认未开**，请勿当作当前演示功能。

| 规划点 | 用户将来会看到的 |
|--------|------------------|
| 双登录 | 公众号手机号 / 邮箱动态码 |
| 事务优先 | 说「请假」等关键字 → 表单事务，不先进 Hermes |
| 应用目录 | `application/<app>/` 配置 + UI + 工具白名单 |
| 小思 | 以**应用**挂在赤壁上；能力走 **`tools/plugins` 同契约**；文件数据在工作区 `Database/xiaosi`（对齐小思 `DataBase/`） |
| 权限 | 仍走小思引擎（`remarkName`），禁止 Agent 直接改业务 ini |

详见：[chiby-app-platform-design.md](./chiby-app-platform-design.md)（含 trunk 源码速查 §6.12）。

---

## 13. 故障排查速查

| 现象 | 处理 |
|------|------|
| rehearsal 未 ready | 检查 `hosts.json`、ACL、Hermes/依赖日志 |
| 发消息无回复 | 看 uvicorn 日志；Ctrl+F5；确认未卡在确认卡 |
| 高级/智能无规划 | Hermes 进程与 `hermes_bridge.yaml`；Lab 页自检 |
| 不能选某主机 | ACL 未授权该 `external_user_id` |
| 越权主机报错 | 检查顶栏选机；属硬拦截属预期 |
| DocHub 搜不准 | 装 chromadb；配 embedding；或接受 hash 降级 |
| 展开执行结果跳底 | 已修复；强制刷新前端缓存 |
| 改代码不生效 | 重启 uvicorn |

彩排口播：[mobile-ai-datacenter-runbook.md](./mobile-ai-datacenter-runbook.md)。

---

## 14. 文档地图（按角色）

| 角色 | 优先阅读 |
|------|----------|
| 演示 / 一线运维 | **本文** · [mobile-ai-datacenter-ops-manual.md](./mobile-ai-datacenter-ops-manual.md) |
| 知识管理员 | [knowledge-hub-user-manual.md](./knowledge-hub-user-manual.md) · [doc-hub-user-manual.md](./doc-hub-user-manual.md) |
| 安全负责人 | [tsm-a-security-model.md](./tsm-a-security-model.md) · [tsm-a-ops-runbook.md](./tsm-a-ops-runbook.md) · [confirm-card-design.md](./confirm-card-design.md) |
| 架构 / 研发 | [chiby-technical-whitepaper.md](./chiby-technical-whitepaper.md) · [system-code-structure.md](./system-code-structure.md) · [index.md](./index.md) |
| 产品演进 | [chiby-app-platform-design.md](./chiby-app-platform-design.md) |

---

## 15. 修订记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-07-29 | v1.0 | 首版全功能合订：已落地能力 + 入口 + 三模式/IM/确认/知识/安全 + 规划预告 |
