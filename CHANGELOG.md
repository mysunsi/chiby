# Changelog

所有重要变更记录于此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

内部 SVN 主题归纳见 [`docs/CHANGELOG-svn.md`](./docs/CHANGELOG-svn.md)（非产品对外定义）。

## [0.1.2] - 2026-08-05

### 内部试用（TestPyPI）

相对 0.1.1 的运维体验补强（同日增补）：

- **主机列表**：`GET /api/hosts` 分页/检索；`prefer_ids` 将已选主机置顶
- **Fleet**：范围选机 oneshot（不强制开 Tab）；目标范围搜索分页；勾选「仅已打开 Tab」时隐藏选机区
- **主机分组弹窗**：成员搜索分页 + 跨页勾选草稿
- **引导**：首次连上终端后轻提示「点击 🤖 唤醒 AI 助手」

## [0.1.1] - 2026-08-06

### 首次公开发布

与 `packages/*/pyproject.toml` 版本对齐的正式开源 wheel（Apache-2.0）。

#### 核心功能

- **主机管理**：静态组 CRUD、标签/状态、表单编辑、测连状态
- **Fleet 批量执行**：范围选机（组/主机）、按 OS 分段预览、并行执行、AI 报告生成
- **定时任务**：Cron 调度、预填参数、执行历史；连续失败知识提示
- **多机 AI 排查排障**：并行取证（process_list / service_status / log_search / network_connections）、横向对比、离群检测、单机降级
- **知识双轨**：KnowledgeHub（短经验）+ DocHub（长文档）+ 诊断/Fleet 一键入库
- **进度展示**：SSE 五阶段（init → similar_cases → executing → analyzing → suggesting → done）、主机级卡片、工具状态
- **统一审计**：platform_audit 平台层、trace_id 全链路、查询 API
- **相似案例匹配**：排查前自动检索 + 上下文注入
- **TSM-A 安全护栏**：确认卡、风险判定、基础审计
- **Web 终端**：SSH / WinRM；自然语言 → Shell（LLM 可选）
- **闭环治理**：重做 / 摘要 / 回放 / 变更冻结
- **工具插件契约**

#### 开发者

- Python 3.10+，FastAPI + Uvicorn
- 开源协议：Apache-2.0（上游见 `NOTICE`）
- 边界检查：`python scripts/check_oss_boundary.py`
- 测试隔离：`pytest -m "not proprietary"`
- 入口：`uvicorn chibyterm.main:app`

#### 已知限制

- 主机标签表达式（动态组）见路线图
- Pro 版功能（高级权限、闸门）尚未启用

## [0.1.0] - 2026-08-05

### 内部预览

功能集合与 0.1.1 同口径；对外以 **0.1.1** wheel / tag 为准。
