# 贡献指南

欢迎贡献 **ChibyTerm（赤壁终端）**！

## 快速开始

```bash
git clone https://github.com/chiby-ai/chibyterm.git
cd chibyterm
pip install -e ".[dev]"
# 依赖见 requirements.txt；Monorepo 包映射见根目录 pyproject.toml
```

要求：**Python 3.10+**。

## 测试

```bash
# 仅运行开源测试（PR 必须通过）
pytest -m "not proprietary" -v

# 收集检查
pytest -m "not proprietary" --collect-only -q

# 开源边界门禁（确保 packages/ 无闭源硬引用）
python scripts/check_oss_boundary.py
```

依赖闭源包（`chiby_mobile` / `chiby_hermes_bridge`）的用例请标注：

```python
import pytest

pytestmark = pytest.mark.proprietary
```

## 提交 PR

1. Fork 本仓库并基于 `main`（或当前默认分支）创建特性分支：`git checkout -b feat/your-feature`
2. 保持提交小而聚焦，说明「为什么」而不只是「改了什么」
3. 确保 `pytest -m "not proprietary"` 与 `check_oss_boundary.py` 通过
4. 提交 PR，描述改动与测试结果；若影响公开行为请在描述中标注

## 开源边界

- 不要在 `packages/` 内硬 `import` `proprietary` / `chiby_mobile` / `chiby_hermes_bridge`（`importlib` 可选探测除外，且须过门禁）
- 不提交密钥、`data/*.json` 中的真实密码、闭源 `proprietary/` 构建产物（`dist/`、`*.egg-info`）

## 插件与扩展

工具插件契约见 [`docs/extending-agent-tools.md`](docs/extending-agent-tools.md) 与 [`tools/plugins/README.md`](tools/plugins/README.md)。写操作必须经确认卡路径。

## 代码规范

- 遵循 PEP 8；可用 `ruff` / `black` 格式化
- 新增功能需包含单元测试（开源路径勿依赖闭源包）

## 许可证

你提交的 Contribution 将采用本仓库的 **Apache-2.0** 许可。请勿提交第三方代码而未保留其原有许可证声明。

## 行为准则

请阅读 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)。安全漏洞请按 [SECURITY.md](./SECURITY.md) 私报告知。
