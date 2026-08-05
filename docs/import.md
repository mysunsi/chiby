# Chiby / ChibyTerm 重要文档清单（import）

> **用途：** 基于 **2026-08 产品现状**，从 `docs/` 全量文档中筛出「能对齐且当前重要」的阅读/维护清单。  
> **不是**全库索引（全库见 [index.md](./index.md)）；本文件回答：**先读谁、谁作准、谁可归档**。  
> **维护：** 架构/边界/发布有决议变更时，同步改本清单与「现状快照」。

| 项 | 内容 |
|----|------|
| 梳理日期 | 2026-08-05 |
| 对照基线 | SVN 开源拆分 + TestPyPI 试发（手册 r91）；策略 v3；架构/边界 v1.4 |
| 全库规模 | `docs/**/*.md` 约 62 篇 + `adr/` 4 篇 |

---

## 0. 现状快照（对齐口径）

读任何下游文档前，先以本表为准；若某文与本表冲突，**以本表 + LICENSE + 源码目录为准**，旧文标为待修订。

| 维度 | 当前事实 |
|------|----------|
| 产品品牌 | **Chiby（赤壁）** |
| 开源终端产品名 | **ChibyTerm（赤壁终端）** |
| 开源 Python 包 | **`chibycore`**（执行/闭环/KB/DocHub）+ **`chibyterm`**（Web 入口） |
| 源码位置 | `packages/chibycore/`、`packages/chibyterm/` |
| 闭源 | `proprietary/chiby_mobile/`、`proprietary/chiby_hermes_bridge/`（`chiby.plugins` entry_points） |
| 开源核范围 | 全量 Web 终端 + 闭环 + **KnowledgeHub + DocHub** + 工具插件契约 + TSM-A 基础护栏 |
| 闭源范围 | 掌上 AI 机房 + Hermes/Chiby ACP 桥（智能型/全能型中枢胶水） |
| 许可 | 开源面 **Apache-2.0**（根目录 `LICENSE` / `NOTICE`）；上游 Hermes 仍为 **MIT** 依赖 |
| 仓形态 | SVN Monorepo 过渡；门禁 `scripts/check_oss_boundary.py` |
| 发布进度 | **TestPyPI 已试发**；正式 PyPI = 内部试用后（P2-4） |
| 启动入口 | `uvicorn chibyterm.main:app`（开发期可有 `terminal` 别名；正式 wheel **勿依赖** `terminal`） |
| 诚实原则 | 能力标注已交付/部分/设计中/规划；勿把路线图写成现货 |

**已知文档漂移（读旧文时注意）：**

- 部分文仍写 `ops-bridge` / `ops_terminal` / `terminal/main.py` → 现以 **`chibyterm` / `chibycore` / `packages/*`** 为准。  
- [index.md](./index.md) 已于 2026-08-05 按现状重排；启动一律以 **`chibyterm.main`** 为准。  
- 两份打包手册并存：以 **handbook** 为生产主手册（含步骤导读与干净 venv F2）；runbook 为并行补强，勿两套各改一半。

---

## 1. 必读 / 必维护清单（按优先级）

路径均相对仓库根 `Assistant/`（`docs/` 内用相对链接）。

### P0 — 开源边界、发布与对外入口（发版/试用必看）

| # | 文件 | 角色 | 对齐要点 |
|---|------|------|----------|
| 1 | [README.md](../README.md) | 对外产品定义与 5 分钟启动 | 包名、Apache、开源 vs 企业矩阵 |
| 2 | [CONTRIBUTING.md](../CONTRIBUTING.md) | 贡献与门禁约定 | `check_oss_boundary`、测试范围 |
| 2a | [ARCHITECTURE.md](../ARCHITECTURE.md) | 开源/闭源边界薄入口 | 链到 oss-pro / 边界审查 / 插件 |
| 2b | [API_REFERENCE.md](../API_REFERENCE.md) | API 薄入口 | 以 `/docs` 为准；OSS 路由前缀 |
| 2c | [SECURITY.md](../SECURITY.md) | 漏洞私报与范围 | 根目录；勿公开 Issue |
| 2d | [CHANGELOG.md](../CHANGELOG.md) | 对外版本记录 | Keep a Changelog；非 SVN 归纳 |
| 2e | [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | 行为准则 | 社区规范 |
| 3 | [LICENSE](../LICENSE) / [NOTICE](../NOTICE) | 合规事实源 | SPDX=Apache-2.0 |
| 4 | [docs/open-source-boundary-review.md](./open-source-boundary-review.md) | **切分执行约束**（决议锁定） | 开源核/闭源/P0 先拆代码 |
| 5 | [docs/oss-pro-saas-architecture.md](./oss-pro-saas-architecture.md) | **三层产品架构定稿** | 终端 OSS / Pro / 掌上 SaaS |
| 6 | [docs/chibyterm-package-release-handbook.md](./chibyterm-package-release-handbook.md) | **发布主手册**（生产操作版） | build → TestPyPI → 干净 venv F2 → 试用 |
| 7 | [docs/chibyterm-python-packaging-runbook.md](./chibyterm-python-packaging-runbook.md) | 打包 runbook（与上互补） | P0–P2-3 工程细节；冲突时以 handbook 操作步骤为准 |
| 8 | [docs/import.md](./import.md) | **本文**：重要文档清单 | 对齐口径 + 分层清单 |
| 9 | [docs/index.md](./index.md) | 全库导航入口 | 模块→文档→env；结构需随 `packages/` 更新 |

### P1 — 战略口径与开源核能力（对齐叙事 / 用户面）

| # | 文件 | 角色 | 对齐要点 |
|---|------|------|----------|
| 10 | [docs/chiby-strategy-v3.md](./chiby-strategy-v3.md) | 产品与商业策略整合 | 诚实能力矩阵、路线图、开源意向 |
| 11 | [docs/chiby-ai-security-os-mapping.md](./chiby-ai-security-os-mapping.md) | Harness / AI OS 对外映射 | 四层模块真实交付状态 |
| 12 | [docs/chiby-technical-whitepaper.md](./chiby-technical-whitepaper.md) | 技术总架构白皮书 | 三模式、子系统、数据面、边界 |
| 13 | [docs/chiby-product-user-manual.md](./chiby-product-user-manual.md) | 全功能使用说明书（合订） | 已交付 vs 规划预告 |
| 14 | [docs/chiby-one-pager-outline.md](./chiby-one-pager-outline.md) | 一页纸 Pitch | 融资/参赛禁写清单 |
| 15 | [docs/product-focus-and-depth.md](./product-focus-and-depth.md) | 聚焦与做深方向 | 收心做强、信任面优先 |
| 16 | [docs/system-code-structure.md](./system-code-structure.md) | 代码结构与入口对照 | **须随 packages/proprietary 同步** |
| 17 | [docs/knowledge-hub-user-manual.md](./knowledge-hub-user-manual.md) | KnowledgeHub 用户手册 | OSS 知识轨；`kb_*` |
| 18 | [docs/doc-hub-user-manual.md](./doc-hub-user-manual.md) | DocHub 用户说明 | OSS 文档轨 MVP |
| 19 | [docs/doc-hub-technical-design.md](./doc-hub-technical-design.md) | DocHub 技术设计 v2 | MVP 已交付；混合检索/§15 调度=设计中 |
| 20 | [docs/context-data-unit-architecture.md](./context-data-unit-architecture.md) | CDU / HostTargets | 选机上下文硬边界 |
| 20a | [docs/host-group-management.md](./host-group-management.md) | 主机与静态分组落地说明 | 组/tags/labels 三套语义；Fleet·定时·排查；规模化缺口 |
| 21 | [docs/extending-agent-tools.md](./extending-agent-tools.md) | Agent 工具扩展入门 | REMOTE_TOOL / Hello World |
| 22 | [docs/tool-plugin-delivery-handbook.md](./tool-plugin-delivery-handbook.md) | 插件交付一册 | Phase 1–6 契约+验收 |
| 23 | [docs/tool-plugin-architecture.md](./tool-plugin-architecture.md) | 插件架构约定 | `tools/plugins/` manifest |
| 24 | [docs/host-plugin-contract.md](./host-plugin-contract.md) | 主机插件契约 | 薄 handler，不拆内核 |
| 25 | [docs/tool-marketplace-phase6.md](./tool-marketplace-phase6.md) | 工具市场 Phase 6 | catalog / 技能包 |
| 26 | [docs/closure-api.md](./closure-api.md) | 修复闭环 API | OSS 执行面集成要点 |
| 26a | [docs/intent-closed-loop.md](./intent-closed-loop.md) | 意图级闭环（Intent Checklist） | 命令级→意图级；`OPS_INTENT_CHECKLIST`；赛题零人口径 |
| 27 | [docs/winrm-ssh-parity-checklist.md](./winrm-ssh-parity-checklist.md) | WinRM↔SSH 对齐清单 | 双传输体验验收 |
| 28 | [docs/tsm-a-security-model.md](./tsm-a-security-model.md) | TSM-A 安全模型 | L1～L3；开源护栏底座 |
| 29 | [docs/tsm-a-ops-runbook.md](./tsm-a-ops-runbook.md) | TSM-A 运维手册 | OTP/Vault/SIEM 开关 |
| 30 | [docs/confirm-card-design.md](./confirm-card-design.md) | 确认卡 v2 | 风险/YES/AI 解读（已交付范围 B） |

### P2 — 闭源侧（掌上 / Hermes；不进 OSS wheel，但对齐边界必懂）

| # | 文件 | 角色 | 对齐要点 |
|---|------|------|----------|
| 31 | [docs/mobile-ai-datacenter-ops-manual.md](./mobile-ai-datacenter-ops-manual.md) | 掌上演示**首选**操作手册 | 启动/env/页面；明确闭源 |
| 32 | [docs/mobile-ai-datacenter-runbook.md](./mobile-ai-datacenter-runbook.md) | 15 分钟彩排 | 口播与应急 |
| 33 | [docs/hermes-mode-design.md](./hermes-mode-design.md) | Hermes ACP 桥设计 | 与 proprietary 桥对应 |
| 34 | [docs/hermes-tools-product-integration.md](./hermes-tools-product-integration.md) | Hermes 工具对接新产品 | Skill/MCP/A1/A2 |
| 35 | [docs/adr/0001-hermes-mode.md](./adr/0001-hermes-mode.md) | ADR：Hermes 模式 | 决议固化 |
| 36 | [docs/adr/0002-hermes-full-capability.md](./adr/0002-hermes-full-capability.md) | ADR：全功能转化 | 路径 A/B |
| 37 | [docs/adr/0003-remote-tools-and-ops-coexistence.md](./adr/0003-remote-tools-and-ops-coexistence.md) | ADR：远程工具 vs OPS_* | 凭据铁律、A1/A2 |
| 38 | [docs/adr/0004-mode-hierarchy.md](./adr/0004-mode-hierarchy.md) | ADR：三模式分层 | 高效/智能/全能 |
| 39 | [docs/omnipotent-task-state-machine.md](./omnipotent-task-state-machine.md) | 全能型任务状态机 | 显式结案 |
| 40 | [docs/mobile-multi-host-job-design.md](./mobile-multi-host-job-design.md) | 多机 Job | P0/P0.5 已落地；定时待做 |
| 41 | [docs/mobile-im-chat-ui-design.md](./mobile-im-chat-ui-design.md) | IM 壳交互 | M1 已落地 |
| 42 | [docs/p0-hard-block-turn-trace.md](./p0-hard-block-turn-trace.md) | 跨机硬拦截 + Turn Trace | 安全底座与掌上共用概念 |
| 43 | [docs/linux-distro-command-profile-design.md](./linux-distro-command-profile-design.md) | DistroProfile | P0a–P2 已落地 |
| 44 | [docs/mobile-modes-to-industrial-milestones.md](./mobile-modes-to-industrial-milestones.md) | 三模式→工业级里程碑 | checkpoint/escalation 路线 |
| 45 | [docs/ai-agent-contest-system-gap.md](./ai-agent-contest-system-gap.md) | 参赛系统缺口 | S1–S9；冲刺范围 |

### P3 — 设计中 / 规划（重要但勿当现货）

| # | 文件 | 角色 | 状态标签 |
|---|------|------|----------|
| 46 | [docs/chiby-app-platform-design.md](./chiby-app-platform-design.md) | 应用平台 + 小思咬合 | **设计中**（M0–M4） |
| 47 | [docs/db-ai-assistant-design.md](./db-ai-assistant-design.md) | `db_*` 数据库助手 | **设计稿** |
| 48 | [docs/mobile-plan-tier-advanced-design.md](./mobile-plan-tier-advanced-design.md) | Free/Pro 闸门 | **设计中**（待认证里程碑） |
| 49 | [docs/mobile-repair-rollback-design.md](./mobile-repair-rollback-design.md) | 修复失败回滚 | 设计+部分实现，勿整篇当已交付 |
| 50 | [docs/mobile-hermes-assistant-ha.md](./mobile-hermes-assistant-ha.md) | Hermes×Assistant HA | **备忘/建议** |
| 51 | [docs/hermes-credential-profile-script-design.md](./hermes-credential-profile-script-design.md) | 凭据 Profile 脚本 | **草案** |
| 52 | [docs/hermes-im-integration-architecture.md](./hermes-im-integration-architecture.md) | 企微/飞书拓扑 | **参考架构** |
| 53 | [docs/terminal-hermes-ui-spec.md](./terminal-hermes-ui-spec.md) | 终端右侧 Hermes UI | 基线规格；闭源编排相关 |
| 54 | [docs/tool-plugin-integration-tests.md](./tool-plugin-integration-tests.md) | 插件集成测试故事 | 维护测试时参考 |

---

## 2. 建议阅读路径（按角色）

| 角色 | 建议顺序（文件编号见上表） |
|------|---------------------------|
| **生产/发布** | 1 → 6（+7）→ 4 → 5 → 步骤 F2 干净试用 → 9 |
| **开源社区贡献** | 1 → 2 → 4 → 21 → 22 → 17 → 18 |
| **产品/对外口径** | 10 → 11 → 14 → 12 → 13 |
| **掌上/Hermes 演示** | 31 → 32 → 38 → 37 → 33 |
| **新人工程师（全貌）** | 8（本文）→ 16 → 5 → 12 → 6 |

---

## 3. 降级 / 历史稿（不进「重要对齐」主链）

以下仍可考古，**默认不作为现状准绳**；新文档勿再以它们为唯一上游。

| 文件 | 原因 |
|------|------|
| `docs/design-ssh-terminal-ops.md` | 早期终端总设；包名/路径已迁 |
| `docs/工业级AI运维助手设计方案.md` | 2026-05 对照矩阵；部分过时 |
| `docs/AI Ops Assistant v1.0规划(创新的运维大脑).md` | 早期代码分析 |
| `docs/AI 运维自愈系统 - 产品与技术实现全景文档(元宝).md` | remediator 早期 PRD |
| `docs/实现 FastAPI 接口.md` | 已完成实现 prompt |
| `docs/命令修复闭环*.md`、`命令集及UI设计汇总执行.md`、`AI 思考流节奏控制与过程透明度.md` | 早期 UI/闭环稿，多重复 |
| `docs/mobile-ai-datacenter-demo.md` | 原始需求；操作以 ops-manual 为准 |
| `docs/CHANGELOG-svn.md` | SVN 主题归纳；非产品定义 |

---

## 4. 工程脚本与包元数据（文档外，但与清单同级重要）

| 路径 | 用途 |
|------|------|
| `packages/chibycore/pyproject.toml` | 底层库发布元数据 |
| `packages/chibyterm/pyproject.toml` | 终端包发布元数据（依赖 chibycore） |
| `proprietary/*/pyproject.toml` | 闭源包 + `chiby.plugins` |
| `scripts/check_oss_boundary.py` | 开源边界门禁 |
| `scripts/upload_testpypi.ps1` | TestPyPI 上传 |
| `tools/plugins/README.md` | 插件目录说明 |
| `tools/contrib/README.md` | 社区贡献区 |

---

## 5. 维护约定

1. **新增「定稿/决议」类文档**：同步加入本文件对应优先级表，并在 [index.md](./index.md) 挂链。  
2. **包名/目录变更**：先改 §0 快照与 P0 表，再改 handbook / boundary / architecture。  
3. **两套打包文档**：操作步骤以 `chibyterm-package-release-handbook.md` 为准；runbook 仅补工程说明。  
4. **历史稿**：不强制删除；归入 §3，避免新人误读为现货。

---

**文档版本：** 2026-08-05（首版：按开源拆分 + TestPyPI + KB/DocHub 入核梳理）
