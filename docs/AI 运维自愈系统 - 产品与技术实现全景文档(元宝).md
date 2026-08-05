# AI 运维自愈系统 - 产品与技术实现全景文档【已完成】

## 1. 项目概述

本项目旨在构建一个企业级 AI 运维自愈系统（Auto-Remediation System）。系统能够在捕获命令行执行错误后，利用大语言模型（LLM）进行根因分析，自动生成修复命令，并进行重试验证，直至任务成功或判定为不可修复。系统强调安全性、鲁棒性及可观测性，避免无效循环与风险操作。

## 2. 核心业务流程（对齐原始 PRD）

系统严格遵循以下自动化闭环修正流程：

1.  **错误捕获与结构化解析**
    *   **捕获**：实时捕获命令执行的 `stdout`、`stderr` 及 `return_code`。
    *   **解析**：通过正则表达式提取关键要素：
        *   **错误类型**：权限不足（`PERMISSION_DENIED_SUDO`）、文件不存在（`FILE_NOT_FOUND_PATH_TYPO`）、命令未找到（`COMMAND_NOT_FOUND_PKG_MISSING`）、网络超时（`NETWORK_TIMEOUT_UNREACHABLE`）。
        *   **错误码**：Linux 返回码（1, 126, 127 等）。
        *   **上下文**：涉及路径、服务名、当前用户权限。
    *   **示例**：
        ```json
        // 原始错误: cp: cannot create regular file '/tmp/app.log': Permission denied
        // 解析结果
        {
          "type": "PERMISSION_DENIED_SUDO",
          "path": "/tmp/app.log",
          "reason": "当前用户无目标目录写入权限",
          "error_code": 1,
          "requires_package": null
        }
        ```

2.  **错误可修正性判断与上下文累积**
    *   **可修正判断**：
        *   **可修正**：权限不足（加 `sudo`）、路径拼写错误（修正路径）、参数缺失（补充参数）。
        *   **不可修正**：服务器宕机、核心依赖未安装、硬件故障。
    *   **上下文累积（历史链）**：
        *   维护 `原始命令 -> 错误1 -> 修正命令1 -> 错误2 -> 修正命令2` 的完整链条。
        *   每次调用 LLM 时传入完整历史，避免重复无效修正。

3.  **大模型驱动的命令修正策略**
    *   **输入**：结构化错误 + 修正历史链 + 环境信息（OS、权限）。
    *   **输出**：
        *   **根因说明**：自然语言解释错误本质。
        *   **修正命令**：优先选择简单、低风险方案。
        *   **风险提示**：对高危操作（如 `sudo rm -rf`）进行预警。
    *   **复杂场景适配**：
        *   **环境动态变化**：生成带前置检查的脚本（如 `if [ -f ... ]`）。
        *   **多命令依赖**：联动调整整个命令序列（如 `cd` 路径错误导致后续 `ls` 失败）。

4.  **修正命令确认与重试循环**
    *   **用户确认**：展示对比信息，提供“立即执行 / 手动调整 / 放弃”选项。
    *   **重试逻辑**：
        *   执行修正命令，成功则结束。
        *   失败则开启新一轮循环。
    *   **终止条件**：
        *   达到最大重试次数（默认 3 次）。
        *   修正命令与上一轮相似度 > 90%（判定为无效修正）。
        *   用户手动终止。
        *   判定为不可修正错误。

5.  **不可修正错误处理与经验沉淀**
    *   **不可修正处理**：终止重试，返回“需人工介入”及原因，或给出人工操作建议（如“请先安装 Maven”）。
    *   **经验沉淀（知识库）**：
        *   每次成功的“错误-修正”案例自动存入本地 SQLite 知识库。
        *   记录字段：错误类型、环境特征、原始命令、修正命令、根因说明。
        *   **优先匹配**：后续遇到同类错误，优先从知识库匹配方案，减少对大模型的依赖。

## 3. 系统架构设计

### 3.1 架构原则
*   **非侵入式集成**：不修改现有业务代码（如 `core/executor.py`），通过包装器（Wrapper）模式接入。
*   **控制层与 AI 层分离**：
    *   **控制层（Python）**：负责确定的逻辑（执行、解析、风控、循环）。
    *   **AI 层（LLM）**：负责不确定的语义逻辑（根因分析、生成命令）。
*   **可插拔设计**：支持本地执行、Docker 执行等多种后端。

### 3.2 项目目录结构

```
remediator/
├── remediator/
│   ├── core/
│   │   ├── __init__.py         # 导出核心组件
│   │   ├── executor.py         # 【已有】原命令执行器
│   │   ├── executor_wrapper.py # 【新增】AI自愈包装层（核心集成点）
│   │   ├── executor_backends.py# 【新增】执行后端抽象（本地/Docker）
│   │   ├── metrics.py         # 【新增】指标模型与收集器
│   │   └── lite_fixer.py      # 【新增】轻量级规则修复器
│   ├── remediation/           # 【核心模块】自愈逻辑（未改动）
│   │   ├── __init__.py
│   │   ├── models.py          # Pydantic 数据模型
│   │   ├── parser.py          # 正则错误解析与可修正性判断
│   │   ├── knowledge_base.py  # SQLite 知识库管理
│   │   ├── llm_agent.py       # LLM 交互与 Prompt 工程
│   │   └── loop.py           # 重试控制器与闭环逻辑
│   ├── cli_fix.py            # 【新增】命令行工具入口
│   └── diagnostics.py        # 【新增】故障诊断报告生成
├── examples/
│   └── production_integration.py # 生产集成示例
├── scripts/
│   └── report.py             # 指标统计报表脚本
├── tests/                    # 【新增】回归测试
├── deploy/                  # 【新增】Docker 部署配置
├── dashboard/               # 【新增】Streamlit 监控看板
└── pyproject.toml
```

## 4. 核心模块实现详解

### 4.1 数据模型 (`remediation/models.py`)
*   使用 Pydantic v2 确保数据校验。
*   **`StructuredError`**: 存储解析后的错误信息（类型、路径、错误码、`requires_package`）。
*   **`KnowledgeRecord`**: 知识库记录，包含 `fingerprint`（用于精确去重）。
*   **`LLMRemediationJSON`**: 定义 LLM 必须输出的 JSON Schema（根因、修正命令、风险）。

### 4.2 知识库与指纹 (`remediation/knowledge_base.py`)
*   **指纹生成**：`compute_error_fingerprint` 结合错误类型、归一化命令、OS 信息进行 SHA256 哈希，确保同类错误在不同环境/路径下的隔离与命中。
*   **三级检索策略**：
    1.  Fingerprint 精确命中（最快）。
    2.  `error_category` + `requires_package` 命中（针对依赖缺失）。
    3.  `error_category` + `stderr` 相似度匹配（兜底）。
*   **去重逻辑**：写入时检查 `(fingerprint, fixed_command)`，避免重复数据。

### 4.3 控制循环 (`remediation/loop.py`)
*   **相似度防死锁**：结合 `difflib.SequenceMatcher` 和 Levenshtein 距离，设置双重阈值（如 0.98 和 0.85），防止 AI 陷入语义雷同的无效循环。
*   **历史累积**：`RemediationHistory` 将历史尝试转化为 Prompt 文本，帮助 LLM 改变策略。

### 4.4 生产集成层 (`core/executor_wrapper.py`)
这是连接业务系统与自愈模块的**关键适配器**。
*   **`run_with_remediation`**: 主入口函数。
    *   **Dry-run 模式**：仅分析错误，不执行修复，输出诊断报告。
    *   **风险拦截**：对 `HIGH` 风险命令（如 `sudo rm -rf /`）进行强制拦截，除非用户明确确认。
    *   **指标注入**：调用 `MetricsCollector` 记录每次会话的耗时、重试次数、KB 命中率等。

### 4.5 LLM 策略增强 (`remediation/llm_agent.py`)
*   **System Prompt 强化**：
    *   强制要求输出 JSON。
    *   要求对删除/覆盖操作增加 `if [ -f ... ]` 前置检查。
    *   要求对多命令链（`&&`）修复第一个失败点。
    *   优先处理 `requires_package`（如 `apt install maven`）。

## 5. 企业级（ToB）产品化规划

### 5.1 Phase 8: 企业级管控 (Enterprise Ready)
*   **多租户隔离**：在知识库和指标中引入 `environment_id`，确保不同项目/客户的数据物理或逻辑隔离。
*   **RBAC 权限系统**：定义角色（如 Operator, Approver），控制谁能执行命令、谁能审批高风险操作。

### 5.2 Phase 9: 开放平台与生态 (Open Platform)
*   **Webhook 通知**：支持将修复结果、审批请求推送到企业微信/钉钉/Slack。
*   **FastAPI 接口**：提供标准 RESTful API，方便集成到 CI/CD、Jenkins 或内部运维平台。

### 5.3 终极形态：IDE 插件（ToB 杀手锏）
将运维能力左移至开发阶段，抢占开发者心智。
*   **原理**：VS Code/JetBrains 插件监听终端错误 -> 调用后端 `/api/v1/remediate` -> 展示修复建议。
*   **价值**：
    *   **销售利器**：现场演示“一键修复”，体验极佳。
    *   **降本增效**：在开发阶段解决环境问题，减少生产事故。

## 6. 快速开始

### 6.1 环境配置
```bash
# 安装依赖
pip install -r pyproject.toml

# 设置环境变量
export OPENAI_API_KEY="sk-..."
export PYTHONPATH=/path/to/remediator
export REMEDIATION_METRICS_PATH="./data"
```

### 6.2 使用示例
```python
from remediator.core import run_with_remediation

# 自动修复命令（交互模式）
result = run_with_remediation("cp /root/file /tmp")

# Dry-run 模式（仅分析）
result = run_with_remediation("cp /root/file /tmp", dry_run=True)

# 生产集成模式
result = run_with_remediation(
    command="systemctl restart nginx",
    interactive=False,
    confirm_high_risk=True
)
```

---
*文档生成时间：2026-05-05*
*版本：v1.0 (Phase 7 Completed)*