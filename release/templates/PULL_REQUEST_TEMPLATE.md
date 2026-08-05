## 摘要

<!-- 说明改动目的（为什么），而非仅罗列文件 -->

## 类型

- [ ] 功能
- [ ] 修复
- [ ] 文档
- [ ] 重构 / 杂项

## 检查项

- [ ] `pytest -m "not proprietary"` 通过
- [ ] `python scripts/check_oss_boundary.py` 通过
- [ ] 未在 `packages/` 硬依赖闭源包（`chiby_mobile` / `chiby_hermes_bridge`）
- [ ] 未提交密钥 / 真实 `hosts.json` 密码
- [ ] 若影响公开 API 或用户可见行为，已在描述中标注

## 测试说明

<!-- 如何验证本 PR -->
