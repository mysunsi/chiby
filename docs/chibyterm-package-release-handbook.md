# ChibyTerm 开源包制作与发布流程手册（生产操作版）

> **整份手册在干什么：** 教你把源码「打包成可 `pip install` 的 Python 库」，并走完试发、试用。  
> **学完应能：** 自己构建、上传 TestPyPI、在干净环境安装启动，并知道每一步为什么存在。

> 适用对象：需要自行走通「开发 → 构建 → TestPyPI → 试用 →（可选）正式 PyPI」的生产/发布人员。  
> 仓库：SVN `Open/Assistant`（非 git）。  
> 产品名：**ChibyTerm（赤壁终端）**；PyPI/导入名：`chibyterm` + `chibycore`。  
> 版本基线：`0.1.0`（以各包 `pyproject.toml` 为准）。

---

## 0. 一句话目标

> **本步做什么：** 先认清最终要交付的是哪几个「包」，各自给谁用。  
> **要达成什么：** 脑子里有一张图——开源两包 + 闭源两包，而不是「一整坨代码直接拷贝」。

把原来的单体运维终端，拆成：

| 包 | 角色 | 分发 |
|----|------|------|
| **chibycore** | 执行网关、闭环、KnowledgeHub、DocHub | 开源 wheel |
| **chibyterm** | Web 终端入口（依赖 chibycore） | 开源 wheel |
| **chiby-mobile / chiby-hermes-bridge** | 掌上机房、Hermes 桥 | 闭源 wheel（`proprietary/`，不进公开仓） |

社区：`pip install chibyterm` 即可跑开源终端；企业再叠加闭源插件（`chiby.plugins` entry_points）。

---

## 1. 目录与命名（必须先记住）

> **本步做什么：** 对照仓库目录，认清「源码在哪、闭源在哪、脚本在哪」。  
> **要达成什么：** 后面敲命令时知道该进哪个文件夹，不会把 `packages` 和 `proprietary` 搞混；也知道 `terminal` 只是开发别名。

```text
Assistant/
├── packages/
│   ├── chibycore/          # 开源底层库（独立 pyproject.toml）
│   └── chibyterm/          # 开源终端包（独立 pyproject.toml）
├── proprietary/
│   ├── chiby_mobile/       # 闭源：掌上
│   └── chiby_hermes_bridge/# 闭源：Hermes 桥
├── data/                   # 运行时配置（hosts、llm 等；勿提交密钥）
├── LICENSE / NOTICE        # Apache-2.0 + 第三方声明
├── scripts/
│   ├── check_oss_boundary.py   # 开源边界门禁
│   └── upload_testpypi.ps1     # TestPyPI 上传
├── terminal/__init__.py    # 仅开发便利：别名 → chibyterm（正式 wheel 不含此别名）
├── conftest.py / path_alias.py
└── pyproject.toml          # Monorepo 开发用（editable）；正式分发以 packages/*/pyproject.toml 为准
```

**曾用名（已废弃，仅兼容别名）：**

- `ops_terminal` / `ops-terminal` → **`chibyterm`**
- `ops_core` → **`chibycore`**

**环境变量**（刻意未改，与包名解耦）：`OPS_SHELL_PORT`、`OPS_MOBILE_DEMO*`、`OPS_HERMES_BRIDGE*` 等。

---

## 2. 完整制作 Python 库的标准流程（通用）

> **本步做什么：** 先学「做任意 Python 库」的通用套路（与 Chiby 业务无关）。  
> **要达成什么：** 理解 `pyproject.toml → build → wheel → 试装 → 上传索引` 这条流水线，后面跟做本项目只是「套模板填命令」。

下面是与业务无关的「做库」闭环；后文第 3 节是本项目的具体命令。

```mermaid
flowchart LR
  A[写代码 + pyproject.toml] --> B[本地 python -m build]
  B --> C[得到 .whl + .tar.gz]
  C --> D[干净 venv 试装]
  D --> E[import / 启动冒烟]
  E --> F[上传 TestPyPI]
  F --> G[他人机器从 TestPyPI 安装验证]
  G --> H[内部试用]
  H --> I[上传正式 PyPI]
  I --> J[pip install 包名]
```

### 2.1 每个可发布包最少要有

> **本步做什么：** 对照检查「一个能发布的包」缺不缺件。  
> **要达成什么：** 知道没有 `pyproject.toml` / 没有 `__init__.py` 就谈不上 `pip install`。

1. **包目录**（含 `__init__.py`，建议有 `__version__`）  
2. **`pyproject.toml`**（现代打包标准入口）  
   - `[project] name / version / requires-python / dependencies / license`  
   - `[tool.setuptools]`：包发现、`package-data`（静态资源如 `web/`）  
3. **构建后端**：`setuptools` + `wheel`（本项目已用）  
4. **工具**：`pip install build twine`

### 2.2 构建产物

> **本步做什么：** 认清 `python -m build` 生成的两个文件分别干什么。  
> **要达成什么：** 知道日常安装用的是 `.whl`；`.tar.gz` 是源码包，一般给需要从源码编译的场景。

```text
packages/<包名>/dist/
  <包名>-0.1.0-py3-none-any.whl   # 安装用
  <包名>-0.1.0.tar.gz             # 源码包
```

### 2.3 两个索引的区别（生产必懂）

> **本步做什么：** 分清 TestPyPI（练习场）和 PyPI（正式商店）。  
> **要达成什么：** 试装时不踩「把 TestPyPI 当主源 → 下到假依赖包」的坑。

| 索引 | 用途 |
|------|------|
| **TestPyPI** | 试发、可反复覆盖实验；依赖不全，常有同名垃圾包 |
| **PyPI** | 正式发布；`pip install 包名` 的默认源 |

**试装铁律：**

- 主源用 **正式 PyPI**（拉 fastapi 等）  
- TestPyPI 只用 **`--extra-index-url`**（拉你们自己的包）  
- **不要**把 TestPyPI 设成 `--index-url` 主源（会下到假 FastAPI）

### 2.4 Token

> **本步做什么：** 准备上传用的「钥匙」（API Token）。  
> **要达成什么：** 能让 `twine upload` 通过身份校验；用户名固定为 `__token__`。

- TestPyPI：https://test.pypi.org/manage/account/token/  
- 正式 PyPI：https://pypi.org/manage/account/token/  
- 上传时：`TWINE_USERNAME=__token__`，`TWINE_PASSWORD=pypi-...`

---

## 3. 本项目：从开发到 TestPyPI 的操作步骤

> **本章做什么：** 按 A→H 把 ChibyTerm 从「能改代码」做到「别人能从 TestPyPI 装上并启动使用」。  
> **要达成什么：** 生产人员可以照着敲命令复现整条发布链，并明白每步卡点在验什么。

### 步骤 A — 开发环境（本仓）

> **本步做什么：** 在仓库里装好依赖，把服务跑起来，确认界面是 ChibyTerm。  
> **要达成什么：** 开发/联调可以改代码立刻验证；为后面「打包」备好可运行的源码树。  
> **通俗理解：** 先保证「自家厨房能炒菜」，再谈「做成罐头外卖」。

```powershell
cd D:\Open\Assistant
# 建议 Python >= 3.10（仓库要求 >=3.10；开发机常用 3.11）
python --version

# 方式 1：editable（改代码立刻生效）
pip install -e .
pip install -r requirements.txt

# 方式 2：仅 PYTHONPATH（轻量）
$env:PYTHONPATH = "packages;proprietary\chiby_mobile\src;proprietary\chiby_hermes_bridge\src"
```

启动（数据目录在仓库根 `data/`）：

```powershell
cd D:\Open\Assistant
$env:OPS_MOBILE_DEMO = "0"
$env:OPS_HERMES_BRIDGE = "0"
python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

浏览器：`http://127.0.0.1:8000` → 顶栏应为 **ChibyTerm · 赤壁终端**。

开发期也可用过渡命令：`uvicorn terminal.main:app`（靠根目录 `terminal/` 别名；**正式 wheel 安装后不要依赖 `terminal`**）。

### 步骤 B — 开源边界门禁

> **本步做什么：** 跑一遍自动检查脚本，看开源目录有没有「偷偷引用闭源」。  
> **要达成什么：** 防止把闭源能力打进公开 wheel；门禁不过就不要上传。  
> **通俗理解：** 出厂前安检——开源箱子里不能塞闭源零件。

```powershell
cd D:\Open\Assistant
python scripts\check_oss_boundary.py
# 可选：检查刚打的 wheel
python scripts\check_oss_boundary.py --wheel packages\chibyterm\dist\chibyterm-0.1.0-py3-none-any.whl
```

通过应看到：`OSS boundary check OK`。  
规则要点：`packages/` 内禁止硬编码 `import proprietary` / `chiby_mobile` / `chiby_hermes_bridge`（闭源用 entry_points / importlib 惰性加载）。

### 步骤 C — 分别构建两个开源包

> **本步做什么：** 对 `chibycore`、`chibyterm` 各执行一次 `python -m build`，生成 `.whl`。  
> **要达成什么：** 得到可安装的分发文件（在各自 `dist/` 下）；顺序上先底层库、后终端包。  
> **通俗理解：** 把源码「压成安装包」——还没上网，先在本地做成罐头。

```powershell
cd D:\Open\Assistant
python -m pip install -U build twine

# 先底层，再终端
python -m build packages\chibycore
python -m build packages\chibyterm
```

检查：

```powershell
dir packages\chibycore\dist
dir packages\chibyterm\dist
```

### 步骤 D — 本地干净环境冒烟（不经过 TestPyPI）

> **本步做什么：** 新建一个空的虚拟环境，只装刚打好的 wheel，试 `import`。  
> **要达成什么：** 证明「离开本仓库目录也能装上、能导入」，排除「其实还在用源码路径」的假象。  
> **通俗理解：** 换一台「空桌子」拆罐头吃——确保不是靠厨房里的散料才吃得上。

```powershell
python -m venv D:\venv\chiby-smoke
D:\venv\chiby-smoke\Scripts\Activate.ps1
python -m pip install -U pip

# 先装 wheel（可 --no-deps 再补依赖；或直接装 chibyterm wheel 让 pip 解析）
pip install packages\chibycore\dist\chibycore-0.1.0-py3-none-any.whl
pip install packages\chibyterm\dist\chibyterm-0.1.0-py3-none-any.whl

python -c "from chibyterm.main import app; print(app.title)"
# 预期：ChibyTerm
```

确认 `pip show chibyterm` 的 **Location** 在该 venv 的 `site-packages`，而不是 `D:\Open\Assistant\packages`。

### 步骤 E — 上传 TestPyPI

> **本步做什么：** 用 Token 把本地 `dist/*` 上传到 TestPyPI（先 chibycore，再 chibyterm）。  
> **要达成什么：** 网上出现可被别人 `pip install` 的试发版本；网页能打开项目页。  
> **通俗理解：** 把罐头放到「试销货架」——还不是正式商店，但外人已经能按流程拿货。

```powershell
$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = "pypi-你的TestPyPI令牌"
powershell -File scripts\upload_testpypi.ps1
```

成功后可见：

- https://test.pypi.org/project/chibycore/0.1.0/  
- https://test.pypi.org/project/chibyterm/0.1.0/  

若提示版本已存在：升高 `packages/*/pyproject.toml` 与 `__init__.py` 中的 `version`（如 `0.1.1`），重新 build 再传。

### 步骤 F — 从 TestPyPI 安装（给试用机）

> **本步做什么：** 在**另一台机器/新 venv**（Python≥3.10）上，从网上把包装回来。  
> **要达成什么：** 验证「别人按文档安装」走得通；依赖从正式 PyPI 拉，自家包从 TestPyPI 拉。  
> **通俗理解：** 模拟客户装软件——用正确货源组合，避免试销货架上的假冒配件。

**要求：Python ≥ 3.10**（3.8 会报 `No matching distribution`）。

```powershell
# 用 3.11 建环境（示例）
py -3.11 -m venv C:\Chiby
C:\Chiby\Scripts\Activate.ps1
python --version          # 确认 3.10+
python -m pip install -U pip

# 正确：正式 PyPI 为主，TestPyPI 为补充
pip install --extra-index-url https://test.pypi.org/simple/ chibyterm

pip show chibyterm
python -c "from chibyterm.main import app; print(app.title)"
```

**错误示范（易踩坑）：**

```powershell
# 不要这样：TestPyPI 当主源 → 可能装到假 fastapi
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ chibyterm
```

若仍冲突，可拆开：

```powershell
pip install fastapi "uvicorn[standard]" pydantic httpx PyYAML python-dotenv python-multipart websockets
pip install --index-url https://test.pypi.org/simple/ --no-deps chibycore
pip install --index-url https://test.pypi.org/simple/ --no-deps chibyterm
```

### 步骤 F2 — 干净 venv：配置 data 并启动使用

> **本步做什么：** 在**不依赖源码仓**的独立工作目录里准备 `data/`，用已安装的 `chibyterm` 启动 Web 服务并打开浏览器。  
> **要达成什么：** 模拟真实用户「只装了 pip 包」也能跑通整端；确认配置读的是工作目录下的 `data/`，而不是 `D:\Open\Assistant`。  
> **通俗理解：** 换空厨房只靠罐头做饭——工作目录就是你的灶台，`data/` 是调料罐。

**前提：** 已完成步骤 F（`C:\Chiby` 已 Activate，且 `pip show chibyterm` 的 Location 在该 venv 的 `site-packages`）。

```powershell
# 仍在已 Activate 的 C:\Chiby 环境中

# 1) 独立工作目录（与源码仓分离）
mkdir C:\ChibyWork\data -Force
cd C:\ChibyWork

# 2) 最小主机配置（改成你的机器；密码勿提交仓库）
@'
{
  "hosts": [
    {
      "id": "demo",
      "name": "demo",
      "host": "192.168.1.100",
      "port": 22,
      "username": "root",
      "password": "改成你的密码",
      "conn_type": "ssh"
    }
  ]
}
'@ | Set-Content -Encoding utf8 data\hosts.json

# 3) 启动（必须在含 data/ 的目录下；wheel 无仓库根时 PROJECT_ROOT=当前工作目录）
$env:OPS_MOBILE_DEMO = "0"
$env:OPS_HERMES_BRIDGE = "0"
python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

浏览器打开：http://127.0.0.1:8000  

顶栏应为 **ChibyTerm · 赤壁终端**。用法：

1. 左侧选主机 / 新建会话  
2. 左侧终端直接敲 Shell，或输入自然语言（如「查看磁盘使用情况」）  
3. 高危命令会出确认卡，点允许后才执行  
4. API 文档：http://127.0.0.1:8000/docs  

**可选：自然语言（LLM）** —— 不配也能当普通 Web 终端；要试用 NL 可在启动前设置：

```powershell
$env:OPENAI_API_KEY = "sk-..."
# 或按实际模型编写 C:\ChibyWork\data\llm_config.json
# （可参考源码仓 data/llm_config.example.json，再拷到工作目录）
```

**干净环境验收（安装侧）：**

| 检查 | 预期 |
|------|------|
| `pip show chibyterm` 的 Location | 在 `C:\Chiby\Lib\site-packages`，**不是** `D:\Open\Assistant` |
| 打开 `/` | 顶栏 **ChibyTerm · 赤壁终端** |
| 打开 `/docs` | Swagger 正常；默认无 `/ws/hermes`、`/api/mobile/*` |

**注意：**

- 当前**没有**独立 CLI 命令 `chibyterm`；一律用 `python -m uvicorn chibyterm.main:app`（不要用 `terminal.main`）。  
- 停服务：终端 `Ctrl+C`。下次先 `C:\Chiby\Scripts\Activate.ps1`，再 `cd C:\ChibyWork` 后启动。  
- 业务功能细项见下一步「步骤 G」。

### 步骤 G — 内部试用（业务验收）

> **本步做什么：** 用装好的包连真实主机，按清单点功能。  
> **要达成什么：** 确认「能装」之外还「能干活」；默认不开闭源路由；问题记下来再发正式版。  
> **通俗理解：** 试吃几天——包装合格还不够，菜品味道和稳定性要过关。

**推荐：** 干净 venv 按 **步骤 F2** 在 `C:\ChibyWork` 启动后再做下表。  
**备选：** 若在本仓联调，可直接用仓库根的 `data/`：

```powershell
cd D:\Open\Assistant
$env:OPS_MOBILE_DEMO = "0"
$env:OPS_HERMES_BRIDGE = "0"
python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
```

试用检查表：

| # | 检查项 | 预期 |
|---|--------|------|
| 1 | 打开 `/` | 顶栏 **ChibyTerm · 赤壁终端** |
| 2 | 打开 `/docs` | Swagger 可用 |
| 3 | 默认无闭源路由 | 无 `/ws/hermes`、`/api/mobile/*` |
| 4 | SSH/WinRM | 能开终端、敲命令有回显 |
| 5 | 自然语言 | 能生成/执行；高危出确认卡 |
| 6 | KnowledgeHub/DocHub | 搜索接口可访问 |
| 7 | 重启进程 | 配置仍可读 |

可选：安装闭源 wheel 并打开开关，验证 entry_points 动态注册（企业场景）。

### 步骤 H — SVN 提交（开发侧）

> **本步做什么：** 把源码与文档变更提交到 SVN（不含密钥、venv、dist）。  
> **要达成什么：** 团队共享同一基线；发布记录可追溯。  
> **通俗理解：** 菜谱入库——罐头配方要进版本库，厨房垃圾不要进。

```powershell
cd D:\Open\Assistant
svn status
# 勿提交：.env、.venv、dist、*.egg-info、data 运行时库/密钥
svn commit -m "说明本次变更"
```

已完成示例：开源拆分与包重命名等已合入 **r89**；操作手册初版 **r90**（以服务器日志为准）。

---

## 4. 昨天到今天：我们实际走过的阶段（对照用）

> **本步做什么：** 浏览历史阶段表，把「目录为什么长这样」和演进顺序对上号。  
> **要达成什么：** 新人知道这不是一次性拍脑袋，而是 P0→P2 逐步解耦后再做库发布。

便于生产人员理解「为什么目录长这样」。

| 阶段 | 做了什么 | 结果形态 |
|------|----------|----------|
| **P0** | 入口对闭源零硬依赖；共享模型下沉；断反向 import；插件开关 | 默认可零闭源启动 |
| **P0-6/7/8** | `packages/` + `proprietary/` 草创；wheel exclude；门禁脚本 | 可安装、可排除、可门禁 |
| **P1** | `svn move` 闭源到 `proprietary/*/src`；独立闭源 pyproject + `chiby.plugins` | 物理隔离 + entry_points |
| **品牌** | UI/文档 OPSSHELL → **ChibyTerm** | 用户可见名统一 |
| **P2-1** | `LICENSE`（Apache-2.0）、`NOTICE`、README 快速开始、`CONTRIBUTING` | 合规门面 |
| **包名统一** | `ops_*` → `chibyterm` / `chibycore` | PyPI 名与 import 名一致 |
| **P2-3** | 分包 pyproject、build、上传 TestPyPI、干净环境验证 | 试发完成 |
| **试用踩坑** | Python 3.8 装不上；TestPyPI 主源装到假 FastAPI | 见第 5 节 |

测试说明：`pytest -m "not proprietary" --collect-only` 约 **596**；全量跑曾出现失败（多为未标 `proprietary` 的闭源相关用例），开源 CI 全绿需另补 marker——**不阻塞包发布试发**。

---

## 5. 常见问题（生产排障）

> **本步做什么：** 对照报错表快速定位。  
> **要达成什么：** 常见坑（Python 版本、索引优先级、别名）能自行解决，少打断研发。

| 现象 | 原因 | 处理 |
|------|------|------|
| `No matching distribution for chibyterm` | Python &lt; 3.10，或 pip 过旧 | 用 3.10+；`python -m pip install -U pip` |
| 装 fastapi 报 `DESCRIPTION.txt` / FASTAPI-1.0 | TestPyPI 当主源，下到假包 | 改用 `--extra-index-url` 指向 TestPyPI |
| `Requirement already satisfied` 却像没更新 | 环境里已有同版本本地包 | `pip uninstall` 后重装，或新建 venv |
| `from terminal.main import` 在干净环境失败 | `terminal` 只是开发别名 | 正式安装一律用 `chibyterm` |
| 导入成功但找不到 `data/hosts.json` | 工作目录不是仓库根 | 在 `Assistant` 下启动，或自备 `data/` |
| 构建产物被 SVN 扫到 | `dist` / `egg-info` | 已设 ignore，勿 `svn add` |

---

## 6. 正式 PyPI（P2-4）预览——内部试用稳定后再做

> **本步做什么：** 了解正式上架清单（暂不要求立刻执行）。  
> **要达成什么：** 知道试用通过后还要改版本号、传正式源、打 tag；且正式版号不能随意覆盖。  
> **通俗理解：** 试销没问题，再进「正规超市货架」。

1. 确认版本号（`packages/chibycore` 与 `packages/chibyterm` 同步）  
2. `python -m build` 两包  
3. `twine upload` 到 **pypi**（先 chibycore，后 chibyterm）  
4. 干净环境：`pip install chibyterm`（无需 TestPyPI）  
5. 启用 README 中 PyPI badge  
6. SVN 服务器侧打 tag（如 `tags/v0.1.0`）  

**注意：正式 PyPI 同一版本号原则上不能覆盖，试发请继续用 TestPyPI。**

---

## 7. 生产人员最小抄写清单（一页纸）

> **本步做什么：** 把 A～F2 缩成最少命令，便于现场照抄。  
> **要达成什么：** 熟练后不开长文也能完成「构建 → 上传 → 干净环境安装 → 启动」。

```powershell
# 0) Python 3.11 + 升级 pip
py -3.11 -m venv C:\Chiby
C:\Chiby\Scripts\Activate.ps1
python -m pip install -U pip build twine

# 1) 本仓构建（在 D:\Open\Assistant）
cd D:\Open\Assistant
python -m build packages\chibycore
python -m build packages\chibyterm
python scripts\check_oss_boundary.py

# 2) 上传 TestPyPI
$env:TWINE_USERNAME="__token__"; $env:TWINE_PASSWORD="pypi-..."
powershell -File scripts\upload_testpypi.ps1

# 3) 干净 venv 安装（主源正式 PyPI）
pip install --extra-index-url https://test.pypi.org/simple/ chibyterm
python -c "from chibyterm.main import app; print(app.title)"

# 4) 独立工作目录启动（干净试用；勿依赖源码仓）
mkdir C:\ChibyWork\data -Force
cd C:\ChibyWork
# 先写好 data\hosts.json（见步骤 F2），再：
$env:OPS_MOBILE_DEMO="0"; $env:OPS_HERMES_BRIDGE="0"
python -m uvicorn chibyterm.main:app --host 127.0.0.1 --port 8000
# 浏览器：http://127.0.0.1:8000
```

---

## 8. 相关文件索引

> **本步做什么：** 需要改配置/查脚本时按表找文件。  
> **要达成什么：** 减少在仓库里盲目搜索的时间。

| 文件 | 用途 |
|------|------|
| `packages/chibycore/pyproject.toml` | 底层库元数据与依赖 |
| `packages/chibyterm/pyproject.toml` | 终端包元数据；依赖 `chibycore` |
| `proprietary/*/pyproject.toml` | 闭源包 + `chiby.plugins` |
| `scripts/upload_testpypi.ps1` | TestPyPI 上传 |
| `scripts/check_oss_boundary.py` | 开源边界门禁 |
| `README.md` | 快速开始（对外） |
| `docs/open-source-boundary-review.md` | 开源/闭源切分约束 |

---

**文档版本：** 2026-08-05（对应 SVN 开源拆分与 TestPyPI 试发完成后的操作基线；含各步骤导读 + **步骤 F2 干净 venv 启动使用**）
