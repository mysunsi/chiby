以下是为 `ai-ops-assistant` **命令集及UI输出设计汇总执行功能** 的详细设计文档。

---

# 需求：命令集汇总执行（Command Aggregation & Execution Panel）

## 1. 设计目标
- **命令合并**：将 AI 生成的多个单步命令合并为一条可直接复制执行的单条命令，消除分步交互。
- **面板化执行**：在聊天输出区形成独立的“命令执行面板”，统一管理思考、确认、执行、结果反馈全流程。
- **风险控制**：中高风险命令集启动二次确认，低风险直接执行。
- **视觉流畅**：思考动效、实时输出流、执行结果状态图标化，体验连贯。

---

## 2. 核心数据结构

### 2.1 命令集对象 `CommandSet`
```ts
interface CommandSet {
  combinedCommand: string;      // 合并后的单条命令
  os: 'linux' | 'windows';     // 目标操作系统
  shell: 'bash' | 'cmd' | 'powershell';  // 终端类型
  commands: string[];           // 原始命令列表（用于展示或调试）
  risk: 'LOW' | 'MEDIUM' | 'HIGH';
  description: string;          // 命令集说明（如“清理/var/log日志”）
}
```

### 2.2 执行状态枚举
```
IDLE          → 组件初始化（无面板）
THINKING      → AI 生成命令集中
PENDING_CONFIRM → 展示命令集，等待用户确认
EXECUTING     → 命令正在执行
SUCCESS       → 执行成功
FAILED        → 执行失败，进入修复闭环准备
```

---

## 3. 命令合并规则（后端 / 前端可选实现）

**建议由后端在生成执行计划时完成合并**，输出 `combinedCommand`，前端直接使用。

### 3.1 合并规则
- **顺序执行**：
  - Linux/bash: `cmd1; cmd2; cmd3`
  - Windows CMD: `cmd1 & cmd2 & cmd3`
  - PowerShell: `cmd1; cmd2; cmd3`
- **条件依赖**：
  - 若命令间有明确的前置依赖（如删除文件前需检查存在性），统一使用 `&&` 连接（满足 shell 语义：前一个成功才执行下一个）。
  - 后端可在计划中标记依赖关系，生成时按需混合：`cmd1; cmd2 && cmd3`。
- **转义**：
  - 确保特殊字符被正确转义，避免单条命令注入风险。

### 3.2 示例
```
原始:
  1. cd /var/log
  2. du -sh .
  3. find . -name "*.log" -mtime +30 -delete

合并后（Linux）:
  cd /var/log && du -sh . && find . -name "*.log" -mtime +30 -delete
```

### 3.3 前端回退
若后端未返回合并命令（兼容老版本），前端可自行拼接：
- 在风险为 LOW/MEDIUM 且无复杂依赖时，根据 os 类型用分隔符拼接。

---

## 4. UI 组件设计：`CommandExecutionPanel`

### 4.1 整体结构
```
┌─────────────────────────────────────────────┐
│  🤖 AI正在思考...                           │  ← 标题栏
│  ───────────────────────────────            │
│  (思考动效)                                  │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  ✅ 请确认命令集                            │  ← 标题栏
│  ───────────────────────────────             │
│  cd /var/log && du -sh . && find ... -delete │  ← 代码块（可滚动）
│                              📋 复制         │
│  ───────────────────────────────             │
│           [ 开始 ]   [ 取消 ]                │  ← 右下角按钮
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  ⚡ 当前命令正在执行中...                    │  ← 标题栏
│  ───────────────────────────────             │
│  2.1G /var/log                              │  ← 输出区域，流式追加
│  find: ...                                  │
│  ───────────────────────────────             │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  ✅ 命令已完全执行成功                       │  ← 绿色勾
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  ❌ 命令执行失败，正在进入命令修复闭环        │  ← 红色叉
│  (自动跳转至修复状态机)                      │
└─────────────────────────────────────────────┘
```

### 4.2 状态驱动标题与内容

| 状态 | 标题内容 | 下方内容 | 按钮区 |
|------|---------|---------|--------|
| `THINKING` | `🤖 AI正在思考...` 带闪烁动画 | 脉动波纹动画 / 骨架屏 | 无 |
| `PENDING_CONFIRM` | `✅ 请确认命令集` | 命令代码块 + 复制按钮 | `[ 开始 ]` `[ 取消 ]` |
| `EXECUTING` | `⚡ 当前命令正在执行中...` | 增量输出终端视图 | 无（输出区自动滚动） |
| `SUCCESS` | `✅ 命令已完全执行成功` | 可选：折叠的输出 | `[ 新任务 ]` `[ 保存模板 ]` |
| `FAILED` | `❌ 命令执行失败，正在进入命令修复闭环` | 错误摘要 | 自动进入修复流程 |

### 4.3 复制功能
- 命令代码块右侧始终显示 `📋 复制` 图标按钮（`PENDING_CONFIRM` 和 `EXECUTING` 后皆可复制）。
- 点击后调用 `navigator.clipboard.writeText(combinedCommand)`，并给予短暂“已复制”提示。
- **即便在执行中/完成后，用户依然可以复制命令**，满足高级用户需求。

### 4.4 开始与取消按钮逻辑
- **开始**按钮：
  - 首先检查 `risk` 等级：
    - `LOW`：直接发送 `execute_command_set` 消息，状态跳转至 `EXECUTING`。
    - `MEDIUM` 或 `HIGH`：弹出二次确认弹窗（见第5节）。
  - 二次确认通过后，触发执行。
- **取消**按钮：
  - 清空该命令集面板，状态回到 `IDLE`，恢复普通聊天界面。

### 4.5 执行过程流式输出
- 后端通过 WebSocket 发送 `command_output` 消息（已有），前端将每次输出的 `data` 追加到输出区域。
- 输出区域使用 `<pre>` 或终端模拟组件，保留 ANSI 颜色（可选），自动滚动到底部。
- 执行期间禁用所有其他命令操作，防止冲突。

### 4.6 执行完成处理
- 收到 `command_finished` 消息，携带 `exit_code`：
  - `exit_code === 0`：状态 → `SUCCESS`。
  - `exit_code !== 0`：状态 → `FAILED`，触发 `TaskStateMachine` 的 `Healing` 状态（自动或提示进入修复闭环）。面板可暂时保留，同时顶部状态栏更新。

---

## 5. 二次确认弹窗（中高风险专用）

**触发条件**: 命令集 `risk` 为 `MEDIUM` 或 `HIGH`。

**弹窗样式**:
```
┌──────────────────────────────────────┐
│  ⚠️ 中风险操作确认                   │
│                                      │
│  即将执行以下命令集，风险等级：🟡 中  │
│  命令: cd /var/log; sudo rm -rf ...  │
│                                      │
│  影响: 可能删除系统日志，导致服务异常 │
│                                      │
│     [ 确认执行 ]    [ 取消 ]         │
└──────────────────────────────────────┘
```
- 展示风险级别（颜色标签）、命令摘要、影响简述。
- **不是强确认**：无需输入字符或勾选，仅普通确认（因为命令集已经合并展示，用户可清晰看到完整命令，且取消成本低）。
- 点击“确认执行”关闭弹窗并执行；点击“取消”则返回 `PENDING_CONFIRM`，不执行。

---

## 6. 与现有状态机及模块的整合

### 6.1 状态机对接
`TaskStateMachine` 中加入新事件：
- `VIEW_COMMAND_SET`：从 `Generating` 进入 `PlanPreview`（这里用命令集面板替代原来的分步预览卡，状态可复用 `PlanPreview` 或新建 `CommandSetPreview`）。
- `EXEC_COMMAND_SET`：进入 `Running` 状态。
- `COMMAND_SET_COMPLETE`：转 `AllCompleted` 或 `StepFailed`（根据 exit code）。

### 6.2 WebSocket 消息
**新消息** `command_set_execute`（前端发送）：
```json
{
  "type": "command_set_execute",
  "data": {
    "combined_command": "cd /var/log && du -sh . && ...",
    "os": "linux",
    "shell": "bash"
  }
}
```

**后端响应**：
- 持续发送 `command_output`（格式不变）。
- 最后发送 `command_set_finished`：
```json
{
  "type": "command_set_finished",
  "data": {
    "exit_code": 0,
    "stdout_summary": "2.1G /var/log",
    "stderr_summary": null
  }
}
```

### 6.3 后端改动
- **命令合并逻辑**：在生成执行计划时，新增 `aggregate_commands(plan, os, shell)` 方法，返回 `combinedCommand`。
- **执行器**：`terminal/executor.py` 中新增处理 `command_set_execute` 消息的入口，直接创建一个 shell 进程执行合并后的命令，并流式推出输出。
- **风险重新评估**：合并后的整体风险应取原各步骤风险的最高值，并可能上调（如果合并不当会增加风险）。但初期可直接取最高。

### 6.4 前端改动
- 移除或保留原有的分步计划预览卡，替换为 `CommandExecutionPanel`。
- 当收到包含 `combined_command` 的 `llm_response` 或 `plan_preview` 消息时，渲染该面板。
- 复制功能独立为可复用按钮组件 `CopyButton`。
- 二次确认弹窗 `RiskConfirmDialog`，可复用之前的模态框但移除强校验。

---

## 7. 实施步骤（Cursor 任务序列）

### Phase 1: 命令合并后端 & 消息协议
1. 后端实现 `aggregate_commands` 方法，按 OS/Shell 规则拼接。
2. 在 `llm_response` 或任务生成阶段，返回字段 `combined_command`、`os`、`shell`。
3. 新增消息 `command_set_execute` 和 `command_set_finished`，执行器兼容。
4. 测试合并正确性。

### Phase 2: 命令执行面板 UI
1. 创建 `CommandExecutionPanel.vue` 组件，内部状态机（IDLE/THINKING/PENDING/EXECUTING/SUCCESS/FAILED）。
2. 实现各个状态的模板，包括思考动效、命令展示、复制按钮、开始/取消按钮、输出区域。
3. 集成 `CopyButton`，实现复制反馈。
4. 连接 WebSocket：发送 `command_set_execute`，接收输出并追加，完成时处理成功/失败状态。
5. 中高风险二次确认弹窗组件。

### Phase 3: 与聊天输出栏整合
1. 修改聊天区消息渲染逻辑：当消息包含 `commandSet` 类型时，渲染 `CommandExecutionPanel` 而非普通文本。
2. 处理取消操作：隐藏面板，不发送任何后端请求。
3. 处理失败后进入修复闭环的过渡（调用现有 `startHealing` 或跳转）。

### Phase 4: 样式与动效
1. 思考动画（加载条/点闪烁）。
2. 标题栏图标颜色变化。
3. 输出自动滚动及 ANSI 颜色支持（可选）。
4. 整体响应式适配。

---

## 8. 注意事项
- 命令合并时要注意**环境变量预设**：如果原始命令中有 `export VAR=xxx` 或 `cd`，必须放在前面并用 `&&` 保证上下文传递。
- 如果命令中本身含有分号或特殊字符，需要合理转义或使用更安全的执行方式（如临时脚本文件）。
- 用户依然可以手动复制命令到本地终端执行，该交互不阻塞面板内执行。
- 执行过程中若 WebSocket 断开，应显示连接丢失提示，并允许重连后查看结果。

---

已按你的要求在前端去掉分步计划预览与交互，并统一在 plan_cancel 后调用 planFlowSetActive(false)。变更均在 terminal/web/index.html。

1. handleLLMPlan（计划卡片）
标题改为 「命令集（合并执行）」。
删除：步骤列表 <ol>、「计划执行与验证」折叠区、「逐步执行 / 一键执行 / 取消」、编辑命令 与 plan_edit_save。
保留：说明文案 + command_set 面板（复制 / 开始 / 取消）。
兜底：若没有可渲染的命令集（无 command_set 或 JSON 编码失败），只显示说明 + 「取消计划」（仍走 plan_cancel → cancel_plan）。
2. plan_cancel
在原有 hidePlanFlowOverlay 之后增加 planFlowSetActive(sid, false)，与 cmd_set_dismiss 等行为一致。
3. 点击逻辑
已移除：plan_gated、plan_batch、plan_edit_save、plan_retry_open、plan_fail_autofix、plan_step_ok、全部 plan_stall_*、plan_danger_ok。
保留：plan_cancel（用于兜底「取消计划」）。
4. WebSocket / 辅助函数
plan_danger / plan_step：不再出卡片，只发一条系统说明，并 planFlowSetActive(false)。
step_command_result / verification / plan_progress：改为仅 appendSessionAiChat 文本，不再挂载到分步卡片、不再触发分步闭环按钮。
删除：appendPlanStreamLine、分步「卡住」计时与面板、appendVerificationToChat、appendStepCommandResultToChat、injectPlanFailureNlIfPresent、buildPlanFailureNlInner 等分步专用逻辑。
findLastPlanCardInLog：用于复盘快照；appendPlanClosureSnapshotCard 会优先用旧版 data-ai-plan-stream，否则用 命令集 .cmd-set-out。
handlePlanEnd：不再写流式 pre，把摘要并进系统消息，并 planFlowSetActive(false)。

- 说明：后端仍可能发送分步相关消息；现在右侧只以系统消息展示，不再提供分步 UI。若你希望服务端也停用 approve_plan / step_ok 等协议，需要再改 terminal/main.py 等，本次未动后端。