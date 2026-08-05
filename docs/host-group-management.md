# 主机与分组管理 · 落地实现说明

> **状态：** 已交付（静态组）· 动态组 / 规模化列表 = 路线图  
> **核对日期：** 2026-08-05  
> **作准顺序：** 本文 + 源码 > 旧文 [design-ssh-terminal-ops.md](./design-ssh-terminal-ops.md)（其中 SQLAlchemy `HostGroup` 方案**未落地**，现行为 JSON 静态组）  
> **用户侧短说明：** `packages/chibyterm/web/help/*/fleet.md`

---

## 1. 结论（先读这段）

| 维度 | 现状 |
|------|------|
| 主机权威存储 | `data/hosts.json`（内存 `_HOST_STORE`） |
| 分组权威存储 | `data/host_groups.json`，**仅静态组**（显式 `host_ids`） |
| 执行时选机 | **一律展开为 `host_ids`**；组名 / `group_id` 主要用于展示与提示词 |
| 动态组 / 标签表达式 | **未实现**（见根 `CHANGELOG.md` 已知限制） |
| 多主机规模化 | **列表层已分页/过滤**；分组仍为静态选机快捷方式；适合撑到几百台 UI |

**一句话：** 分组是 Fleet / 排查的「快捷选机」，不是运行时绑定实体；定时任务、ACL、多机诊断都不「跟组」。

---

## 2. 数据模型

### 2.1 Host

- 模型：`packages/chibyterm/models/app.py`（`Host` / `HostCreate` / `HostUpdate`）
- 文件：`data/hosts.json`（写前备份 `hosts.json.bak`；密码可 `ENC$` 加密，见 `chibycore` 凭据模块）

| 字段族 | 字段 | 说明 |
|--------|------|------|
| 身份连接 | `id`, `name`, `host`, `port`, `username`, `password`, `conn_type` | SSH / WinRM |
| 分组相关 | `tags: list[str]` | 自由标签；**intent-broadcast** 按 tag 选机 |
| | `labels: dict[str,str]` | 键值（如 env/role）；表单可写，**尚无动态组消费者** |
| 状态 | `status` | `online\|offline\|busy\|unknown`；测连成功可标 online |
| 其它 | `distro_profile`, WinRM/SSH key 字段, `description`, `is_active` | 发行版指纹等 |

规范化辅助：`packages/chibyterm/host_groups.py` 内 `normalize_tags` / `normalize_labels` / `normalize_host_status`。

### 2.2 HostGroup（静态组）

- 逻辑：`packages/chibyterm/host_groups.py`
- 文件：`data/host_groups.json` → `{ "groups": [...], "updated_at": ... }`
- **无独立 Pydantic 模型**；`normalize_group()` 强制 `"type": "static"`

| 字段 | 说明 |
|------|------|
| `id` | `grp_` + hex |
| `name` | ≤80 字符 |
| `type` | 恒为 `"static"` |
| `host_ids` | 显式成员；去重保序 |
| `icon` / `color` | 展示用 |
| `created_at` / `updated_at` | ISO 时间 |

删除主机时：`remove_host_from_all_groups` 级联从各组剔除该 id。

### 2.3 会话 / CDU 中的「范围」

| 结构 | 路径 | 作用 |
|------|------|------|
| `HostScopeView` | `packages/chibyterm/context_units/host_targets.py` | 展示名：`组名（N台）` 或 `已选 N 台主机` |
| CDU `host_targets` | `data/context_units/.../host_targets.json` | `host_ids` + 可选 `group_id` / `group_name` + `bound_host_id` |
| `ConversationState` | `models/session.py` | `ui_host_ids` / `ui_host_group_id` / `ui_host_group_name`（组字段注释：**展示 / 提示词；非强制过滤**） |

---

## 3. 三条并行选机语义（易混淆）

产品里同时存在三套「像分组」的概念，落地消费路径不同：

| 机制 | 依据 | 典型入口 | 执行时存什么 |
|------|------|----------|--------------|
| **静态主机组** | `host_groups.json` 成员列表 | Fleet 组芯片、「主机分组」模态 | 展开后的 `host_ids`（组元数据可选保留） |
| **Host.tags** | 主机上字符串标签 | `POST /api/intent-broadcast/preview\|dispatch` | 按 tag 匹配到的主机集合 |
| **Host.labels** | 键值标签 | 主机表单 | **暂无选机消费者**（动态组路线图） |

掌上 ACL（`proprietary/chiby_mobile`）：白名单是 **host_ids** 或 `*`，**不能按组授权**。

---

## 4. HTTP API（开源）

挂载于 `packages/chibyterm/main.py`：

| Method | Path | 作用 |
|--------|------|------|
| GET | `/api/hosts` | 列表；可选 `page`/`size`/`q`/`tag`/`label`/`status`（见 §4.1） |
| POST | `/api/hosts` | 创建（含 tags/labels/status） |
| GET/PUT/DELETE | `/api/hosts/{host_id}` | 单机读写删（删时级联组） |
| POST | `/api/hosts/test-connection` | 测连；可回写 status |
| POST | `/api/hosts/{id}/probe-distro` | 发行版探测 |
| GET | `/api/host-groups` | `{ groups: [...] }` |

#### 4.1 `GET /api/hosts` 分页与检索（2026-08-05）

| 参数 | 说明 |
|------|------|
| `page` | 从 1 起；**不传则全量**（`page`/`size`/`pages` 为 null） |
| `size` | 默认 20，最大 100（仅当传了 `page`） |
| `q` | 模糊匹配 name / host / id |
| `tag` | tags 精确匹配（大小写不敏感） |
| `label` | `key=value` 精确匹配 labels |
| `status` | online\|offline\|busy\|unknown |
| `prefer_ids` | 逗号分隔主机 id；**过滤后、分页前**将这些主机置顶（最多 500） |

响应：`{ items, total, page, size, pages }`。

Fleet 范围面板与主机分组弹窗在拉取分页列表时会把当前已选 id 传给 `prefer_ids`，因此选中分组或勾选主机后，已选项会出现在第 1 页最前，而不是散落在各页。

| Method | Path | 作用 |
|--------|------|------|
| POST | `/api/host-groups` | 创建组 |
| PUT/DELETE | `/api/host-groups/{group_id}` | 更新（含成员）/ 删组 |
| GET | `/api/host-groups/{group_id}/hosts` | 解析仍有效的成员主机 |

相关但非「组 CRUD」：

| Method | Path | 选机方式 |
|--------|------|----------|
| Fleet / broadcast schedules | `/api/broadcast/schedules*` | 持久化 **展开后的 `host_ids`**，不存 group |
| intent-broadcast | `/api/intent-broadcast/*` | `tag` ∪ `host_ids`（`chibycore/intent_broadcast`） |

闭源（可选插件）：`PUT /api/context-units/host_targets`、`POST /api/mobile/demo/targets` 等可带 group 元数据。

---

## 5. UI 与使用路径

### 5.1 管理入口

- 主 SPA：`packages/chibyterm/web/index.html`
- 顶栏 **「+」→ 主机分组**：静态组 CRUD；成员列表 **搜索 + 分页**（跨页勾选草稿后保存）
- 主机表单：编辑标签 / 属性 / status；测连
- 主机下拉 / Fleet 范围：共用 `GET /api/hosts?page&q&tag&status&prefer_ids…`；Fleet/分组编辑会传已选 id 做置顶

### 5.2 Fleet（静态组主消费路径）

1. 右侧 **Fleet** 模式打开「目标范围」面板。  
2. 点**组芯片** → 将该组 `host_ids` 并入本地 `_fleetScope`（localStorage `chiby.fleetScope.v1`，含 `host_ids` / `group_ids`）。  
3. 用户手改勾选主机后，通常**清空 `group_ids`**（组只是快捷填充）。  
4. 即时群发（**范围选机**）：按主机 **oneshot** 直连执行，**不为**未打开主机批量创建终端 Tab（保留至少一个 Tab 看预览/进度即可）。勾选「仅已打开 Tab」时仍走各会话 PTY。  
5. **「排查」**：`PUT` CDU `host_targets`；**仅当恰好选中一个组**时写入 `group_id` / `group_name`，再打开掌上 IM（顶栏徽章用 `HostScopeView.display_name`）。

帮助文案：`packages/chibyterm/web/help/zh-CN/fleet.md`。

### 5.3 掌上 IM

- 页：`packages/chibyterm/web/mobile_im_demo.html`
- 顶栏多选 `host_ids`；可展示/保留 Fleet 传入的组名
- **不提供** `/api/host-groups` 管理 UI

### 5.4 定时任务

- `packages/chibyterm/broadcast_schedule.py`
- 保存时写入**当时展开的 `host_ids`**
- 之后若组员增删，**任务不会自动刷新**（文档亦写明「定时任务用保存的 host_ids」）

### 5.5 多机诊断

- `packages/chibyterm/multihost_diag.py`：按传入 `host_ids` 聚合取证
- 不解析 group；组信息只影响文案 / 相似案例展示名

---

## 6. 数据流（示意）

```text
data/hosts.json  ──成员引用──▶  data/host_groups.json (type=static)
        │                              │
        │                              ▼ 展开
        ├──────────────▶  Fleet UI (_fleetScope)
        ├──────────────▶  CDU host_targets / ConversationState
        ├──────────────▶  broadcast_schedules (仅 host_ids 快照)
        └──────────────▶  IM ui_host_ids (+ 可选 group 展示字段)
                                 │
                                 ▼
                    SSH/WinRM 批量 · multihost_diag · 闭环
                    （平行：intent-broadcast 走 Host.tags）
```

---

## 7. 主机多了怎么管（能力矩阵）

| 能力 | 现状 | 影响 |
|------|------|------|
| 服务端分页 / 过滤 | **已交付**（`page`/`size`/`q`/`tag`/`label`/`status`） | UI 默认分页；不传 page 仍全量兼容 |
| 前端搜索 | Fleet 范围面板可按名/地址滤 | 主机下拉仍偏全量渲染 |
| 列表虚拟化 | 无 | DOM 随主机数涨 |
| 批量导入 / 导出 | 无专用 API | 靠手填或改 JSON |
| 批量改 tags/labels/组 | 组模态可改成员；主机逐台 | 运维成本高 |
| 批量测连 / 健康视图 | 仅单机 `test-connection` | 无舰队巡检台 |
| 动态组 | 未做 | `labels` 闲置 |

**当前适合：** 十到几十台、手工静态组。  
**规模焦虑本质：** 分组只是「选机快捷方式」，不是「管理单元」；上百台时全量 JSON + 全量 API + 前端局部搜索会先在 UI 崩，再蔓延到执行层。

---

## 8. 规模化路线决议（2026-08-05）

> **若只能砍一刀：先做建议一（分页与检索）。**  
> 不依赖分组模型变更；落地后列表可撑到几百台，并为动态组「按条件查主机」复用同一套查询能力。

### 建议一（首选落地）· 分页与检索 — P1 列表层

解决「眼前痛」：把 `GET /api/hosts` 从全量返回升级为可检索分页列表。

| 能力 | 约定（草案） |
|------|----------------|
| 分页 | `GET /api/hosts?page=1&size=50`，响应带 `total`（及当前页 `items`） |
| 过滤 | `q`（名称/IP 模糊）、`tag`、`label`（如 `env=prod` 或成对参数，实现时定一种） |
| 兼容 | 无查询参数时可保留全量行为一段时间，或明确 breaking 并改前端；**禁止**新 UI 再依赖「一次拉全表渲染」 |
| 范围 | **不改**静态组模型；前端主机列表 / Fleet 范围面板适配分页与过滤 |

投入低、见效快；与后续动态组求值共用「条件 → 主机子集」路径。

### 建议二 · tags / labels / 静态组语义统一 — P0/P1 文案与定位

产品语义最大问题是三套机制混淆。锁定差异化：

| 机制 | 定位 | 消费者 | 用户侧文案 |
|------|------|--------|------------|
| 静态组 | 手工维护的选机快捷方式 | Fleet / 排查 / 展示 | **主机分组**（已用） |
| `Host.tags` | 轻量分类（生产 / 测试 / 数据库） | intent-broadcast 按 tag 匹配 | **标签**（自由分类） |
| `Host.labels` | 结构化元数据（`env=prod`, `role=web`） | 暂无 → 动态组路线图 | **属性**（键值对） |

实现侧：表单 label、帮助文档、`fleet.md` / 主机帮助页与 API 描述对齐上述用词；避免 UI 再出现「标签 / 属性 / 分组」混用。

### 建议三 · 动态组（labels 表达式）— 规模化终态（分阶段）

从「手动维护成员」走向「规则驱动」：用户定义如 `env=prod AND role=web`，系统匹配满足条件的主机，**无需手维 `host_ids`**。

| 阶段 | 内容 | 说明 |
|------|------|------|
| 一 | 表达式**存储 + 预览** | 输入表达式 → 返回匹配主机列表；**不接入** Fleet / 定时 / 诊断执行 |
| 二 | 动态组作**范围源** | Fleet / 定时任务 / 诊断支持选用动态组；**执行时实时求值** 再落成 `host_ids` |
| 三 | ACL / 权限按动态组 | 在列表与执行稳后再迁权限模型 |

依赖建议一的过滤查询能力；建议二文案先稳住，避免用户把「属性表达式」当成「标签」或「静态分组」。

### 其它（不挡建议一）

| 优先级 | 项 |
|--------|-----|
| P1（可并行靠后） | CSV/清单导入 + 批量打标签/属性 |
| P2 | 定时任务可选「跟随组」vs「快照 host_ids」 |
| P3 | ACL / Pro 按组（静态或动态） |

### 落地顺序（锁定）

```text
① 建议一：/api/hosts 分页 + q/tag/label
    → ② 建议二：文案与帮助对齐（可与①小幅并行）
        → ③ 建议三·阶段一：动态组表达式存储与预览
            → 阶段二：执行路径接入 → 阶段三：权限按组
```

---

## 9. 关键源码与测试

| 路径 | 职责 |
|------|------|
| `packages/chibyterm/host_query.py` | 主机过滤 / 分页 |
| `packages/chibyterm/main.py` | hosts / host-groups HTTP、`_HOST_STORE` |
| `packages/chibyterm/models/app.py` | Host* 模型 |
| `packages/chibyterm/context_units/host_targets.py` | CDU + `HostScopeView` |
| `packages/chibyterm/web/index.html` | 主机菜单、分组模态、Fleet 范围 |
| `packages/chibyterm/web/mobile_im_demo.html` | IM 选机 |
| `packages/chibyterm/broadcast_schedule.py` | 定时任务 host_ids |
| `packages/chibyterm/multihost_diag.py` | 多机诊断聚合 |
| `packages/chibycore/intent_broadcast/analysis.py` | 按 tags 选机 |
| `tests/test_host_groups.py` | 组 CRUD / 级联 / labels |
| `tests/test_multihost_diagnosis.py` | HostScopeView / apply_host_targets |

---

## 10. 相关文档

| 文档 | 关系 |
|------|------|
| [context-data-unit-architecture.md](./context-data-unit-architecture.md) | HostTargets CDU；实现已含 group 字段，文内早期 schema 示例可能未列全 |
| [../CHANGELOG.md](../CHANGELOG.md) | 首次发布能力 + 「动态组见路线图」 |
| `packages/chibyterm/web/help/zh-CN/fleet.md` | 用户操作说明 |
| [design-ssh-terminal-ops.md](./design-ssh-terminal-ops.md) | **历史设计**；DB HostGroup **勿当作现行实现** |

---

## 11. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08-05 | 初版：对照源码梳理静态组落地、三套选机语义、规模化缺口与优先级 |
| 2026-08-05 | 锁定规模化路线：①分页检索 → ②标签/属性/分组文案统一 → ③动态组分三阶段；单刀优先建议一 |
| 2026-08-05 | 落地建议一：`GET /api/hosts` 分页/过滤 + 主机菜单/Fleet 服务端检索；`tests/test_hosts_pagination.py` |
| 2026-08-05 | `prefer_ids`：过滤后分页前置顶已选主机（Fleet/分组）；前端选组/勾选后回第 1 页 |
| 2026-08-05 | Fleet 面板补标签/状态筛选与分页控件；主机分组弹窗补搜索+分页（跨页勾选） |
