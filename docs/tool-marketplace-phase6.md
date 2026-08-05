# 工具市场 Phase 6：发现 / 版本 / 依赖 / 技能包

> 目标：在「目录即插件」闭环上，把市场从「静态清单」升级为可发现、可聚合、可声明依赖的预览面。  
> 前置：Phase 1–5（本地 + 主机只读/写入/命令插件）已落地。  
> **本阶段不改变运行时白名单源**；展示与执行仍分离。

---

## 1. 交付物

| 能力 | 落点 |
|------|------|
| 目录字段 enrichment | `PluginRegistry.as_catalog_rows()`：`version` / `skill_pack` / `loaded` / `type` / `dependencies` … |
| 技能包聚合 | `build_skill_packs()` → catalog.`packs` |
| 过滤 | `filter_catalog(pack, type, loaded_only, q)` |
| API | `GET /api/tools/catalog`（可带 query） |
| | `GET /api/tools/packs` |
| | `GET /api/tools/plugins/{id}` |
| UI | `/demo/tools-marketplace`：包芯片、版本徽章、已加载、依赖数、详情弹层 |
| Manifest 约定 | `skill_pack`；可选 `dependencies: [{id, kind, optional}]` |

---

## 2. Manifest 增量字段

```yaml
name: search_knowledge
version: "1.0.0"
category: knowledge
skill_pack: knowledge          # 缺省回落到 category
dependencies:                  # 声明式，当前仅展示 / 文档；不自动安装
  - id: kb_search
    kind: tool
    optional: true
  - id: doc_search
    kind: tool
    optional: true
```

| 字段 | 含义 |
|------|------|
| `version` | 语义化版本字符串（市场展示） |
| `skill_pack` | 技能包 ID；UI/API 按此聚合 |
| `dependencies` | 工具依赖声明（`kind` 默认 `tool`） |

**不做（本阶段）**：自动解析依赖安装、禁用未满足依赖、远程市场拉取、热重载。

---

## 3. API 一览

### `GET /api/tools/catalog`

完整目录；可选过滤：

| Query | 说明 |
|-------|------|
| `pack` | 匹配 `skill_pack`（或 category） |
| `type` | `local_readonly` / `host_readonly` / `host_write` / `host_command` … |
| `loaded_only` | `true` 时仅保留 `loaded`/`status=loaded` |
| `q` | 关键词 |

响应增量：`phase: 6`、`packs`、`plugin_loaded_count`、`pack_count`、`docs.marketplace`。

### `GET /api/tools/packs`

仅返回技能包列表（`id` / `title` / `tool_ids` / `loaded_count` / `total`）。

### `GET /api/tools/plugins/{id}`

单插件详情（含 `parameters` / `usage_example`；未加载但目录存在时返回扫描结果）。

---

## 4. 技能包约定（当前仓库）

| skill_pack | 示例工具 |
|------------|----------|
| `host` | `host_list` |
| `knowledge` | `kb_*` / `search_knowledge` / `get_content` |
| `document` | `doc_search` / `doc_get` |
| `remote_fs` | `remote_read_file` … `remote_rollback` |
| `remote_shell` | `remote_run` / `ssh_execute` / `winrm_execute` |
| `example` | `example_echo` |

---

## 5. 与运行时的边界

- 市场 / catalog = **发现与文档面**
- 执行仍走：`tools_plugin_loader` + `remote_tools` 白名单 / confirm / host 契约
- `dependencies` **不**参与调度；后续若要做「依赖门禁」，另开阶段

---

## 6. 验收清单

1. `/api/tools/catalog` 含 `packs` 且插件行有 `version` / `skill_pack`
2. `/api/tools/packs` 与 catalog 聚合一致
3. `/api/tools/plugins/host_list` 返回详情
4. `/demo/tools-marketplace` 可按技能包筛选，卡片显示版本与已加载
5. `pytest tests/test_tool_plugins.py` 相关用例通过

---

## 7. 后续（非本阶段）

- contrib → plugins 一键晋升 UI
- 依赖门禁 / 版本冲突提示
- 集成验收套件与交付包清单（原 Phase 6+ 尾项）
