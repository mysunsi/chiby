# tools/plugins — 已审核可加载工具

每个子目录：`manifest.yaml` + `handler.py`（仅 `status: approved` 加载）。

薄 handler → `host_plugin_delegate.py`（主机类）→ `remote_tools` **调度内核**（命令编译 / 确认卡 pending / batch 展开等共享逻辑）。

**交付一册（契约 + 迁移清单 + 全量工具表）**：[docs/tool-plugin-delivery-handbook.md](../../docs/tool-plugin-delivery-handbook.md)。

## 本地（Phase 1）

| 目录 | 说明 |
|------|------|
| `search_knowledge/` · `get_content/` | 统一知识调度 |
| `kb_*` / `doc_*` | KnowledgeHub / DocHub |
| `host_list/` · `example_echo/` | 可见主机**目录发现**（非选机 CDU） / Hello World |

> 选机是上下文数据单元 HostTargets，见 [`docs/context-data-unit-architecture.md`](../../docs/context-data-unit-architecture.md)。

## 主机只读（Phase 3）· 8

`remote_read_file` · `list_dir` · `grep` · `search` · `diff` · `logs` · `backup` · `syntax_check`

## 主机写入（Phase 4）· 5

`remote_write_file` · `mkdir` · `remove` · `restore` · `rollback`

## 命令面单机（Phase 5 主体）· 3

`remote_run` · `ssh_execute` · `winrm_execute`（`confirm_mode: command_content`）

**合计约 16+ 工具入口已插件化。**

## 市场 / 技能包（Phase 6）

manifest 可声明 `skill_pack`、`version`、`dependencies`（展示用）。  
见 [`docs/tool-marketplace-phase6.md`](../../docs/tool-marketplace-phase6.md)；预览 `/demo/tools-marketplace`。

场景串链集成测试：[`docs/tool-plugin-integration-tests.md`](../../docs/tool-plugin-integration-tests.md)（`pytest tests/test_plugin_scenario_chains.py`）。

## 明确不迁（当前）

| 仍在内核 | 原因 |
|----------|------|
| `ssh_batch` / `winrm_batch` | 多机展开与执行内核耦合深；**刻意暂留**，非遗漏 |
| 命令编译 / pending / 流式 / 自动备份 | 共享依赖，插件只委托 |

见 [`docs/host-plugin-contract.md`](../../docs/host-plugin-contract.md)。
