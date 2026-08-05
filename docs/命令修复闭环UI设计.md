以下是**命令修复闭环UI设计**的完整详细设计，可直接交给 Cursor 分阶段实现。

---

# 需求：命令修复闭环（Command Remediation Loop）

## 1. 概述
当命令集执行失败时（`exit_code != 0`），系统进入**闭环修复流程**。该流程优先进行最多 3 次自动修复尝试，若仍未成功，则转为**人工介入模式**，让用户提供修复建议并再次尝试。所有修复步骤被完整记录，支持后续审计。

---

## 2. 状态机扩展

在 `TaskStateMachine` 的基础上增加修复相关状态和事件：

### 2.1 新增状态
- `AutoHealing`：自动修复执行中（可能包含多轮）
- `ManualHealing`：等待用户输入修复建议
- `HealingCompleted`：修复成功，可回流至 `AllCompleted`
- `HealingFailed`：修复彻底失败，任务终止

### 2.2 状态流转
```
StepFailed → AutoHealing (自动开始)
AutoHealing → Running (执行修复命令)
Running → StepSuccess → AutoHealing (继续验证？)
          → StepFailed → AutoHealing (再次尝试) [未达3次]
          → ManualHealing [达到3次]
ManualHealing → Running (用户建议被采纳，新修复命令执行)
Running → StepSuccess → HealingCompleted
          → StepFailed → ManualHealing (再次等待用户输入)
ManualHealing → Aborted (用户点击取消)
```

### 2.3 事件
- `START_AUTO_HEALING`：触发自动修复序列
- `HEALING_ATTEMPT_DONE`：一次尝试完成，携带结果
- `HEALING_FAILED_EXHAUSTED`：三次尝试均失败
- `ASK_MANUAL_INPUT`：进入人工介入
- `MANUAL_SUGGESTION_SUBMIT`：用户提交建议
- `MANUAL_CANCEL`：取消人工修复
- `HEALING_COMPLETED`：闭环成功

---

## 3. 数据结构

### 3.1 修复记录 `RemediationRecord`
```ts
interface RemediationRecord {
  id: string;
  originalCommand: string;
  failureTime: number;
  failureLog: string;            // 失败日志摘要
  attempts: RemediationAttempt[]; // 所有尝试
  totalDuration: number;         // 秒
  finalStatus: 'success' | 'failed' | 'cancelled';
  manualInput?: string;          // 人工介入时的用户建议
}
```

### 3.2 单次尝试 `RemediationAttempt`
```ts
interface RemediationAttempt {
  index: number;                 // 第几次
  strategy: string;              // 修复策略描述
  command: string;               // 实际执行的命令
  exitCode: number;
  outputSummary: string;         // 输出摘要
  success: boolean;
  timestamp: number;
  automated: boolean;            // 是否自动生成
  userSuggestion?: string;       // 若为人工，用户的输入
}
```

---

## 4. 自动修复流程 (Auto-Healing)

### 4.1 后端逻辑
- **入口**：接收到 `command_set_finished` 且 `exit_code != 0`，且当前计划允许自动修复。
- **修复策略生成**：调用 LLM，传入**失败日志、原命令、当前操作系统信息**，要求输出一个**差异化的修复命令**（非重试原命令），并附带策略说明（`strategy`）。为保证差异化，需将之前失败的策略作为历史上下文传递给 LLM。
- **执行**：通过终端执行返回的修复命令，流式输出到前端。
- **次数**：最多 3 次自动尝试。每次尝试后记录结果，并累加尝试计数。
- **终止**：
  - 某次修复命令执行成功（exit 0），并且通过验证（可由用户确认或自动判断），则结束，状态置为成功。
  - 3 次尝试后仍失败，前端推送 `manual_healing_required` 消息，状态变更为 `ManualHealing`。

### 4.2 前端表现
在命令执行面板中，状态转为 `AutoHealing`，标题显示“🔧 正在进行自动修复（第 1/3 次）...”或类似文字。下方输出区域显示修复策略描述和命令执行输出。用户可观察但无法干预（除中止整个计划外）。

---

## 5. 人工介入界面 (Manual Healing UI)

当自动修复耗尽次数时，弹出**独立的人工修复窗口**（模态窗口或独立面板）。

### 5.1 窗口布局
```
┌──────────────────────────────────────────────┐
│  🛠 人工介入修复                     [✕]     │
├──────────────────────────────────────────────┤
│  修复总结报告                                │
│  ────────────────────────────                │
│  原始命令: rm -rf /data/mysql                │
│  失败时间: 2026-05-09 14:22:10                │
│  失败日志: Permission denied                  │
│                                              │
│  自动修复尝试 1/3:                           │
│    策略: 使用sudo提升权限                    │
│    命令: sudo rm -rf /data/mysql             │
│    结果: ❌ 失败 - sudo: command not found   │
│  自动修复尝试 2/3:                           │
│    策略: 切换到root后执行                    │
│    命令: su -c "rm -rf /data/mysql"          │
│    结果: ❌ 失败 - Authentication failure    │
│  自动修复尝试 3/3:                           │
│    策略: 尝试使用pkexec                      │
│    命令: pkexec rm -rf /data/mysql           │
│    结果: ❌ 失败 - pkexec not allowed        │
│                                              │
│  总耗时: 45秒                                │
├──────────────────────────────────────────────┤
│  请输入您的修复建议或命令:                    │
│  ┌────────────────────────────────────────┐  │
│  │ (输入框，最大8191字符)                   │  │
│  └────────────────────────────────────────┘  │
│                                 字符数: 0/8191│
│                                              │
│              [ 再次修复 ]   [ 取消 ]          │
└──────────────────────────────────────────────┘
```

### 5.2 交互约束
- **输入校验**：
  - 内容必须非空（trim后不为空字符串）。
  - 长度不得超过 8191 字符。
  - 不满足条件时，“再次修复”按钮置灰（`disabled: true`），悬停显示 tooltip：“请输入新的修复建议”。
- **操作**：
  - **再次修复**：将用户输入作为 `userSuggestion` 发送到后端，后端生成新的修复命令（结合用户建议），执行并返回结果，同时**更新总结报告**（动态追加人工建议和新尝试记录）。
  - **取消**：发送 `cancel_healing`，关闭窗口，修复流程终结，记录最终状态为 `cancelled`。

### 5.3 动态更新
- 每当一次人工修复尝试完成后，总结报告尾部**动态追加**一条新条目：
  ```
  人工修复尝试 4:
    用户建议: 使用绝对路径 rm 并检查权限
    修复策略: 检查权限后使用绝对路径 rm 命令
    命令: stat /data/mysql && /bin/rm -rf /data/mysql
    结果: ✅ 成功
  ```
- 成功时，自动关闭窗口并在主面板显示“命令修复成功”。
- 失败时，窗口保持打开，输入框清空（或保留以便用户修改），用户可以再次提交新建议，形成新尝试，直到成功或取消。

---

## 6. 闭环验证（Verification）

- **每次修复后自动验证**：修复命令执行后，后端除了看 `exit_code` 外，还可执行一个简短的验证命令（例如原命令的目标检测：`ls /data/mysql` 看是否还存在）。如果验证通过，则标记成功。
- **成功关闭**：成功时，向后端发送 `healing_success`，结束整个修复闭环。任务状态变为 `AllCompleted`。
- **失败累计**：人工阶段无次数限制，但每次失败都会累计尝试次数并追加记录。用户可手动取消。

---

## 7. 可追溯性存储

所有修复记录必须持久化存储，便于审计。建议后端存储到与知识库相关的数据库或 JSON 日志中。

### 7.1 存储接口
- 后端自动在每次尝试（包括自动和人工）后将 `RemediationAttempt` 追加到会话/任务的修复日志中。
- 最终生成 `RemediationRecord` 并保存到 `data/remediation_logs/{task_id}.json` 或写入 SQLite `remediation_history` 表。

### 7.2 审计UI（后续可选）
- 在任务历史中可查看完整修复报告，包括自动和人工步骤。

---

## 8. WebSocket 消息定义

| 消息类型 | 方向 | 数据结构 | 说明 |
|----------|------|----------|------|
| `start_auto_healing` | 后端→前端 | `{ attempt: 1, total: 3 }` | 开始自动修复第n次 |
| `heal_attempt_result` | 后端→前端 | `{ attempt: RemediationAttempt }` | 一次自动修复完成 |
| `auto_healing_exhausted` | 后端→前端 | `{ record: RemediationRecord }` | 3次失败，需人工介入 |
| `manual_healing_prompt` | 后端→前端 | `{ record: RemediationRecord }` | 打开人工修复窗口时发送 |
| `submit_manual_suggestion` | 前端→后端 | `{ task_id, user_input: string }` | 用户提交建议 |
| `manual_heal_result` | 后端→前端 | `{ attempt: RemediationAttempt, updated_record: RemediationRecord }` | 人工修复尝试结果，并携带更新后的完整报告 |
| `cancel_healing` | 前端→后端 | `{ task_id }` | 用户取消整个修复 |
| `healing_completed` | 后端→前端 | `{ final_record: RemediationRecord }` | 修复成功，闭环结束 |

---

## 9. 组件设计要点（给 Cursor）

### 9.1 ManualHealingModal.vue
- Props: `visible`, `remediationRecord`（初始报告）
- 使用一个 `ref` 存储完整的 `RemediationRecord`（动态更新）
- 内部状态：`userInput`, `validationError`
- 监听 `manual_heal_result` 消息，将新的 `updated_record` 替换本地 record，并追加尝试条目。
- 输入框绑定 `userInput`，计算 `isSubmitDisabled = !userInput.trim() || userInput.length > 8191`
- 再次修复按钮 emit `submit`，取消 emit `cancel`
- 当收到成功修复且验证通过后，弹出成功提示并关闭窗口。

### 9.2 HealingReport.vue (修复总结报告子组件)
- Props: `record: RemediationRecord`
- 展示格式化报告，根据 `attempts` 动态渲染尝试列表，样式美化。
- 支持滚动，当条目增多时自动滚到底部。

### 9.3 集成到命令执行面板
- 在 `CommandExecutionPanel` 中，当状态为 `FAILED` 并收到 `auto_healing_exhausted` 后，打开 `ManualHealingModal`。
- 处理 `healing_completed` 后，将主面板状态改为 `SUCCESS` 或其他。

---

## 10. 后端改动概要

- **修复策略生成器** `services/remediator/strategy_generator.py`：封装与 LLM 交互，传入历史尝试信息，返回差异化修复命令。
- **闭环控制器** `services/remediator/loop.py`：
  - 函数 `run_auto_healing_loop(task_id, max_attempts=3)`，通过 WebSocket 向前端推送进展。
  - 处理 `submit_manual_suggestion`，结合用户输入生成新修复命令并执行。
  - 保存所有记录到 `remediation_logs`。
- **验证逻辑**：简单验证可复用原有命令的 exit code，更复杂的验证可配置。

---

## 11. 实施步骤（Cursor）

### Phase 1: 后端自动修复循环
1. 实现 `RemediationRecord` 和 `RemediationAttempt` 数据结构。
2. 编写 `run_auto_healing_loop`，集成 LLM 生成差异化命令，执行并存储记录。
3. 通过 WebSocket 发送 `start_auto_healing`、`heal_attempt_result`、`auto_healing_exhausted`。

### Phase 2: 人工介入窗口与交互
1. 前端构建 `ManualHealingModal` 及 `HealingReport` 子组件。
2. 实现输入校验与按钮状态逻辑。
3. 监听 WebSocket 消息，动态更新报告，处理成功/失败/取消。
4. 对接后端 `submit_manual_suggestion` 和 `cancel_healing` 消息。

### Phase 3: 闭环完成与存档
1. 成功修复后，发送 `healing_completed`，前端更新主面板。
2. 后端确保所有记录写入日志文件/数据库。
3. 添加简单的审计查看（如任务详情页展示修复历史）。

---

以上设计覆盖了自动修复、人工介入、报告、验证及存档全部要求，可直接作为 Cursor 的开发蓝图。