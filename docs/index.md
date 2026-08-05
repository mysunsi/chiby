# Assistant / ChibyTerm 文档索引

一页速查：**现状口径 → 文档导航 → 源码入口 → 环境变量 / data → 启动与测试**。  
详细设计见各专题文档；**先读谁 / 谁作准**见 [import.md](./import.md)。

| 项 | 当前事实（2026-08） |
|----|---------------------|
| 品牌 | **Chiby（赤壁）** |
| 开源终端 | **ChibyTerm（赤壁终端）** · 包名 `chibyterm` + `chibycore` |
| 许可 | 开源面 **Apache-2.0**（根目录 `LICENSE` / `NOTICE`） |
| 源码布局 | `packages/chibycore`、`packages/chibyterm`；闭源 `proprietary/chiby_mobile`、`proprietary/chiby_hermes_bridge` |
| 发布 | TestPyPI 已试发；正式 PyPI 待内部试用后（P2-4） |
| 启动入口 | `uvicorn chibyterm.main:app`（开发期可有 `terminal` 别名；**正式 wheel 勿依赖**） |

> **重要文档分层：** [import.md](./import.md)  
> **发布 / 干净 venv 试用：** [chibyterm-package-release-handbook.md](./chibyterm-package-release-handbook.md)  
> **对外入口：** [../README.md](../README.md) · [../CONTRIBUTING.md](../CONTRIBUTING.md) · [../ARCHITECTURE.md](../ARCHITECTURE.md) · [../API_REFERENCE.md](../API_REFERENCE.md) · [../SECURITY.md](../SECURITY.md) · [../CHANGELOG.md](../CHANGELOG.md)

---

## 0. 仓库目录（对齐用）

```text
Assistant/
├── packages/
│   ├── chibycore/          # 开源：执行网关、闭环、KnowledgeHub、DocHub
│   └── chibyterm/          # 开源：Web 终端入口（依赖 chibycore）
├── proprietary/
│   ├── chiby_mobile/       # 闭源：掌上 AI 机房
│   └── chiby_hermes_bridge/# 闭源：Hermes/Chiby ACP 桥
├── terminal/               # 仅开发便利别名 → chibyterm（不进正式 wheel）
├── tools/plugins/          # 工具插件契约与市场
├── data/                   # 运行时配置（hosts、llm 等；勿提交密钥）
├── scripts/
│   ├── check_oss_boundary.py
│   └── upload_testpypi.ps1
├── docs/                   # 本目录
├── LICENSE / NOTICE / README.md / CONTRIBUTING.md
├── ARCHITECTURE.md / API_REFERENCE.md / CHANGELOG.md / SECURITY.md
└── pyproject.toml          # Monorepo 开发用（editable）
```

曾用名（废弃）：`ops_terminal` / `ops-terminal` → **`chibyterm`**；`ops_core` → **`chibycore`**。  
环境变量仍多用 `OPS_*`（与包名解耦，刻意保留）。

---

## 1. 产品总览与战略

| 文档 | 说明 |
|------|------|
| [import.md](./import.md) | **重要文档清单**（P0～P3：先读谁 / 可归档） |
| [chiby-strategy-v3.md](./chiby-strategy-v3.md) | 产品与商业策略 v3.0：诚实能力矩阵 + 路线图 |
| [chiby-ai-security-os-mapping.md](./chiby-ai-security-os-mapping.md) | AI 安全 OS ↔ 赤壁四层映射（诚实状态） |
| [chiby-technical-whitepaper.md](./chiby-technical-whitepaper.md) | 技术白皮书：总架构、三模式、子系统、开源边界 |
| [chiby-product-user-manual.md](./chiby-product-user-manual.md) | 全功能使用说明书（已交付 + 规划预告） |
| [chiby-one-pager-outline.md](./chiby-one-pager-outline.md) | 一页纸 Pitch：版式 + 禁写清单 |
| [chiby-app-platform-design.md](./chiby-app-platform-design.md) | 通用应用平台 + 小思咬合（**设计中**） |
| [product-focus-and-depth.md](./product-focus-and-depth.md) | 聚焦与做深方向 |
| [system-code-structure.md](./system-code-structure.md) | 代码结构与入口对照（**须随 packages 同步**） |
| [ai-agent-contest-system-gap.md](./ai-agent-contest-system-gap.md) | 参赛规格 ↔ 仓库缺口 S1–S9 |

---

## 2. 开源边界与发布（P0）

| 文档 | 说明 |
|------|------|
| [oss-pro-saas-architecture.md](./oss-pro-saas-architecture.md) | 三层架构定稿：终端 OSS / Pro / 掌上 SaaS（v1.4） |
| [open-source-boundary-review.md](./open-source-boundary-review.md) | 切分执行约束 + 决议锁定 + P0 解耦清单（v1.4） |
| [chibyterm-package-release-handbook.md](./chibyterm-package-release-handbook.md) | **发布主手册**：build → TestPyPI → 干净 venv F2 → 试用 |
| [chibyterm-python-packaging-runbook.md](./chibyterm-python-packaging-runbook.md) | 打包 runbook（与 handbook 互补；操作冲突以 handbook 为准） |
| `scripts/check_oss_boundary.py` | 开源边界门禁（`packages/` 禁硬 import 闭源） |
| `scripts/upload_testpypi.ps1` | TestPyPI 上传 |

**开源核：** 全量 Web 终端 + 闭环 + KnowledgeHub + DocHub + 工具插件契约 + TSM-A 基础护栏。  
**闭源：** 掌上 AI 机房 + Hermes/Chiby ACP 桥（`chiby.plugins` entry_points 动态挂载）。

---

## 3. ChibyTerm 开源核（Web 终端 / KB / Doc / 插件）

### 3.1 用户与能力文档

| 文档 | 说明 |
|------|------|
| [knowledge-hub-user-manual.md](./knowledge-hub-user-manual.md) | KnowledgeHub 使用手册（`kb_*` / 生命周期） |
| [doc-hub-user-manual.md](./doc-hub-user-manual.md) | DocHub 使用说明（MVP） |
| [doc-hub-technical-design.md](./doc-hub-technical-design.md) | DocHub 技术设计 v2.0（混合检索 / §15 调度 = 设计中） |
| [closure-api.md](./closure-api.md) | 修复闭环 API（`closure-execute` 等） |
| [intent-closed-loop.md](./intent-closed-loop.md) | **意图级闭环**：Intent Checklist、与 goal_resume 关系、零人工口径 |
| [winrm-ssh-parity-checklist.md](./winrm-ssh-parity-checklist.md) | WinRM ↔ SSH 体验对齐清单 |
| [extending-agent-tools.md](./extending-agent-tools.md) | 扩展 Agent 工具（REMOTE_TOOL；Hello World） |
| [tool-plugin-architecture.md](./tool-plugin-architecture.md) | `tools/plugins/` manifest / handler |
| [tool-plugin-delivery-handbook.md](./tool-plugin-delivery-handbook.md) | 插件交付一册（Phase 1–6） |
| [host-plugin-contract.md](./host-plugin-contract.md) | 主机插件契约（薄 handler） |
| [tool-marketplace-phase6.md](./tool-marketplace-phase6.md) | 工具市场：发现 / 版本 / 技能包 |
| [tool-plugin-integration-tests.md](./tool-plugin-integration-tests.md) | 插件场景集成测试 |
| [context-data-unit-architecture.md](./context-data-unit-architecture.md) | CDU / HostTargets |
| [host-group-management.md](./host-group-management.md) | **主机与静态分组落地**：模型 / API / Fleet·排查·定时用法、三套选机语义、规模化缺口 |
| [../tools/contrib/README.md](../tools/contrib/README.md) | 社区工具贡献区 |
| [../tools/plugins/README.md](../tools/plugins/README.md) | 插件目录说明 |

演示页（开源默认可开）：`/demo/knowledge-hub` · `/demo/doc-hub` · `/demo/tools-marketplace`  
API：`/api/kb/*` · `/api/docs/*` · `GET /api/tools/catalog` · `/api/tools/packs`

### 3.2 源码入口（开源）

| 模块路径 | 必读 / 说明 | 环境变量 / 配置（节选） |
|----------|-------------|-------------------------|
| `packages/chibyterm/main.py` | Web 入口、会话、hosts、闭环路由 | `OPS_SHELL_PORT`、`OPS_MOBILE_DEMO*`、`OPS_HERMES_BRIDGE*`（默认关） |
| `packages/chibyterm/host_groups.py` | 静态主机组 CRUD（见 [host-group-management.md](./host-group-management.md)） | `data/host_groups.json` |
| `packages/chibyterm/session_manager.py` | 会话 / PTY / WinRM | `OPS_TERMINAL_CAPTURE_MAX_CHARS`、`OPS_WINRM_*` |
| `packages/chibyterm/llm_shell.py` | 自然语言 → Shell | `data/llm_config.json`、`llm_models.json` |
| `packages/chibyterm/tools_plugin_loader.py` | 工具插件发现 | `OPS_TOOL_PLUGINS`、`OPS_TOOL_PLUGINS_DIR` |
| `packages/chibycore/local_oneshot.py`、`ssh_oneshot.py`、`winrm_oneshot.py`、`ssh_executor.py` | 执行路径 | `OPS_LOCAL_ONESHOT_MAX_OUTPUT_CHARS` |
| `packages/chibycore/closure_retry_runner.py`、`closure_events.py`、`closure_service.py` | 闭环重试与 obs | `OPS_CLOSURE_OBS_SSE`、`OPS_CLOSURE_*`、`OPS_CLOSURE_FIX_FALLBACK` |
| `packages/chibycore/intent_checklist.py` | 意图清单与逐项编排 | `OPS_INTENT_CHECKLIST`（默认开） |
| `packages/chibycore/output_budget.py` | 输出预算 | `OPS_CLOSURE_*`、`OPS_API_EXEC_IO_TAIL_CHARS` |
| `packages/chibycore/execution_gateway.py`、`policy_engine.py` | 网关与策略 | `OPS_POLICY_*` |
| `packages/chibycore/pending_change_control.py` | 变更冻结 | SQLite `data/ops.db` |
| `packages/chibycore/knowledge_hub/` | KnowledgeHub | `data/knowledge_hub.db` 等 |
| `packages/chibycore/doc_hub/` | DocHub | `data/doc_hub/` |
| `packages/chibycore/mcp_loader.py` | MCP 预加载 | `OPS_MCP_CONFIG`、`OPS_MCP_STRICT` |
| `packages/chibycore/subprocess_util.py` | POSIX 进程组与超时 | （通常无需配置） |

早期总设（历史，包名/路径已迁）：[design-ssh-terminal-ops.md](./design-ssh-terminal-ops.md)

---

## 4. 安全护栏（TSM-A / 确认卡）

| 文档 | 说明 |
|------|------|
| [tsm-a-security-model.md](./tsm-a-security-model.md) | TSM-A L1～L3 安全模型 |
| [tsm-a-ops-runbook.md](./tsm-a-ops-runbook.md) | 运维手册：OTP / Vault / SIEM |
| [confirm-card-design.md](./confirm-card-design.md) | 确认卡 v2.0（范围 B 已落地） |
| [p0-hard-block-turn-trace.md](./p0-hard-block-turn-trace.md) | 跨机硬拦截 + Turn Trace MVP |

---

## 5. 闭源：掌上 AI 机房 / Hermes 桥

> 不进公开 `chibyterm` wheel；需安装闭源包并打开开关。演示首选：[mobile-ai-datacenter-ops-manual.md](./mobile-ai-datacenter-ops-manual.md)。

| 文档 | 说明 |
|------|------|
| [mobile-ai-datacenter-ops-manual.md](./mobile-ai-datacenter-ops-manual.md) | **掌上启动与演示操作手册**（首选） |
| [mobile-ai-datacenter-runbook.md](./mobile-ai-datacenter-runbook.md) | 15 分钟口播彩排 |
| [mobile-ai-datacenter-demo.md](./mobile-ai-datacenter-demo.md) | 原始需求/设计（操作以 ops-manual 为准） |
| [mobile-im-chat-ui-design.md](./mobile-im-chat-ui-design.md) | 模拟 IM 壳（M1 已落地） |
| [mobile-multi-host-job-design.md](./mobile-multi-host-job-design.md) | 多机 Job（P0/P0.5 已落地；定时待做） |
| [mobile-repair-rollback-design.md](./mobile-repair-rollback-design.md) | 修复失败回滚（设计稿） |
| [mobile-plan-tier-advanced-design.md](./mobile-plan-tier-advanced-design.md) | Free/Pro 闸门（设计中） |
| [mobile-hermes-assistant-ha.md](./mobile-hermes-assistant-ha.md) | Hermes×Assistant HA（备忘） |
| [mobile-modes-to-industrial-milestones.md](./mobile-modes-to-industrial-milestones.md) | 三模式 → 工业级里程碑 |
| [linux-distro-command-profile-design.md](./linux-distro-command-profile-design.md) | DistroProfile（P0a～P2 已落地） |
| [omnipotent-task-state-machine.md](./omnipotent-task-state-machine.md) | 全能型任务状态机 |
| [hermes-mode-design.md](./hermes-mode-design.md) | Hermes ACP 桥设计 |
| [hermes-tools-product-integration.md](./hermes-tools-product-integration.md) | Hermes 工具对接（Skill/MCP/A1/A2） |
| [hermes-im-integration-architecture.md](./hermes-im-integration-architecture.md) | 企微 / 飞书拓扑参考 |
| [hermes-credential-profile-script-design.md](./hermes-credential-profile-script-design.md) | Profile / 凭据隔离（草案） |
| [terminal-hermes-ui-spec.md](./terminal-hermes-ui-spec.md) | 终端右侧 Hermes UI 规格 |
| [db-ai-assistant-design.md](./db-ai-assistant-design.md) | `db_*` 数据库助手（设计稿） |
| [adr/0001-hermes-mode.md](./adr/0001-hermes-mode.md) | ADR：Hermes 模式 |
| [adr/0002-hermes-full-capability.md](./adr/0002-hermes-full-capability.md) | ADR：全功能转化路径 A/B |
| [adr/0003-remote-tools-and-ops-coexistence.md](./adr/0003-remote-tools-and-ops-coexistence.md) | ADR：远程工具 vs OPS_* |
| [adr/0004-mode-hierarchy.md](./adr/0004-mode-hierarchy.md) | ADR：高效 / 智能 / 全能 |

演示页（需闭源插件启用）：`/demo/mobile-im` · `/demo/mobile-jobs` · `/demo/hermes-lab` 等。

源码：`proprietary/chiby_mobile/src/chiby_mobile/`、`proprietary/chiby_hermes_bridge/src/chiby_hermes_bridge/`  
开发别名：`terminal.mobile` / `terminal.hermes_bridge` → 上述闭源包（见根 `conftest.py` / `path_alias.py`）。

---

## 6. 数据文件（运行时）

| 路径 | 说明 |
|------|------|
| `data/hosts.json` | 主机清单（可能含加密字段；**勿提交真实密码到公开仓**） |
| `data/host_groups.json` | 静态主机组（见 [host-group-management.md](./host-group-management.md)） |
| `data/llm_config.json`、`data/llm_models.json` | LLM 提供方与模型列表 |
| `data/llm_config.example.json` | LLM 配置示例 |
| `data/knowledge_hub.db` | KnowledgeHub 库（运行时生成） |
| `data/doc_hub/` | DocHub 数据目录 |
| `data/kb_closure_archive.jsonl` | 闭环成功占位归档 |
| `data/ops.db` | 变更控制等 SQLite（若启用） |
| `data/hermes_bridge.yaml` | Hermes 桥配置（闭源场景；有 example） |
| `data/transcripts/`、`data/turn_traces/` | 会话与 Turn Trace（运行时） |

干净 venv 试用：在独立工作目录自建 `data/`，见 handbook **步骤 F2**。

---

## 7. 启动命令

### 7.1 本仓开发

```powershell
cd D:\Open\Assistant
$env:OPS_MOBILE_DEMO = "0"
$env:OPS_HERMES_BRIDGE = "0"
python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

浏览器：http://127.0.0.1:8000 → 顶栏 **ChibyTerm · 赤壁终端**  
API 文档：http://127.0.0.1:8000/docs  
健康检查：`GET /api/health`（含 mcp / closure 等摘要，视依赖而定）

开发期过渡别名（勿用于正式 wheel）：`uvicorn terminal.main:app`

### 7.2 干净 venv / TestPyPI 安装后

```powershell
# 工作目录须含 data/（见 handbook 步骤 F2）
cd C:\ChibyWork
python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

### 7.3 企业闭源扩展（可选）

```powershell
# 安装闭源 wheel 后：
$env:OPS_MOBILE_DEMO = "1"          # 或 OPS_MOBILE_DEMO_ENABLED
$env:OPS_HERMES_BRIDGE = "1"        # 或 OPS_HERMES_BRIDGE_ENABLED
python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

---

## 8. 测试与门禁

| 命令 / 项 | 说明 |
|-----------|------|
| `pytest -m "not proprietary"` | **开源门禁套件**（闭源 import 的用例由 `conftest.py` 自动打标并排除） |
| `pytest`（全量） | 含掌上 / Hermes；需本仓 `proprietary/` 在 path 上 |
| `python scripts/check_oss_boundary.py` | 开源边界静态检查 |
| `python scripts/check_oss_boundary.py --wheel <whl>` | 检查已构建 wheel |

---

## 9. 历史 / 降级稿（默认不主读）

完整降级表见 [import.md §3](./import.md)。常见包括：

- `design-ssh-terminal-ops.md`、`工业级AI运维助手设计方案.md`
- `AI Ops Assistant v1.0规划*.md`、`AI 运维自愈系统*.md`、`实现 FastAPI 接口.md`
- `命令修复闭环*.md`、`命令集及UI设计汇总执行.md`
- `CHANGELOG-svn.md`（SVN 主题归纳，非产品定义）

---

**索引版本：** 2026-08-05（对齐 packages/proprietary、ChibyTerm 启动入口、TestPyPI、开源门禁 pytest）
