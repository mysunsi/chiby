# 工具插件化交付手册

> **一册收成**：契约摘要 + 迁移清单 + 全量工具表 + 验收入口。  
> 版本：2026-07-27 · Phase 1–6 + 场景集成测试已落地。  
> 详设原文仍保留；冲突时以**实现与本册验收表**为准，详设作背景。

| 读者 | 用途 |
|------|------|
| 交付 / 验收 | 对照迁移勾选、工具表、测试命令 |
| 二次开发 | 新增插件最小契约与目录约定 |
| 运维 | 开关、市场预览、刻意不迁项 |

---

## 0. 一句话结论

**添加工具 = `tools/plugins/<id>/manifest.yaml` + `handler.py`（`status: approved`）**；主机类只做**薄委托**，命令编译 / 确认卡 pending / batch / 流式仍在 `terminal/mobile/remote_tools.py`。  
当前：**25** 个插件入口已加载；白名单内仅 **`ssh_batch` / `winrm_batch`** 刻意留在内核。

---

## 1. 架构契约（不可倒车）

### 1.1 四原则

| 原则 | 含义 |
|------|------|
| 执行内核统一 | SSH/WinRM 编译、多机 batch、流式、附件、pending 字段 → **内核** |
| 插件只做入口 | manifest 元数据 + 薄 handler；不复制命令生成 |
| 凭据不出插件 | 禁止 password / secret / token / private_key；只传 `host_id` + 业务参数 |
| 先契约后搬运 | Phase 2 定接口 → 3 只读 → 4 写入 → 5 命令 → 6 市场 → 集成验收 |

### 1.2 目录与角色

```text
tools/plugins/<tool_id>/     # 可执行（approved）
  manifest.yaml
  handler.py                 # run 或 arun
tools/contrib/               # 提案，不自动 import
terminal/tools_plugin_loader.py
terminal/mobile/host_plugin_delegate.py   # 主机薄委托
terminal/mobile/remote_tools.py           # 调度内核 + 共享依赖
terminal/mobile/{kb,doc,orchestrator}_tools.py  # 本地实现库
```

| 旋钮 | 作用 |
|------|------|
| `OPS_TOOL_PLUGINS=0` | 关闭插件发现；回退库模块 / 内核硬编码 |
| `OPS_TOOL_PLUGINS_DIR` | 自定义扫描根 |
| `remote_tools.plugin_auto_merge` | 白名单自动合并插件 id（默认 true） |

### 1.3 Manifest 要点

**本地**

```yaml
type: local_readonly | local_write
host_required: false
status: approved
```

**主机**

```yaml
type: host_readonly | host_write | host_command
host_required: true
security:
  needs_confirmation: true|false
  confirm_mode: command_content   # 命令面：按 command 内容判定，不强制工具级恒确认
  read_only: true|false
  risk_level: low|medium|high|critical
skill_pack: remote_fs|remote_shell|knowledge|…   # Phase 6 聚合
dependencies:                                    # 展示用，不自动安装
  - id: remote_backup
    kind: tool
    optional: false
```

加载门禁：`status != approved` 不加载；参数名禁止含凭据子串；未列入迁移名单的内置同名 → 插件跳过；已迁名允许插件注册并优先执行。

### 1.4 Handler / Context / 返回值

```python
def run(params: dict, context: dict) -> dict: ...
# 或 async def arun(...)
```

| context 键 | 说明 |
|------------|------|
| `raw` / `agent_mode` | 原始 JSON、模式 |
| `executor` / `resolve_host` / `host_allowed` | 主机执行（无明文密码） |
| `list_visible_hosts` | `host_list` |
| `stream_chunk` / `attachment_store` | 流式 / 附件 |

返回：`{ok, error, error_code, stdout, data, duration_ms, exit_code?, command?, host?}`。  
可选 `format_result(data) -> str`。

主机推荐路径：`handler.arun` → `host_plugin_delegate.arun_host_tool` → `execute_remote_tool_call`（`_host_plugin_delegated` 防递归）。

### 1.5 确认卡与白名单

- 写文件 / 删除 / restore / rollback → `FILE_ALWAYS_CONFIRM`（恒确认）。
- `kb_ingest` → 确认；pending 必须保留 title/symptom 等字段。
- 命令面 `confirm_mode: command_content` → `plugin_needs_confirmation` 返回 `None`，由命令内容策略判定。
- 工具名仍在 `DEFAULT_ALLOWED_TOOLS`；迁出后进 `MIGRATED_*_PLUGIN_TOOLS`。

详设：[host-plugin-contract.md](./host-plugin-contract.md) · [tool-plugin-architecture.md](./tool-plugin-architecture.md) · [extending-agent-tools.md](./extending-agent-tools.md)

---

## 2. 迁移清单（分期验收）

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | Loader + 本地 kb/doc/orch/host_list/example_echo | **完成** |
| Phase 2 | 主机契约（不拆内核；薄 handler） | **契约+实现路径完成** |
| Phase 3 | 主机只读文件 8 件 | **完成** |
| Phase 4 | 主机写入 5 件（含 rollback 别名） | **完成** |
| Phase 5 | `remote_run` / `ssh_execute` / `winrm_execute` | **主体完成** |
| Phase 5 暂留 | `ssh_batch` / `winrm_batch` | **刻意不迁** |
| Phase 6 | 市场：version / skill_pack / dependencies / packs API | **完成** |
| 集成验收 | 场景串链 `test_plugin_scenario_chains.py` | **完成** |

### 2.1 已迁本地（9）— `MIGRATED_LOCAL_PLUGIN_TOOLS`

- [x] `example_echo`
- [x] `host_list`
- [x] `kb_search` · `kb_get` · `kb_ingest`
- [x] `doc_search` · `doc_get`
- [x] `search_knowledge` · `get_content`

### 2.2 已迁主机（16）— `MIGRATED_HOST_PLUGIN_TOOLS`

**只读（8）**

- [x] `remote_read_file` · `remote_list_dir` · `remote_grep` · `remote_search`
- [x] `remote_diff` · `remote_logs` · `remote_backup` · `remote_syntax_check`

**写入（5）**

- [x] `remote_write_file` · `remote_mkdir` · `remote_remove`
- [x] `remote_restore` · `remote_rollback`（别名 → restore）

**命令单机（3）**

- [x] `remote_run` · `ssh_execute` · `winrm_execute`

### 2.3 刻意不迁 / 共享依赖（非遗漏）

| 项 | 原因 |
|----|------|
| `ssh_batch` / `winrm_batch` | 多机展开与协议过滤深耦内核 |
| `build_file_tool_command` / pending / 流式 / 自动备份 | 共享依赖；插件只委托 |

### 2.4 验收命令

```bash
pytest tests/test_tool_plugins.py -q
pytest tests/test_plugin_scenario_chains.py -q   # 或 pytest -m integration -q
```

场景说明：[tool-plugin-integration-tests.md](./tool-plugin-integration-tests.md)  
市场 API：[tool-marketplace-phase6.md](./tool-marketplace-phase6.md) · 预览 `/demo/tools-marketplace`

---

## 3. 全量工具表

> 来源：`tools/plugins/*/manifest.yaml`（2026-07-27）。  
> **确认**：`Y`=工具级需确认；`cmd`=`confirm_mode: command_content`；`N`=免确认（只读/本地只读）。  
> **依赖**：声明式展示，不自动安装。

### 3.1 本地插件

| id | 标题 | type | skill_pack | ver | 确认 | RO/RW | 依赖 |
|----|------|------|------------|-----|------|-------|------|
| example_echo | Hello World 回显 | local_readonly | example | 1.0.0 | N | RO | — |
| host_list | 列出可见主机 | local_readonly | host | 1.0.0 | N | RO | — |
| kb_search | 运维知识库检索 | local_readonly | knowledge | 1.0.0 | N | RO | — |
| kb_get | 运维知识库详情 | local_readonly | knowledge | 1.0.0 | N | RO | — |
| kb_ingest | 运维知识沉淀 | local_write | knowledge | 1.0.0 | Y | RW | — |
| doc_search | 企业文档语义检索 | local_readonly | document | 1.0.0 | N | RO | — |
| doc_get | 企业文档片段读取 | local_readonly | document | 1.0.0 | N | RO | — |
| search_knowledge | 统一知识检索 | local_readonly | knowledge | 1.0.0 | N | RO | kb_search, doc_search |
| get_content | 统一知识正文 | local_readonly | knowledge | 1.0.0 | N | RO | kb_get, doc_get |

### 3.2 主机只读（remote_fs）

| id | 标题 | ver | 确认 | 依赖 |
|----|------|-----|------|------|
| remote_read_file | 读取远端文件 | 1.0.0 | N | — |
| remote_list_dir | 列出远端目录 | 1.0.0 | N | — |
| remote_grep | 远端代码/文本搜索 | 1.0.0 | N | — |
| remote_search | 远端搜索（grep 别名） | 1.0.0 | N | — |
| remote_diff | 远端文件变更对比 | 1.0.0 | N | — |
| remote_logs | 远端日志尾部 | 1.0.0 | N | — |
| remote_backup | 远端文件备份 | 1.0.0 | N | — |
| remote_syntax_check | 远端语法检查 | 1.0.0 | N | — |

### 3.3 主机写入（remote_fs）

| id | 标题 | ver | 确认 | risk | 依赖 |
|----|------|-----|------|------|------|
| remote_write_file | 写入远端文件 | 1.0.0 | Y | medium | remote_read_file（可选声明） |
| remote_mkdir | 创建远端目录 | 1.0.0 | Y | medium | — |
| remote_remove | 删除远端文件或目录 | 1.0.0 | Y | high | — |
| remote_restore | 从备份恢复远端文件 | 1.0.0 | Y | high | remote_backup |
| remote_rollback | 回滚（restore 别名） | 1.0.0 | Y | high | remote_backup |

### 3.4 主机命令（remote_shell）

| id | 标题 | ver | 确认 | 说明 |
|----|------|-----|------|------|
| remote_run | 远端执行命令 | 1.0.0 | cmd | 通用命令入口 |
| ssh_execute | SSH 执行命令 | 1.0.0 | cmd | 单机 |
| winrm_execute | WinRM 执行命令 | 1.0.0 | cmd | 单机 |

### 3.5 仍在内核（白名单）

| id | 落点 | 说明 |
|----|------|------|
| ssh_batch | `remote_tools.py` | 多机 SSH；刻意暂留 |
| winrm_batch | `remote_tools.py` | 多机 WinRM；刻意暂留 |

### 3.6 技能包对照

| skill_pack | 工具 |
|------------|------|
| example | example_echo |
| host | host_list |
| knowledge | kb_* · search_knowledge · get_content |
| document | doc_search · doc_get |
| remote_fs | 全部 remote_* 文件面 |
| remote_shell | remote_run · ssh_execute · winrm_execute |

---

## 4. 新增工具最短路径

1. 复制 `tools/plugins/example_echo/`（本地）或任意同 `type` 的 remote_*（主机）。
2. 改 `manifest.yaml`：`name`=目录名、`parameters`、`usage_example`、`skill_pack`。
3. 实现 `handler.py`：本地调 `*_tools`；主机调 `host_plugin_delegate`。
4. 主机新工具若需覆盖同名内置：加入 `MIGRATED_HOST_PLUGIN_TOOLS`（或本地名单）。
5. 重启 Assistant；看 `/demo/tools-marketplace`；发 `<<<REMOTE_TOOL>>>` 验证。
6. 补单测或挂到 `test_plugin_scenario_chains.py` 故事链。

**不需要**改 `orchestrator.py` / `config.py`（常规情况）。

---

## 5. 代码与文档索引

| 路径 | 职责 |
|------|------|
| `terminal/tools_plugin_loader.py` | 发现、名单、confirm、执行打包 |
| `terminal/mobile/host_plugin_delegate.py` | 主机委托 |
| `terminal/mobile/remote_tools.py` | 白名单、confirm、pending、内核执行 |
| `terminal/tools_catalog.py` | 市场 catalog / packs |
| `tools/plugins/README.md` | 目录速查 |
| [tool-plugin-architecture.md](./tool-plugin-architecture.md) | 架构详设 |
| [host-plugin-contract.md](./host-plugin-contract.md) | 主机契约详设 |
| [tool-marketplace-phase6.md](./tool-marketplace-phase6.md) | 市场 Phase 6 |
| [tool-plugin-integration-tests.md](./tool-plugin-integration-tests.md) | 场景集成测试 |
| [extending-agent-tools.md](./extending-agent-tools.md) | 扩展说明书 / Hello World |
| [context-data-unit-architecture.md](./context-data-unit-architecture.md) | **CDU**：主机选择等上下文单元（非工具） |
| ADR-0003 | remote_tools 与 OPS 共存 |

---

## 附录 A · 工具 vs 上下文数据单元

| | 工具 | CDU（如 HostTargets） |
|--|------|----------------------|
| 调用方式 | `<<<REMOTE_TOOL>>>` | UI / `PUT /api/context-units/*` |
| 生命周期 | 单次执行 | **用户级**持久（跨会话；local + server） |
| 选机 | 不负责「当前选中」 | **权威选中态** |
| `host_list` | 仅 ACL 目录发现 | 不改选中态 |
| 空选 | — | 依赖主机 → `need_host`；不静默挑机 |

身份分桶：仅 `external_user_id`（无 role 层）。UI：对话壳顶栏槽，非独立配置页。  
详见 [context-data-unit-architecture.md](./context-data-unit-architecture.md) §3–§6。

---

## 6. 交付勾选（签字用）

（本册为交付基线；勾选后可归档。）

- [ ] 本册 §2 迁移清单与仓库 `MIGRATED_*` 一致（25 插件 + 2 batch 暂留）
- [ ] `pytest tests/test_tool_plugins.py` 通过
- [ ] `pytest tests/test_plugin_scenario_chains.py` 通过
- [ ] `/api/tools/catalog` 含 `phase: 6` 与 `packs`
- [ ] `/demo/tools-marketplace` 可筛选技能包与版本
- [ ] 已知限制已传达：batch 未插件化；dependencies 仅展示
