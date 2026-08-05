# ChibyTerm Python 库制作与发布操作手册（生产向）

> 覆盖范围：开源拆分（P0–P1）→ 合规与 README（P2-1）→ 包名统一 → 构建 wheel → TestPyPI 试发（P2-3）→ 内部试用。  
> 仓库：SVN `Assistant/`（工作副本常见路径 `D:\Open\Assistant`）。  
> 产品名：**ChibyTerm（赤壁终端）**；PyPI / 导入名：**`chibyterm`** + **`chibycore`**。

---

## 0. 一句话目标

做出两个可独立安装的 Python 包：

| 包名 | 作用 | 源码目录 |
|------|------|----------|
| `chibycore` | 执行网关、闭环、KnowledgeHub、DocHub | `packages/chibycore/` |
| `chibyterm` | Web 终端入口（依赖 chibycore） | `packages/chibyterm/` |

闭源扩展（不进公开包）：`proprietary/chiby_mobile`、`proprietary/chiby_hermes_bridge`，通过 `chiby.plugins` entry_points 动态挂载。

---

## 1. 目录与角色（必须先懂）

```text
Assistant/
├── packages/
│   ├── chibycore/          # 开源底层库（可单独 build）
│   │   └── pyproject.toml
│   └── chibyterm/          # 开源终端（可单独 build，依赖 chibycore）
│       ├── pyproject.toml
│       └── web/            # 前端静态资源（打进 wheel）
├── proprietary/            # 闭源，不进 OSS wheel
│   ├── chiby_mobile/
│   └── chiby_hermes_bridge/
├── terminal/               # 仅开发期薄别名（uvicorn terminal.main 过渡用）
├── LICENSE / NOTICE / README.md / CONTRIBUTING.md
├── scripts/
│   ├── check_oss_boundary.py   # 开源边界门禁
│   └── upload_testpypi.ps1     # TestPyPI 上传
└── pyproject.toml          # Monorepo 开发用（pip install -e .）
```

**命名对照（历史 → 现在）**

| 曾用名 | 现名 |
|--------|------|
| Ops Shell / OPSSHELL | **ChibyTerm（赤壁终端）** |
| `ops_terminal` / `ops-terminal` | **`chibyterm`** |
| `ops_core` | **`chibycore`** |

开发期仍可用别名：`terminal`、`ops_terminal`、`ops_core`（conftest / path_alias）。**正式 wheel 安装后请只用 `chibyterm`。**

---

## 2. 环境要求

| 项 | 要求 |
|----|------|
| Python | **≥ 3.10**（推荐 3.11；3.8 会 `No matching distribution`） |
| 工具 | `pip`、`build`、`twine` |
| 系统 | Windows / Linux 均可；本文命令以 PowerShell 为主 |

```powershell
python --version          # 必须 3.10+
python -m pip install -U pip setuptools wheel build twine
```

---

## 3. 完整制作流程（生产按此走）

### 阶段 A：改代码（开发机 / Monorepo）

1. 在 `packages/chibycore`、`packages/chibyterm` 改功能。  
2. 包内导入用 **`chibyterm` / `chibycore`**，不要写死依赖仓库根的 `terminal` 别名（否则 wheel 在干净环境会 ImportError）。  
3. `packages/` 内禁止 `import proprietary` / `import chiby_mobile`（闭源用 entry_points / importlib 可选加载）。  
4. 门禁：

```powershell
cd D:\Open\Assistant
python scripts\check_oss_boundary.py
```

5. 开发自测：

```powershell
$env:PYTHONPATH = "packages;proprietary\chiby_mobile\src;proprietary\chiby_hermes_bridge\src"
python -c "from chibyterm.main import app; print(app.title)"
# 期望：ChibyTerm；日志提示跳过闭源路由（默认开关关）
```

6. 测试（开源 CI 口径）：

```powershell
pytest -m "not proprietary" --collect-only -q   # 目标约 596
pytest -m "not proprietary" -q                  # 全量；闭源用例应打 @pytest.mark.proprietary
```

> 说明：曾出现 570 passed / 26 failed，多为未标记的 mobile/Hermes 用例仍被收集。开源 CI 全绿前需补 marker。

7. SVN：只提交源码与文档，**不要**提交 `.env`、`dist/`、`*.egg-info`、`.venv`、`data/transcripts`。

```powershell
svn status
svn commit -m "说明本次变更"
```

---

### 阶段 B：构建 wheel（做出「可安装的库文件」）

在仓库根执行（每个包有自己的 `pyproject.toml`）：

```powershell
cd D:\Open\Assistant

# 清理旧产物（可选）
Remove-Item -Recurse -Force packages\chibycore\dist, packages\chibycore\build -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force packages\chibyterm\dist, packages\chibyterm\build -ErrorAction SilentlyContinue

# 先底层、后终端
python -m build packages\chibycore
python -m build packages\chibyterm
```

**预期产物：**

```text
packages/chibycore/dist/chibycore-0.1.0-py3-none-any.whl
packages/chibycore/dist/chibycore-0.1.0.tar.gz
packages/chibyterm/dist/chibyterm-0.1.0-py3-none-any.whl
packages/chibyterm/dist/chibyterm-0.1.0.tar.gz
```

**本地不上传也可验安装：**

```powershell
python -m venv D:\venv\chiby-local
D:\venv\chiby-local\Scripts\Activate.ps1
pip install -U pip
pip install packages\chibycore\dist\chibycore-0.1.0-py3-none-any.whl
pip install packages\chibyterm\dist\chibyterm-0.1.0-py3-none-any.whl
python -c "from chibyterm.main import app; print(app.title)"
```

---

### 阶段 C：上传 TestPyPI（试发，可反复覆盖策略有限）

1. 打开 https://test.pypi.org/manage/account/token/ 创建 API Token。  
2. PowerShell：

```powershell
cd D:\Open\Assistant
$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = "pypi-你的TestPyPI令牌"   # 整段粘贴，含 pypi- 前缀
powershell -File scripts\upload_testpypi.ps1
```

脚本顺序：**先 chibycore，后 chibyterm**。

3. 浏览器确认：

- https://test.pypi.org/project/chibycore/0.1.0/  
- https://test.pypi.org/project/chibyterm/0.1.0/  

4. 若报版本已存在：升高 `packages/*/pyproject.toml` 与 `__init__.py` 中的 `version`（如 `0.1.1`），重新 build 再上传。

---

### 阶段 D：干净环境从 TestPyPI 安装（内部试用标准动作）

#### D.1 建干净 venv（Python ≥ 3.10）

```powershell
# 错误：python -m env Xxx
# 正确：
py -3.11 -m venv C:\Chiby
C:\Chiby\Scripts\Activate.ps1
python --version    # 确认 3.10+
python -m pip install -U pip
```

若提示无法运行 `Activate.ps1`：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

#### D.2 安装（索引顺序极其重要）

**推荐（主源 = 正式 PyPI，TestPyPI 仅补充）：**

```powershell
pip install --extra-index-url https://test.pypi.org/simple/ chibyterm
```

**不要**把 TestPyPI 设成唯一主源再装全家桶依赖，例如：

```powershell
# 容易失败 —— TestPyPI 上可能有假的 fastapi 包
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ chibyterm
```

**拆开装（最稳）：**

```powershell
pip install fastapi "uvicorn[standard]" pydantic httpx PyYAML python-dotenv python-multipart websockets
pip install --index-url https://test.pypi.org/simple/ --no-deps chibycore
pip install --index-url https://test.pypi.org/simple/ --no-deps chibyterm
```

#### D.3 验收

```powershell
pip show chibyterm
# Location 应在 ...\Lib\site-packages（不是 D:\Open\Assistant\packages）
# License: Apache-2.0
# Requires: 含 chibycore

python -c "from chibyterm.main import app; print(app.title)"
# 期望：ChibyTerm
# 日志：跳过 Hermes / 闭源演示路由（默认关）
```

---

### 阶段 E：内部试用（业务验收）

在能访问 `data/` 的目录启动（常用仓库根）：

```powershell
cd D:\Open\Assistant
$env:OPS_SHELL_PORT = "8000"
$env:OPS_MOBILE_DEMO = "0"
$env:OPS_HERMES_BRIDGE = "0"
python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

浏览器：`http://127.0.0.1:8000`  
顶栏应显示：**ChibyTerm · 赤壁终端**

| 检查项 | 期望 |
|--------|------|
| `/docs` | 可打开 |
| 默认 openapi | **无** `/ws/hermes`、`/api/mobile/*` |
| SSH/WinRM | 能开终端、敲命令 |
| 自然语言 | 能出计划/说明；高危有确认卡 |
| KnowledgeHub/DocHub | 接口可访问（空库也可） |
| 重启服务 | 配置仍可用 |

**可选：挂闭源扩展**

```powershell
# 先 build proprietary 包，再：
pip install --no-deps path\to\chiby_hermes_bridge-*.whl
pip install --no-deps path\to\chiby_mobile-*.whl
$env:OPS_MOBILE_DEMO_ENABLED = "1"
$env:OPS_HERMES_BRIDGE_ENABLED = "1"
# 再启动；日志应出现 entry_point 加载
```

环境变量 `OPS_*` 与包名解耦，**本次刻意未改名**（兼容旧配置）。

---

## 4. Python 库制作「完整知识链」（对照本次实操）

```text
写代码
  → pyproject.toml（name / version / dependencies / packages）
  → python -m build          # 产出 .whl + .tar.gz
  → 本地 pip install xxx.whl # 验导入
  → twine upload             # 传到 TestPyPI / PyPI
  → 他人 pip install 包名    # 从索引安装
```

| 概念 | 含义 | 本项目例子 |
|------|------|------------|
| 产品名 | 给人看的品牌 | ChibyTerm |
| 发行名（PyPI） | `pip install` 用的名字 | `chibyterm` |
| 导入名 | `import` 用的名字 | `import chibyterm` |
| `pyproject.toml` | 现代打包清单 | `packages/*/pyproject.toml` |
| wheel (`.whl`) | 预构建安装包 | `chibyterm-0.1.0-py3-none-any.whl` |
| sdist (`.tar.gz`) | 源码包 | 同步上传 |
| `requires-python` | 限制解释器版本 | `>=3.10` |
| dependencies | 运行时依赖 | `chibycore`、`fastapi`… |
| TestPyPI | 试发沙箱 | 依赖不全 / 有假包，主源要用正式 PyPI |
| 正式 PyPI | 对外发布 | P2-4，版本号不可随意撤回 |

---

## 5. 故障速查

| 现象 | 原因 | 处理 |
|------|------|------|
| `No module named env` | 命令写成 `python -m env` | 用 `python -m venv` |
| `No matching distribution` + Python 3.8 | 版本过低 | 换 3.10+ 重建 venv |
| 装 fastapi 报 `DESCRIPTION.txt` | TestPyPI 假包 | 主源用正式 PyPI（见 D.2） |
| `already satisfied` 但 Location 在仓库 | 装到了旧 editable | `pip uninstall` 后 `--no-cache-dir` 重装，或新 venv |
| 干净环境 `from terminal.xxx` 失败 | 包内仍用开发别名 | 包内改为 `from chibyterm.xxx` |
| twine `Credential not found` | 未设 Token | `TWINE_USERNAME=__token__` + `TWINE_PASSWORD` |
| 开源 pytest 非全绿 | 闭源测试未标 proprietary | 补 `@pytest.mark.proprietary` |

---

## 6. 正式 PyPI（P2-4）预告——内部试用稳定后再做

1. 确认 `version`（如保持 `0.1.0` 或升 `0.1.1`）。  
2. `python -m build packages/chibycore` 与 `chibyterm`。  
3. `twine upload` 到 **pypi**（需正式 PyPI Token）。  
4. 干净环境：`pip install chibyterm`（无需 TestPyPI）。  
5. 启用 README 中 PyPI badge。  
6. SVN 服务器侧打 tag（如 `tags/v0.1.0`）。

**建议：先内部试用数天，再上正式 PyPI。**

---

## 7. 本次里程碑对照（便于对账）

| 阶段 | 结果 |
|------|------|
| P0 | 入口零感知闭源、共享模型下沉、反向依赖切断 |
| P1 | mobile/hermes 物理迁入 proprietary；entry_points 动态加载 |
| P2-1 | LICENSE（Apache-2.0）、NOTICE、README、CONTRIBUTING |
| 包名统一 | `ops_*` → `chibyterm` / `chibycore`（SVN r89 已入库） |
| P2-3 | TestPyPI 已上传；干净环境可 `import ChibyTerm` |
| P2-4 | 未做（等内部试用） |

---

## 8. 最小「生产一日流程」卡片

```text
1. python --version          → ≥3.10
2. 改代码 → check_oss_boundary → 本地 import
3. python -m build packages\chibycore
4. python -m build packages\chibyterm
5. （可选）upload_testpypi.ps1
6. 干净 venv：
   pip install --extra-index-url https://test.pypi.org/simple/ chibyterm
7. python -c "from chibyterm.main import app; print(app.title)"
8. uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
9. svn commit
```

文档版本：2026-08-05（对齐 P2-3 收官后的状态）
