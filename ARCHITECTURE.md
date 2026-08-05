# ChibyTerm 架构概览

薄入口文档：细节以 `docs/` 专题为准，本文只对齐**边界与导航**。

## 开源 / 闭源边界

```text
┌─────────────────────────────────────────────────────────────┐
│ ChibyTerm 开源核心 (Apache-2.0)                             │
│  packages/chibycore · packages/chibyterm                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ Web 终端    │  │ Fleet 执行  │  │ 知识双轨   │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ 闭环治理    │  │ TSM-A 护栏  │  │ 工具契约   │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
                            │ 可选 pip 依赖（entry_points）
┌───────────────────────────▼─────────────────────────────────┐
│ 企业扩展 (闭源，不进本开源仓)                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ chiby-mobile│  │ chiby-      │  │ Pro 增强    │        │
│  │ (掌上 AI)   │  │ hermes-     │  │ (模板/趋势  │        │
│  │             │  │ bridge      │  │  / 许可等)  │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

| 层 | 包 / 目录 | 许可 |
|----|-----------|------|
| 执行与知识核 | `chibycore` | Apache-2.0 |
| Web 终端入口 | `chibyterm` | Apache-2.0 |
| 掌上 AI 机房 | `chiby-mobile`（`proprietary/`） | 商业闭源 |
| Hermes/Chiby 桥 | `chiby-hermes-bridge` | 商业闭源 |

门禁：`python scripts/check_oss_boundary.py`（`packages/` 禁止硬依赖闭源）。

## 详细文档

- 产品三层架构：[docs/oss-pro-saas-architecture.md](./docs/oss-pro-saas-architecture.md)
- 开源边界决议：[docs/open-source-boundary-review.md](./docs/open-source-boundary-review.md)
- 代码结构入口：[docs/system-code-structure.md](./docs/system-code-structure.md)
- 插件架构：[docs/tool-plugin-architecture.md](./docs/tool-plugin-architecture.md)
- 安全模型：[docs/tsm-a-security-model.md](./docs/tsm-a-security-model.md)
- 技术白皮书：[docs/chiby-technical-whitepaper.md](./docs/chiby-technical-whitepaper.md)

## 技术栈（开源核）

- **后端**：Python 3.10+ · FastAPI · Uvicorn
- **终端 UI**：xterm.js
- **执行**：Paramiko（SSH）/ pywinrm（WinRM）
- **AI**：开源侧为规则 + 可选 LLM API；**智能型/全能型中枢**与 Hermes ACP 桥属企业扩展（上游 Hermes Agent 为 MIT，作可选运行时，非本仓改名）
