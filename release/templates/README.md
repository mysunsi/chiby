# ChibyTerm（赤壁终端）

[![PyPI version](https://img.shields.io/pypi/v/chibyterm)](https://pypi.org/project/chibyterm/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)

**ChibyTerm（赤壁终端）** 是一个 AI 驱动的 Web 运维终端：**自然语言管理服务器，变更前强制确认，事后可审计。**

**Chiby（赤壁）** 是产品品牌；上游 **Hermes Agent** 为 MIT 协议可选运行时。本开源仓提供**终端模式全量能力**（闭环 + KnowledgeHub / DocHub），**不包含**智能型/全能型运维中枢、掌上机房后端与 Hermes/Chiby 桥（商业闭源，单独分发）。

## 分层

| 形态 | 许可 | 内容 |
|------|------|------|
| 终端开源版（本仓库 / `chibyterm` + `chibycore`） | Apache-2.0 | TSM-A 护栏 · Web 终端 · 闭环 · KB/Doc · Fleet · 工具契约 |
| 终端 Pro / 企业扩展 | 商业闭源 | 智能型/全能型中枢 · 许可 · 报告增强 |
| 掌上 SaaS | 商业闭源 | 掌上 IM · Job · 企业策略 |

## 5 分钟快速开始

```bash
pip install chibyterm
# 配置 data/hosts.json 后：
uvicorn chibyterm.main:app --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000`，试试：「查看内存使用情况」。

完整说明见根目录 [README.md](../../README.md)（导出仓则为本文件）。

## 贡献与安全

见 `CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`。安全漏洞请按 `SECURITY.md` 私报告知。

## 许可证

- 代码：Apache-2.0（见 `LICENSE`）
- 第三方：见 `NOTICE`（含 Hermes Agent 的 MIT 声明）

---
© 2026 {{LEGAL_ENTITY}} · Chiby（赤壁）
