# P0：跨机硬拦截 + Turn Trace

企业级短板补齐（相对「只靠告诉 LLM」）。

## 1. 选机硬拦截

- 位置：`terminal/mobile/remote_tools.py` → `execute_remote_tool_call`
- 规则：Agent **显式**写了 `host`/`hosts`，且归一化后不在顶栏 `selected_host_ids` 内 → **拒绝执行**
  - `error_code`: `host_selection_violation`
  - 不会静默改打到选中机（避免 LLM 以为已打到它写的那台）
- 空 host：仍由 UI 选中 / `default_host_id` 注入（不算越界）
- 降级：环境变量 `OPS_HOST_SELECTION_STRICT=0` 恢复「纠回选中集」旧行为

## 2. Turn Trace MVP

- 模块：`terminal/mobile/turn_trace.py`
- 落盘：`data/turn_traces/{turn_id}.jsonl`
- 开关：`OPS_MOBILE_TURN_TRACE`（默认开）；目录可用 `OPS_MOBILE_TURN_TRACE_DIR` 覆盖
- 核心事件：
  - `user_intent`（回合开始）
  - `host_switch`（切机）
  - `tool_call` / `host_selection_violation`（执行层）
  - 带 `turn_id` 的 `append_mobile_audit` 事件 fan-out（确认卡、remote_tool_exec 等）
- 查询：`GET /api/mobile/demo/turn-trace?turn_id=tur_xxx`

## 3. 与现有审计关系

| 通道 | 用途 |
|------|------|
| `mobile_audit.jsonl` | 全局事件流 |
| `turn_traces/{id}.jsonl` | **单回合**端到端回放 |
| transcript / forensic | 继续可用；Trace 是更干净的按回合主链 |
