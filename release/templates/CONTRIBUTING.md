# 贡献指南

欢迎贡献 **ChibyTerm（赤壁终端）**！

## 快速开始

```bash
git clone https://github.com/chiby-ai/chibyterm.git
cd chibyterm
pip install -e ".[dev]"
```

要求：**Python 3.10+**。

## 测试

```bash
pytest -m "not proprietary" -v
python scripts/check_oss_boundary.py
```

依赖闭源包的用例须标注 `@pytest.mark.proprietary`。

## 提交 PR

1. Fork 本仓库并基于默认分支创建特性分支：`git checkout -b feat/your-feature`
2. 保持提交小而聚焦
3. 确保开源测试与边界检查通过
4. 提交 PR，描述改动与测试结果；影响公开行为请标注

## 开发约定

- 遵循 PEP 8；可用 `ruff` / `black`
- 新增执行器 / 工具请走开源插件契约（`tools/`），写操作必须经确认卡
- 不在 `packages/` 硬 import 闭源模块（`chiby_mobile`、`chiby_hermes_bridge`、`proprietary`）
- 不提交密钥与真实 `hosts.json` 密码

## 许可证

Contribution 采用本仓库 **Apache-2.0**。请勿提交未保留原许可证声明的第三方代码。

## 行为准则

参与即视为同意 `CODE_OF_CONDUCT.md`。安全漏洞见 `SECURITY.md`。
