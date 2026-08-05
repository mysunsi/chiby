# 修复闭环 API 说明（closure-execute）

## 修复闭环 API 注意事项

### `mirror_session_id` 要求（远端主机）

远端主机上的受控闭环流式接口：

`POST /api/hosts/{host_id}/closure-execute/stream`

**推荐（Web 终端场景下视为必需）**：在 **JSON 请求体** 中传入 `mirror_session_id`，其值为**当前浏览器 Tab 对应的终端会话 id**（与 WebSocket `/ws/terminal/{session_id}` 中的 `session_id` 一致）。

示例（请求体字段，**非** URL 查询参数）：

```json
{
  "command": "your-command",
  "success_mode": "both",
  "max_fix_attempts": 3,
  "mirror_session_id": "此处填当前 Tab 的 session_id"
}
```

若**不传** `mirror_session_id`（或会话不存在）：

- 右侧聊天区 **纵向时间线** 无法通过 WebSocket 的 `repair_*` 消息驱动（步骤条不更新或保持占位）。
- **左侧终端**也不会收到闭环的镜像输出（`closure_mirror`），用户需依赖 **SSE 返回体中的 `io`/`step` 事件**或下方折叠的原始输出流观察过程。
- **官方 Web 终端**（`terminal/web/index.html`）在调用 `runClosureExecuteRequest` 时已 **固定附带** 当前 `sid` 作为 `mirror_session_id`；第三方/脚本调用若需与 UI 一致，请自行传入。

服务端在 **未传 `mirror_session_id`** 调用 **`/closure-execute/stream`（host）** 时会 **打 WARNING 日志**，便于排查集成问题。

### 获取 `session_id`（浏览器）

当前 Tab 的会话 id 由终端前端在创建/切换会话时持有（例如全局 `activeSession` 或会话列表中的 id）。发起远端闭环请求时，将 **同一 id** 填入 JSON 的 `mirror_session_id` 即可，**不需要**也不支持用查询串 `?mirror_session_id=` 替代请求体字段（除非你在自己的网关中做了一层转换，仍应对齐为 body 字段）。

### `cancel_repair` 与时间线

停止修复通过已连接的 WebSocket 发送：

`{"type":"cancel_repair","data":{"repair_job_id":"<与 SSE meta 中 trace_id/repair_job_id 一致>"}}`

`repair_job_id` 与单次闭环的 `trace_id` 一致。时间线展示依赖 `mirror_session_id`；**取消信号在服务端登记后**可与是否镜像解耦，但 **UI 上的「停止修复」按钮**仍依赖前端页面逻辑与连接状态。

---

## 后续行动（结论）

1. **服务端**：远端 `POST .../hosts/.../closure-execute/stream` 在 body 未含有效 `mirror_session_id` 时 **记录 WARNING**（已实现则以此文档为准做运维核对）。
2. **官方前端**：构造远端执行请求时 **强制附带** 当前 Tab 的 `session_id` 作为 `mirror_session_id`（当前实现已附带）。
3. **产品范围**：**不实现**「无 mirror 时仅靠纯 SSE 推导同一时间线」；若未来有「纯 API 调用也要时间线」的需求，再单独立项（SSE 推导 / 专用调试页）。

---

## Replay Bundle（数字孪生 / 可审计回放）

每次 **`closure-execute` 同步或 SSE 流正常结束**时，服务端尝试将本次闭环写入 **`data/replay_bundles/{trace_id}.json`**（与 `trace_id` 同源）。可通过 REST 拉取：

| 接口 | 说明 |
|------|------|
| `GET /api/replay-bundles/{trace_id}` | 返回完整 JSON 包 |
| `GET /api/replay-bundles?limit=50` | 按修改时间倒序的摘要列表 |

**同步 JSON 响应 / SSE `done` 事件**中会增加可选字段：`replay_bundle_saved`、`replay_bundle_href`（例如 `/api/replay-bundles/cl_xxx`）。

环境变量：`OPS_REPLAY_BUNDLE=0` 关闭落盘；`OPS_REPLAY_MASK_HOSTADDR=1` 在 meta 中隐藏具体 IP/域名。

---

## KB 候选队列（闭环 → 人工「一点批准」入库）

闭环 **成功**（`success_initial` / `success_after_fix`）且未设置 `OPS_KB_PENDING_ON_CLOSURE=0` 时，服务端生成 **`KBPendingCandidate`** 写入 `kh_kb_pending_candidates` 表，并在同步响应 / SSE `done` 中返回 `kb_pending_candidate_id`、`kb_pending_href`。

| 接口 | 说明 |
|------|------|
| `GET /api/kb/pending` | 列出候选，`status=pending\|approved\|rejected\|all` |
| `GET /api/kb/pending/{id}` | 单条详情（含脱敏命令链、输出摘要、主机画像） |
| `POST /api/kb/pending/{id}/approve` | 批准入库 → 写入正式 `KBEntry`（`source=closure_approved`） |
| `POST /api/kb/pending/{id}/reject` | 拒绝（可选 reason） |

批准请求体可覆盖：`title`、`category`、`extra_tags`、`reviewed_by`。

与 **`archive_kb`**（JSONL 占位归档）并行：**候选队列**面向可检索 KB + 人工把关；占位归档面向原始轨迹追加。

---

## 相关文档

- 纵向时间线交互：`docs/命令修复闭环（自动修复阶段）设计.md`（含 **时间线渲染前提** 说明）。
