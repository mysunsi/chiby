# Chiby 本地知识库使用手册

本文说明 **KnowledgeHub（本地知识库）** 的定位、全生命周期维护，以及在前端（管理页 / 掌上 IM / 桌面终端）中的使用方式，并给出可直接照抄的示例。

> 产品侧名称：**Chiby 知识库**。  
> 技术实现：`chibycore/knowledge_hub` + `/api/kb` + Agent 工具 `kb_*`。  
> **不是** Hermes 上游的 `MEMORY.md` / `memory` 工具；与「主机内存还剩多少」无关。  
> **企业长文档（PDF/手册）**请用 DocHub，见 [doc-hub-user-manual.md](./doc-hub-user-manual.md)（`doc_search` / `/demo/doc-hub`）。

---

## 1. 它是什么、不是什么


| 是                    | 不是                      |
| -------------------- | ----------------------- |
| 运维故障经验、脚本、最佳实践的本地库   | 聊天会话历史                  |
| 按关键词检索、按需注入给 Agent   | 整库塞进 system prompt      |
| 落盘在本机 SQLite（可备份）    | 云端向量库（当前默认）             |
| 人可 CRUD，Agent 可查/可沉淀 | Hermes Memory / USER.md |


三类内容：

1. **知识条目（KB）**：症状 → 根因 → 修复 → 验证（主路径，管理页完整支持）
2. **脚本库（Script）**：可复用命令/脚本（API 完整；管理页可检索与只读查看）
3. **最佳实践（Best Practice）**：流程型文档（API 完整）

默认库文件：`Assistant/data/knowledge_hub.db`。

---



## 2. 入口一览


| 入口           | URL / 位置                                   | 能力                                           |
| ------------ | ------------------------------------------ | -------------------------------------------- |
| **知识库管理页**   | ``` /demo/knowledge-hub；终端侧栏「知识库管理」 ```    | 列表、检索、新建、编辑、删除、详情                            |
| **掌上 IM**    | `/demo/mobile-im`                          | Agent 调 `kb_search` / `kb_get` / `kb_ingest` |
| **桌面终端 NL**  | `/terminal` 右侧模式选「知识库 / 脚本库」               | **仅检索说明**，不下发命令                              |
| **REST API** | `/api/kb/`*；Swagger：`/docs` → KnowledgeHub | 全量 CRUD、评分、导出                                |
| **自动沉淀**     | remediator / 闭环成功案例                        | 成功经验写入（`source` 非 manual）                    |


重启服务后，管理页与 API 才会生效；前端请强制刷新。

---



## 3. 全生命周期

```text
创建 ──► 入库 ──► 检索/引用 ──► 应用（查主机/修复）
  │         │              │
  │         │              ├─► 评分 / 成功失败反馈
  │         │              └─► 修订（编辑）或归档删除
  │         │
  │         ├─ 人工：管理页 / POST /api/kb
  │         ├─ Agent：kb_ingest（智能型/全能型 + 确认卡）
  │         └─ 自动：闭环/自愈成功沉淀
  │
  └─ 备份：复制 knowledge_hub.db 或 GET /api/kb/export
```



### 3.1 创建（新建）

**推荐：管理页**

1. 打开 `/demo/knowledge-hub`
2. 点「新建条目」
3. 填写必填：标题、症状、根因、修复方案
4. 可选：分类、置信度、标签、适用 OS/服务、验证方法、备注
5. 保存 → 列表出现新 `id`

**字段建议：**


| 字段  | 写法建议                      |
| --- | ------------------------- |
| 标题  | 短、可搜：`nginx 连接数过高导致 502`  |
| 症状  | 用户能看到的现象、日志关键字            |
| 根因  | 一句话机制，勿只写「坏了」             |
| 修复  | 可执行步骤或命令（注明风险）            |
| 验证  | 如何确认修好（如 `curl -I` 非 502） |
| 标签  | `nginx,502` 便于检索          |


**API：**

```http
POST /api/kb/kb
Content-Type: application/json

{
  "title": "nginx 连接数过高导致 502",
  "category": "service_ops",
  "symptom": "上游返回 502 Bad Gateway，worker 连接打满",
  "root_cause": "worker_connections 过低或 upstream 超时",
  "remediation": "调大 worker_connections；nginx -t && systemctl reload nginx",
  "verify_method": "curl -I 返回非 502；error.log 无 upstream timed out",
  "tags": ["nginx", "502"],
  "confidence": "medium",
  "applicable_os": ["linux"],
  "applicable_service": "nginx"
}
```

**Agent 沉淀（智能型 / 全能型）：** 聊天里说「把这次处理沉淀进知识库」，Agent 发 `kb_ingest`，界面会弹确认卡，点允许后写入。

### 3.2 查询 / 检索


| 方式     | 操作                                                  |
| ------ | --------------------------------------------------- |
| 管理页    | 顶部搜索框 + 模式（知识/脚本/全部）→「检索」；或分类筛选 +「刷新列表」             |
| 管理页详情  | 点击列表行 → 右侧看全文                                       |
| API 列表 | `GET /api/kb/kb?limit=50&category=service_ops`      |
| API 检索 | `GET /api/kb/search?q=nginx%20502&mode=kb&limit=10` |
| API 详情 | `GET /api/kb/kb/{entry_id}`                         |
| 掌上聊天   | 见第 5 节示例                                            |
| 桌面 NL  | 模式选「知识库」，只返回说明                                      |




### 3.3 编辑

- **管理页**：选中条目 →「编辑」→ 改完保存（`PATCH /api/kb/kb/{id}`）
- **Agent**：当前 **没有** `kb_update`，请用人维页面或 API
- 过时手册务必改「修复/验证」，避免 Agent 按旧方案操作



### 3.4 删除

- **管理页**：详情 →「删除」→ 确认
- **API**：`DELETE /api/kb/kb/{entry_id}`
- **Agent**：不能删除（防误删）



### 3.5 应用与反馈

1. 聊天中引用条目 → Agent 按修复方案出 OPS_PLAN / REMOTE_TOOL
2. 可选反馈（API）：
  - `POST /api/kb/kb/{id}/rate`（0–5 分）
  - `POST /api/kb/kb/{id}/feedback?success=true|false`（累计成功/失败，影响置信度）



### 3.6 备份与导出

- 停服或保证无写入时，复制 `data/knowledge_hub.db`
- 或调用导出接口：`GET /api/kb/export`（若已挂载）做 JSON 备份

---



## 4. 管理页操作速查

地址：`/demo/knowledge-hub`


| 操作   | 步骤                      |
| ---- | ----------------------- |
| 看总量  | 顶栏 chips：KB / 脚本 / 最佳实践 |
| 浏览全部 | 「刷新列表」或清空搜索后回车          |
| 按分类  | 下拉「全部分类」选如「服务运维」        |
| 关键词搜 | 输入词 →「检索」               |
| 新建   | 「新建条目」填表 →「保存」          |
| 编辑   | 点列表行 →「编辑」              |
| 删除   | 详情 →「删除」                |
| 回终端  | 页头链接「终端」；回 IM 用「掌上 IM」  |


分类枚举（与 API `category` 一致）：

`system_monitor` / `user_management` / `package_management` / `service_ops` / `network_ops` / `security` / `database` / `docker_k8s` / `failure_recovery` / `other`

---



## 5. 聊天里怎么用（Agent 工具）



### 5.1 模式与权限


| 模式  | 读库 `kb_search`/`kb_get`    | 写库 `kb_ingest` |
| --- | -------------------------- | -------------- |
| 高效型 | 一般不走 Hermes 规划             | 否              |
| 智能型 | 可以（纯 KB 工具、同轮无 OPS 时可本地执行） | 可以（确认卡）        |
| 全能型 | 可以（REMOTE_TOOL）            | 可以（确认卡）        |


工具协议形态（模型输出，用户一般只需自然语言）：

```text
<<<REMOTE_TOOL>>>
{"tool":"kb_search","q":"nginx 502","mode":"kb","limit":5}
<<<END_REMOTE_TOOL>>>
```

```text
<<<REMOTE_TOOL>>>
{"tool":"kb_get","entry_id":"nginx502abcd"}
<<<END_REMOTE_TOOL>>>
```



### 5.2 语义铁律（务必记住）

- 「内存 / 磁盘 / CPU」→ **目标主机资源**，走 SSH/WinRM
- 「知识库 / 手册 / 库里的经验」→ **KnowledgeHub**
- 禁止指望 Hermes `memory` 工具；掌上已禁用

---



## 6. 使用示例（十条）

下列示例可直接复制到掌上 IM（建议 **智能型** 或 **全能型**，并先绑定目标主机）。

### 例 1：按库排查 nginx 502

> 按我们知识库里 nginx 502 的手册，先查当前主机是不是同类问题，再按修复步骤处理。

预期：`kb_search` →（可选）`kb_get` → 主机侧查 nginx / 日志 → 变更时确认卡。

### 例 2：只查库、先不动手

> 知识库里有没有磁盘 inode 耗尽的处理经验？先列条目和摘要，**先不要执行任何命令**。

预期：仅知识库工具结果，无 OPS / 无远端变更。

### 例 3：拿到 id 后读全文

> 把知识库条目 `nginx502abcd` 的完整修复和验证步骤读出来给我。

预期：`kb_get`；若 id 不对会提示不存在。

### 例 4：按标签/服务搜脚本向经验

> 在知识库里搜和 mysql 从库延迟相关的条目，mode 用 kb，多给几条对比一下。

预期：多条命中列表（标题、分数、snippet）。

### 例 5：桌面终端「仅检索」模式

1. 打开 `/terminal`
2. 右侧 NL 模式选 **「知识库」**
3. 输入：`nginx 502`

预期：右侧流式说明命中条目；**不会**自动下发 shell。

### 例 6：管理页录入后再聊天引用

1. `/demo/knowledge-hub` 新建「WinRM 下 nginx 用 Start-Process -p」
2. 掌上 IM：
  > 按知识库里 WinRM nginx 启动相关条目，检查这台 Windows 主机 nginx 是否按手册启动。

预期：检索命中刚录条目，再按 Windows/WinRM 路径排查。

### 例 7：排障成功后沉淀

> 刚才这套「权限导致 nginx -t 失败 → chmod → reload」已经修好了，请沉淀进知识库，标题你起一个规范一点的。

预期：`kb_ingest` → **确认卡** → 允许后入库 → 可在管理页看到 `source=manual`（或 Agent 写入的 created_by）。

### 例 8：区分「主机内存」与「知识库」

先问：

> 内存还剩多少？

应走目标机 `free -h` / Win32_OperatingSystem，**不要**进知识库。

再问：

> 知识库里有没有「内存泄漏排查」的手册？

才走 `kb_search`。

### 例 9：多主机巡检前先对齐手册

> 先从知识库调出「nginx 健康检查」相关条目，再按手册里的只读检查项，对当前选中的多台主机做一轮巡检。

预期：先 KB，再 OPS_JOB / 批量 REMOTE_TOOL（视模式而定）。

### 例 10：修订过时条目（人维）+ 聊天复验

1. 管理页打开旧条目「使用 service nginx restart」→ 编辑为优先 `reload`，保存
2. 聊天：
  > 再查一次知识库 nginx 重启/重载相关条目，确认现在推荐的是 reload 还是 restart。

预期：检索结果体现修订后的 remediation。

---



## 7. API 速查（维护用）

前缀：`/api/kb`（完整以 `/docs` 为准）


| 方法       | 路径                              | 作用                |
| -------- | ------------------------------- | ----------------- |
| GET      | `/stats`                        | 统计                |
| GET      | `/search?q=&mode=kb|script|all` | 检索                |
| GET      | `/kb`                           | 列表                |
| GET      | `/kb/{id}`                      | 详情                |
| POST     | `/kb`                           | 新建                |
| PATCH    | `/kb/{id}`                      | 更新                |
| DELETE   | `/kb/{id}`                      | 删除                |
| POST     | `/kb/{id}/rate`                 | 评分                |
| POST     | `/kb/{id}/feedback?success=`    | 应用反馈              |
| GET/POST | `/scripts`…                     | 脚本库 CRUD          |
| GET/POST | `/best-practices`…              | 最佳实践              |
| POST     | `/ingest/...`                   | 从 remediator/终端沉淀 |


PowerShell 示例（删除）：

```powershell
Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:<端口>/api/kb/kb/<entry_id>"
```

---



## 8. 质量与运营建议

1. **一条一事**：一个条目只覆盖一种典型故障，勿塞整本运维百科。
2. **标题可搜**：带产品名 + 错误码/现象（`502`、`inode`、`Permission denied`）。
3. **修复可执行**：写清命令与回滚/验证，Agent 才敢跟。
4. **标签稳定**：统一小写英文或固定中文词，避免同义重复。
5. **定期 scrub**：管理页按 `source` / 低置信度清理错条目。
6. **先库后人肉**：重复故障优先「按库处理」，减少临场编命令。
7. **变更走确认**：智能型常规变更、`kb_ingest`、高危操作都会弹卡——先看再点。
8. **备份库文件**：升级或迁移前复制 `knowledge_hub.db`。

---



## 9. 常见问题

**Q：聊天里说了「按知识库」但没查库？**  
A：确认模式为智能型/全能型；服务已重启加载新工具；表述里带「知识库/手册/库里」等词；同轮若既有 OPS 又有 KB，智能型会优先走运维计划——可拆成两轮：先只查库，再动手。

**Q：和 Hermes Lab 里的 Memory 有什么关系？**  
A：无关。Memory 是偏好/长期事实；知识库是运维案例库。掌上禁用 memory 工具，避免和「主机内存」串台。

**Q：脚本能不能在管理页里改？**  
A：当前管理页以 **KB 条目** 为主；脚本请用 `/api/kb/scripts` 或 `/docs`。检索脚本可在管理页模式选「脚本库」。

**Q：数据在哪？多人怎么共享？**  
A：默认单机 SQLite。多机共享需自行迁 PostgreSQL（`DATABASE_URL`）或共享/同步库文件——属部署策略，不在本手册展开。

---



## 10. 相关代码与页面


| 路径                                 | 说明                  |
| ---------------------------------- | ------------------- |
| `chibycore/knowledge_hub/`          | 存储、检索、入库、REST       |
| `terminal/mobile/kb_tools.py`      | Agent 工具实现          |
| `tools/plugins/kb_*/` · `remote_tools.py` | Agent 插件入口 / 白名单与 preamble || `terminal/web/knowledge_hub.html`  | 管理页                 |
| `terminal/web/mobile_im_demo.html` | 掌上聊天                |
| `/demo/knowledge-hub`              | 管理页路由               |
| `/api/kb`                          | REST 前缀             |


---

*文档版本：与 Chiby KnowledgeHub 工具面及* `/demo/knowledge-hub` *管理页同步。*