# 意图级闭环（Intent Checklist）

> **一句话：** 闭环终点从「命令 `exit_code=0`」升级为「用户意图的子目标全部完成」。  
> **赛题叙事：** GOAI「零人工运维」— 系统拆解 → 逐项执行/自愈 → 进度可见 → 仅高危暂停。

## 1. 为什么需要

命令级闭环（含 `goal_resume`）能修好「`nginx -t` 权限不足」，但仍可能在「语法检测通过」处停住，而用户要的是「完整配置信息」。

| 维度 | 命令级闭环 | 意图级闭环 |
|------|------------|------------|
| 终点 | 当前命令判定通过 | 意图清单全部 `completed` |
| 成功信号 | `sudo nginx -t` OK | 路径/参数/完整配置均已拿到 |
| 失败后续 | 修好即停 | 继续下一项，或标 `partial`/`failed` |

## 2. 四层关系

```text
L0 意图清单 IntentChecklist     ← packages/chibycore/intent_checklist.py
L1 计划项（可从 plan steps / a&&b&&c 拆分）
L2 项内执行：run_closure_retry_loop（修复 + goal_resume）
L3 项验证：仅 success_initial / success_after_fix 算完成
```

- **项内**：继续用命令级修复与原命令复验。  
- **项间**：清单负责「下一子目标」，避免停在半截修复上。

## 3. 代码入口

| 组件 | 路径 |
|------|------|
| 模型 / runner | `packages/chibycore/intent_checklist.py` |
| 计划态字段 | `packages/chibyterm/plan_state.py` → `PlanRuntime.intent` / `checklist` |
| 批准后编排 | `packages/chibyterm/main.py` → `_run_plan_as_intent_checklist` |
| WS 进度 | `intent_checklist_progress` |
| UI | `packages/chibyterm/web/index.html` 意图进度卡 |
| 单测 | `tests/test_intent_checklist.py` |

## 4. 开关

| 环境变量 | 默认 | 含义 |
|----------|------|------|
| `OPS_INTENT_CHECKLIST` | `1`（开启） | 设为 `0`/`false`/`off` 则回退旧 `_dispatch_plan_core` |

批准计划后（`approve_plan`）：默认走意图清单；复合命令 `a && b && c` 会拆成多项。

## 5. 「零人工」定义（产品口径）

系统自主完成：意图拆解 → 子目标执行/自愈 → 直到清单完成或不可恢复失败。  
**仅在**无法恢复的失败，或策略认定的**高危需审批**操作时暂停等待人工。

用户始终可通过 AI 面板意图卡看到「完成了百分之几」（`n/m`）。

## 6. 与受控修复时间线的关系

单项失败触发的「受控闭环 / repair_*」仍可用；意图清单是其**外层编排**。  
`repair_ok_goal_unverified` 表示「环境修好了但本项原目标未复验」— 该项不算 `completed`。
