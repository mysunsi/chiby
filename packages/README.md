# packages/ — 开源发布单元（物理根）

| 目录 | PyPI / 导入名 | 说明 |
|------|---------------|------|
| `chibycore/` | `chibycore` | 执行契约、闭环、KnowledgeHub、DocHub |
| `chibyterm/` | `chibyterm`（别名 `terminal` / 过渡 `ops_terminal`） | ChibyTerm Web 终端入口 |

**注意**：本目录本身**不是** Python 包（无 `__init__.py`）。  
曾用名：`ops_core` → `chibycore`，`ops_terminal` → `chibyterm`。

闭源扩展在 `proprietary/chiby_mobile`、`proprietary/chiby_hermes_bridge`（独立 wheel + `chiby.plugins`）。

门禁：`python scripts/check_oss_boundary.py`
