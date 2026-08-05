# Assistant 历史修改记录（SVN）

> 来源：`https://yl.sunsi.cn:8443/svn/Open/Assistant`  
> 生成说明：仓库内多数提交**留言为空**，本表按 **revision + 变更路径** 归纳主题；作者均为 `zhangql`。  
> 生成日期：2026-07-17

## 总览

| 阶段 | 修订 | 时间跨度 | 主题 |
|------|------|----------|------|
| 初创入库 | r19–r22 | 2026-05-09 | 整仓导入、清理备份与扩展编译产物 |
| 文档与清单 | r23–r24 | 2026-05-11 | SmartOps / WinRM-SSH 对等清单等 |
| 终端 Web + ops-ui | r25–r27 | 2026-05-14 | 依赖、终端页、ops-ui、hosts |
| Hermes 桥接入 | r35–r36 | 2026-06-15 | ACP WebSocket / hermes_bridge 包 |
| 终端与会话 | r38 | 2026-07-09 | session_manager、终端页微调 |
| 掌上 AI 机房 | r40 | 2026-07-17 | mobile 编排、Hermes Worker、healing、演示页 |

未在上表单独列出的修订（如 r26、r28–r34、r37、r39）多为 **Open 仓库其他目录** 变更，或未改动 `/Assistant` 路径。

---

## 按修订明细

### r19 · 2026-05-09 · 项目初创入库

首次将 `Assistant` 整目录加入 SVN，主要内容包括：

- **终端与 API**：`terminal/`、`api/`、`dashboard/`、`web/`
- **运维内核**：`chibycore/`（网关、闭环、策略、SSH/WinRM oneshot、知识库等）
- **自愈 / remediator**：`remediator/`
- **前端驾驶舱**：`ops-ui/`（含当时 `dist/`）
- **VS Code 扩展**：`extensions/ai-ops-remediate/`
- **数据与部署**：`data/`、`deploy/`、`docs/`、`tests/`、`requirements.txt`

### r20 · 2026-05-09

- 更新 `README.md`

### r21 · 2026-05-09

- 删除 `data/hosts.json.bak`

### r22 · 2026-05-09

- 删除 `extensions/ai-ops-remediate/out`（编译产物出库）

### r23 · 2026-05-11 · 文档补强

- 新增：`docs/SmartOps.docx`、`docs/cursor_0510.md`、`docs/index.md`、`docs/winrm-ssh-parity-checklist.md`
- 更新：`docs/closure-api.md`、`docs/design-ssh-terminal-ops.md`

### r24 · 2026-05-11

- 更新 `docs/SmartOps.docx`

### r25 · 2026-05-14 · 终端 / ops-ui 联调

- `terminal/main.py`、`terminal/web/index.html`
- `ops-ui`：`App.tsx`、`vite.config.ts`、`package-lock.json`
- `pyproject.toml`、`requirements.txt`、`README.md`
- `data/hosts.json`

### r27 · 2026-05-14

- 更新 `terminal/web/index.html`

### r33 · 2026-06-03（跨目录提交，含 Assistant 文档）

- 更新 `Assistant/docs/SmartOps.docx`
- （同批还改动了仓库根下其它文档，如 Hermes 解析等）

### r35 · 2026-06-15 · Hermes 进入终端 Web

- 新增：`terminal/hermes_audit_api.py`、`terminal/hermes_ws.py`
- 更新：`terminal/models.py`、`terminal/web/index.html`

### r36 · 2026-06-15 · Hermes ACP 桥

新增 `terminal/hermes_bridge/`：

- `acp_session.py` / `acp_wire.py` / `config.py` / `spawn.py` / `ws_validate.py`

### r38 · 2026-07-09

- 更新 `terminal/session_manager.py`、`terminal/web/index.html`
- （同批含 Knowledge 等其它目录）

### r40 · 2026-07-17 · **掌上 AI 机房助手**

提交说明（唯一非空留言）：

> 增加掌上AI机房助手的功能

主要变更：

- **掌上编排**：新增 `terminal/mobile/`（orchestrator、headless_exec、hermes_protocol/planner、IM 飞书/企微骨架、ACL/审计等）
- **演示页**：`terminal/web/mobile_im_demo.html`、`mobile_audit.html`
- **Hermes Worker**：`terminal/hermes_bridge/acp_worker.py`、`text_clean.py`；增强 `acp_session.py` / `config.py`
- **Agent 服务**：`terminal/agent_service.py`；`llm_shell.py`、`main.py`、`session_manager.py`
- **自愈模块**：`chibycore/healing/`；oneshot / KB / remediator 相关调整
- **配置与文档**：`data/hermes_bridge*.yaml`、mobile ACL/IM example；Hermes / 掌上相关 docs
- **测试**：`tests/test_hermes_*.py` 等

---

## 能力演进时间线（归纳）

```text
2026-05  终端 + chibycore + remediator + ops-ui 基线入库
   │
2026-05  文档体系 / WinRM-SSH 对等清单
   │
2026-05  终端 Web 与 ops-ui 联调
   │
2026-06  Hermes ACP 桥 + 终端右侧 Hermes
   │
2026-07  掌上 AI 机房（无头 SSH/WinRM + 运维/高级模式）
```

---

## 使用与维护建议

1. **以后提交请写非空 log message**（例如：`fix(mobile): 高级模式变更命令走审批卡`），否则只能靠路径猜意图。  
2. 本文件为根据 SVN 路径**事后重建**；若与口头约定不符，以 `svn log -v` 为准。  
3. 更新本记录：`svn log -v Assistant` 后按上表格式追加新 revision。

## 命令备忘

```bash
# 查看 Assistant 路径完整历史
svn log -v https://yl.sunsi.cn:8443/svn/Open/Assistant

# 仅看留言与作者
svn log -l 50 Assistant
```
