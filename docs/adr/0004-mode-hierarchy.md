# ADR-0004：模式分层（高效型 / 智能型 / 全能型）

## 状态

已接受（2026-07-20）。实现进度：模式 ID / UI / 按模式强制 `remote_tools` 已落地；**智能型 = 旧高级 OPS 闭环**已恢复；**全能型 A2 回灌闭环**已落地（`_run_a2_closed_loop`）；编程能力开关为后续。

关联：[0003-remote-tools-and-ops-coexistence.md](./0003-remote-tools-and-ops-coexistence.md)（通道与单脑编排）、[0001-hermes-mode.md](./0001-hermes-mode.md)（基础模式定义）。

## 上下文

- 原三模式（运维 / 高级 / 编程）按「场景」划分，用户选择时容易困惑。
- 按「token 消耗 + 自主度」分层：高效型 / 智能型 / 全能型。
- **纠正（2026-07-20）**：智能型必须对齐已打磨的**旧高级模式**（Hermes + OPS_PLAN 闭环 + 变更确认 + 检查点续跑），**不得**用单轮 A2 顶替。A2 单脑与自主闭环留给全能型。

## 决策

### 1. 三模式定义

| 模式 | 对应旧名 | Planner | 通道（ADR-0003） | Token | 自主度 | `remote_tools` | 确认卡 |
|------|---------|---------|------------------|-------|--------|---------------|--------|
| **高效型** | 运维模式 | rules（非 Hermes） | **A1 only** | 少 | 低（逐步确认） | false | 每次执行前 |
| **智能型** | **高级模式（完整保留）** | Hermes | **A1 OPS 闭环** | 适中 | 中（变更确认 + 检查点） | **false** | 变更类操作 |
| **全能型** | Hermes 主排 | Hermes | **A2（单脑 + 闭环）** | 全开 | 高（自主闭环） | true | 仅高危操作 |

### 2. 各模式行为

**高效型（运维模式演进）**
- 规划器：rules；通道 A1；token 最低；每步确认。

**智能型（= 旧高级模式）**
- 规划器：Hermes；通道：**OPS_PLAN → 无头执行 → 回灌 Hermes → 再规划**（`_run_advanced_closure`）
- `remote_tools` **强制 false**，避免 A2 单脑掐掉 OPS 闭环
- 受控变更确认卡；检查点「继续/结束」；断点续跑；可停止
- 适用：故障排查、性能分析、需判断力且要安全闸门的操作

**全能型（Hermes 主排）**
- 规划器：Hermes；通道：A2（`REMOTE_TOOL`）+ **工具结果回灌自主多轮**（`_run_a2_closed_loop`）
- 仅高危弹确认卡；常规变更可自动（`confirm_changes=false`）
- 原样思考流 +「深度思考」收起；不套自研思维链里程碑
- 轮次上限：默认 8（`OPS_MOBILE_A2_LOOP_CAP`）

### 3. 「编程」不再作为独立模式

原「编程模式」降为能力，挂接在智能型 / 全能型之上（智能型侧仍走 OPS/无头；写文件须确认）。

### 4. 与 ADR-0003 的映射

```text
高效型  → remote_tools=false → A1 only（rules）
智能型  → remote_tools=false → A1 OPS 闭环（旧高级）
全能型  → remote_tools=true  → A2 单脑 + 回灌闭环（多机用 batch，非 OPS_JOB）
```

全局 `hermes_bridge.yaml` 的 `remote_tools.enabled` **不能**覆盖会话模式强制值。

## 非目标

- 用单轮 A2 顶替智能型/旧高级体验。
- 高效型启用 A2。
- 全能型完全无确认（高危仍须确认卡）。

## 配置草案

```yaml
# data/hermes_bridge.yaml（注释约定；运行时由 agent_mode 强制）
modes:
  efficient:    { planner: rules,  remote_tools: false }
  intelligent:  { planner: hermes, remote_tools: false }  # 旧高级
  omnipotent:   { planner: hermes, remote_tools: true, confirm_changes: false, closed_loop: true }
```

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-07-20 | 初版：三模式按 token + 自主度分层 |
| 2026-07-20 | 落地：正式 ID、UI、按模式强制 remote_tools |
| 2026-07-20 | **纠正**：智能型 = 旧高级（OPS 闭环，`remote_tools=false`）；全能型独占 A2 |
| 2026-07-20 | 全能型 A2 回灌闭环落地；`confirm_changes` 接线（仅高危确认） |
| 2026-07-20 | 全能型多机改走 A2 batch（不再 `_hermes_plan_multi_host`→OPS_JOB） |
| 2026-07-21 | 路线图修订见 [mobile-modes-to-industrial-milestones.md](../mobile-modes-to-industrial-milestones.md)：执行底座 P0、建议对外名「托管型」 |
