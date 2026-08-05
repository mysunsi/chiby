# 常见问题

## 打开页面后无法加载主机？

- 确认后端已启动：`python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000`  
- 不要用 `file://` 直接打开 HTML；请通过 http 访问。  
- 若启用了登录，请先登录；未登录时 `/api/*` 会返回 401。

## 自然语言没有反应？

- 到「模型设置」检查 Base URL、模型名与 API Key。  
- 查看右侧模型状态是否显示「未配置 / 离线」。  
- 本机防火墙或代理是否拦截了对模型服务的访问。

## 集群群发（Fleet）在哪里？混用 Windows / Linux 会怎样？

- 详见帮助目录 **「Fleet（集群群发）」**。  
- 入口：右侧交互模式 **🌐 Fleet**。  
- 默认报告口吻：右上角菜单 → **群发设置**（生成报告时可临时覆盖）。  
- 用自然语言描述意图；系统按各机 OS **分别生成命令**，确认后再下发。  
- 只会发给**当前已打开的终端 Tab**（或 Fleet 目标范围内主机）。

## 找不到 data/hosts.json？

- 服务端以**当前工作目录**为根查找 `data/`。  
- 请在含 `data` 的目录下启动，或自行创建最小 `hosts.json`。

## 正式安装后不要用 `terminal.main`？

- 开发仓可用过渡别名 `terminal`。  
- `pip install chibyterm` 后请一律使用：`chibyterm.main:app`。

## Python 版本？

- 需要 **Python ≥ 3.10**。

## 还想深入？

- 仓库 `docs/index.md`：文档导航  
- `docs/import.md`：重要文档分层清单  
- `docs/chibyterm-package-release-handbook.md`：打包与 TestPyPI 试用  
