# 兼容性约定（COMPATIBILITY）

本文件锁定开源 `ops-bridge` 的 API 演进策略，供闭源 Pro / SaaS 依赖时参考。

## 版本号

- 当前处于 `0.x` 阶段，版本形如 `0.MINOR.PATCH`。
- 遵循语义化版本（SemVer）精神，但 `0.x` 阶段 MINOR 可能含不兼容变更，会在此文件说明。

## 开源侧

- 开源包 `ops-bridge` 的公开 API（执行器契约、确认卡/审计接口、工具插件协议、LLM 配置）变更须：
  - 破坏性变更 → 升 MINOR 并在本文件与 CHANGELOG 标注；
  - 仅新增/修复 → 升 PATCH。

## 闭源依赖侧

- 终端 Pro（`assistant-pro` / `pro_core`）锁定开源 major：
  `ops-bridge >= 0.1, < 1.0`
- 开源演进不得在无 major 升级的情况下破坏 Pro 所依赖的已声明接口；
  若必须破坏，走 `1.0` 并同步 Pro 适配。

## 稳定性边界

| 接口 | 稳定性 |
|------|--------|
| 执行器 Protocol / `executor_contract` | 稳定（minor 内不破坏） |
| 确认卡 / 审计 JSONL 结构（基础字段） | 稳定 |
| `demo/*` 编排与 API | 实验性，可随版本调整 |
| 内部模块（未列入公开 API 的） | 不保证稳定 |

## 变更流程

1. 修改公开 API 前，先在 PR 描述标注「compatibility-impacting」。
2. 更新本文件与 CHANGELOG。
3. CI 跑 `pytest -m "not proprietary"` 全绿后方可合并。
