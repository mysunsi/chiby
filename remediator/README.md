# remediator

面向「命令失败 → 结构化错误 → 知识库 / LLM 提案 → 分级执行」的 **AI 运维自愈核心库**，并提供 **FastAPI HTTP 封装**，供 IDE 插件、CI/CD 或运维平台调用。

本目录需作为 **`ai-ops-assistant` 仓库的一部分** 使用：请在仓库根目录安装依赖并运行（见下文）。

---

## 目录结构（概要）

| 路径 | 说明 |
|------|------|
| `core/` | 执行后端、`executor_wrapper` 自愈闭环、规则插件（如数据库保护、K8s OOM 补救）、指标与诊断 |
| `remediation/` | 错误解析、KB、LLM、`RemediationController` 主循环、CLI 入口 |
| `api/` | `POST /api/v1/remediate`、`/analyze` 等 REST 接口 |

---

## 环境准备

在仓库根目录 `ai-ops-assistant/`：

```bash
pip install -r requirements.txt
```

（可选）单独安装 API 声明依赖：

```bash
pip install -e remediator
```

或参见 `remediator/pyproject.toml` 中的 `[project]` 依赖。

---

## 用法一：Python 调用

```python
from remediator import run_with_remediation, analyze_only

# 详见 core.executor_wrapper 参数说明（dry_run、confirm_high_risk、backend 等）
```

延迟导出：`import remediator` 不会立刻加载全部重量级依赖；首次访问 `run_with_remediation` / `analyze_only` 时再导入实现。

---

## 用法二：命令行（CLI）

在仓库根目录执行：

```bash
# 仅分析，不下发修复命令（推荐先试）
python -m remediator.remediation.cli_fix "false" --dry-run --yes

# 非交互执行失败命令并进入自愈闭环
python -m remediator.remediation.cli_fix "false" --yes --max-retries 2
```

---

## 用法三：HTTP API

启动（默认 `0.0.0.0:8000`，可在 `api/start_server.py` 中修改）：

```bash
python -m remediator.api.start_server
```

浏览器打开交互文档：**http://127.0.0.1:8000/docs**

- **鉴权**：请求头携带 `X-API-Key`，值需与服务器侧一致。默认期望密钥为 `YOUR_SECRET_API_KEY`，可通过环境变量 **`MY_PROJECT_API_KEY`** 覆盖（生产环境务必修改）。
- **健康检查**：`GET /health`

主要路由：

- `POST /api/v1/remediate` — 基于上报的命令与 stderr 等信息执行自愈（内部可用「首次注入观测结果」的后端，避免重复执行失败命令）。
- `POST /api/v1/analyze` — 仅分析（等价 dry-run 思路）。

---

## 测试

在仓库根目录：

```bash
python -m pytest tests -v
```

与 `remediator` 强相关的用例：

```bash
python -m pytest tests/test_remediation_flow.py -v
```

可选覆盖率（需 `pytest-cov`）：

```bash
python -m pytest tests/test_remediation_flow.py --cov=remediator.core --cov=remediator.remediation --cov-report=term-missing
```

---

## 常见环境变量（节选）

实际生效集合与运维引擎、LLM 配置一致；以下为经常使用项：

| 变量 | 含义 |
|------|------|
| `MY_PROJECT_API_KEY` | API `X-API-Key` 期望密钥 |
| `OPENAI_API_KEY` / `LITELLM_API_KEY` | LLM 调用（若走 LiteLLM/OpenAI 兼容接口） |
| `REMEDIATION_KB_PATH` | 自愈知识库 SQLite 路径（可选） |
| `REMEDIATION_METRICS_PATH` | `remediation_metrics.jsonl` 路径（可选） |

完整列表可参考仓库根目录 `requirements.txt`、部署示例 `deploy/.env.example` 及各模块内的 `os.getenv`。

---

## 与 terminal / LLM 配置对齐

仓库中的 **`terminal/`**（Web 运维终端）与 **`remediator`** 都会调用大模型，但**职责不同**，配置应对齐而不是混用同一套 Prompt。

### 能力边界

| 组件 | LLM 在做什么 |
|------|----------------|
| **terminal**（`LLMPromptProcessor` + `chibycore.llm_providers`） | **自然语言 → 可执行命令**：面向交互式运维意图 |
| **remediator**（`remediation/llm_agent.py`，经 LiteLLM） | **结构化失败 → 修正命令（JSON）**：根因、修复命令、风险提示 |

二者常见流水线：**终端生成并执行命令 → 若失败再进入 remediator（Lite → KB → LLM）**。这是「串联」，不是把两套逻辑合成一次模型调用。

### 配置入口（对齐要点）

- **terminal / chibycore**：优先读仓库根 **`data/llm_config.json`**，并与 **环境变量**合并（`chibycore.llm_config.get_effective_llm_settings`）；密钥常见为各厂商 Key、`OPENAI_API_BASE` 等。
- **remediator**：经 `executor_wrapper` / `RemediationController` 传入 **`litellm_api_key`、`litellm_api_base`、`llm_model`**，通常对应 **`OPENAI_API_KEY`、`OPENAI_API_BASE`、`REMEDIATION_MODEL`**（或代码内默认模型名）。

**建议**：在同一部署环境用 **同一 `.env` 或同一密钥注入** 兜住 **`OPENAI_API_KEY` / `OPENAI_API_BASE`**；若自愈要用更强或更便宜的模型，仅单独设置 **`REMEDIATION_MODEL`**（或调用 `run_with_remediation(..., llm_model="...")`），终端侧仍在 `data/llm_config.json` 维护对话用模型——即 **一套密钥、两处模型名可按需拆分**。

### 实务提示

1. **排查「终端能用、自愈不能」**：先核对两端 Base URL、Key 是否一致，再看 **`REMEDIATION_MODEL`** 与 `llm_config.json` 中的 `model` 是否分别可达。
2. **超时**：自愈可能多轮 KB/LLM，比单次 NL→命令更长；HTTP 客户端或反向代理超时建议单独放宽。
3. **成本与策略**：自愈路径优先 KB / Lite Fix；控制自动执行范围可用 **`dry_run`、`confidence_execute_threshold`** 及环境变量（如 `REMEDIATION_CONFIDENCE_THRESHOLD`）。
4. **链路追踪**：自愈请求体可带 **`environment_id`**；与 `terminal` 侧会话关联时，可在业务网关打统一 **`trace_id`** 写入日志或审计。

### 与 terminal 的运行组合

- **并排服务**：`terminal` 占其一端口（如 `uvicorn terminal.main:app`），`remediator.api` 占 **8000**（或自定义）；失败时由前端/插件/脚本 **`POST /api/v1/remediate`**，无需在 `terminal` 内硬编码 import `remediator`。
- **深度集成**（自研）：在拿到会话的退出码与 stderr 后，进程内调用 **`run_with_remediation`** 并自定义 **`ExecutorBackend`**，需自行对齐 SSH/PTY 语义，复杂度高，建议在有 HTTP 桥接经验后再做。

---

## 与仓库其他部分的关系

- **`terminal/`、`chibycore/`、`api/`（仓库根下的 FastAPI）** 等为同一产品内的其他入口或业务能力；**本包 `remediator`** 聚焦自愈闭环与对外自愈 API。
- **Docker**：可参考仓库 `deploy/docker-compose.yml`（若将服务改为加载 `remediator.api` 需自行调整镜像启动命令）。

---

## 许可与版本

版本见 `remediator/pyproject.toml`。许可证以仓库根目录声明为准。
