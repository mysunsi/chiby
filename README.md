# ChibyTerm（赤壁终端）

[![PyPI version](https://img.shields.io/pypi/v/chibyterm)](https://pypi.org/project/chibyterm/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/chibyterm)](https://pypi.org/project/chibyterm/)

**ChibyTerm（赤壁终端）** 是一个 AI 驱动的 Web 运维终端：**自然语言管理服务器，变更前强制确认，事后可审计。**

- ✅ Web 终端（SSH / WinRM）
- ✅ 自然语言 → Shell 命令
- ✅ 闭环治理（重试 / 摘要 / 回放）
- ✅ KnowledgeHub + DocHub（知识双轨）
- ✅ TSM-A 安全护栏（确认卡 / 风险判定 / 基础审计）
- ✅ Fleet 批量执行 + AI 报告生成
- ✅ 定时任务自动化
- ✅ 工具插件契约（社区可扩展）

> **企业扩展**：智能型/全能型中枢、掌上 IM/Job、SSO 等见文末闭源包（`chiby-mobile`、`chiby-hermes-bridge`）。

---

## 5 分钟快速开始

### 1. 安装

```bash
pip install chibyterm
```

> 正式 PyPI 未上线前可用 TestPyPI：  
> `pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ chibyterm`

### 2. 配置主机

在工作目录创建 `data/hosts.json`（**勿把真实密码提交到公开仓**）：

```json
{
  "hosts": [
    {
      "id": "demo-1",
      "name": "my-server",
      "host": "192.168.1.100",
      "port": 22,
      "username": "root",
      "password": "<set-locally>",
      "conn_type": "ssh"
    }
  ]
}
```

### 3. 启动

```bash
# 可选：配置 LLM（不配置则仅规则模式）
export LLM_PROVIDER="openai"
export OPENAI_API_KEY="sk-..."

uvicorn chibyterm.main:app --host 0.0.0.0 --port 8000
# Windows PowerShell：
#   python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

### 4. 打开浏览器

访问 `http://localhost:8000`

**试试第一条命令**：

> “查看内存使用情况”

高风险命令自动弹出**确认卡**，点“允许”才执行。

---

## 开源 vs 企业扩展

| 功能 | ChibyTerm（开源） | 企业扩展（闭源） |
|------|:---:|:---:|
| Web 终端 / SSH / WinRM | ✅ | ✅ |
| 自然语言 → Shell | ✅ | ✅ |
| 闭环治理 / 知识双轨 | ✅ | ✅ |
| 工具插件契约 | ✅ | ✅ |
| Fleet 批量执行 + 报告 | ✅ | ✅ |
| 定时任务（基础） | ✅ | ✅ |
| **智能型 / 全能型中枢** | ❌ | ✅ |
| **掌上 IM / Job / 审计大屏** | ❌ | ✅ |
| **SSO / 合规报告 / 许可** | ❌ | ✅ |
| **自定义报告模板 / 趋势对比** | ❌ | ✅ |

企业扩展（需先有开源核心；闭源 wheel 从私有源或本地 `proprietary/*/dist`）：

```bash
pip install chibyterm
pip install chiby-mobile chiby-hermes-bridge

export OPS_MOBILE_DEMO_ENABLED=1
export OPS_HERMES_BRIDGE_ENABLED=1
uvicorn chibyterm.main:app --host 0.0.0.0 --port 8000
```

详见 [chiby.ai](https://chiby.ai)。开源边界门禁：`python scripts/check_oss_boundary.py`。

---

## 开发与贡献

```bash
git clone https://github.com/chiby-ai/chibyterm.git
cd chibyterm
pip install -e ".[dev]"
pytest -m "not proprietary"
```

详见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

架构入口：[ARCHITECTURE.md](./ARCHITECTURE.md) · API：[API_REFERENCE.md](./API_REFERENCE.md) · 安全：[SECURITY.md](./SECURITY.md)

---

## 许可证

Apache-2.0，详见 [LICENSE](./LICENSE) 和 [NOTICE](./NOTICE)。

上游 **Hermes Agent** 为 MIT 许可证（Copyright Nous Research），作为可选运行时依赖——**不是给 Hermes 改名**；产品品牌为 **Chiby（赤壁）**，开源终端产品名为 **ChibyTerm**。

---

## 社区与支持

- GitHub Issues：https://github.com/chiby-ai/chibyterm/issues （公开镜像就绪后）
- 安全漏洞：见 [SECURITY.md](./SECURITY.md)
- 官网：https://chiby.cn
