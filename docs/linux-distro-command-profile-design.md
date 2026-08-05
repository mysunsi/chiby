# 掌上AI机房 · Linux 发行版探测与命令族注入（设计一页）

> 状态：**P0a～P2 已落地**（探测→NL/闭环/Hermes→Job 扇出 per-host 命令族改写）  
> 日期：2026-07-19 / 更新 2026-07-20  
> 范围：SSH（及本机 Linux）连通后识别发行版 → 持久化到主机 → 注入 NL/闭环修复/无头规划的命令约束  
> 关联：`terminal/shell_context.py`、`terminal/models.py`（Host）、`chibycore/closure_llm_fix.py`、`terminal/llm_shell.py`、`docs/mobile-repair-rollback-design.md`  
> **本稿不要求立即编码。**

---

## 1. 背景与问题

当前系统只区分 **PowerShell vs unix（粗 Linux）**：

- 添加 SSH 主机 → 默认 `target_os=linux` → 通用 `df/free/systemctl`  
- **不会**自动区分 Debian/`apt`、RHEL/`dnf`、Alpine/`apk` 等  

后果：装包、服务启停、防火墙、日志路径等发行版相关任务，模型容易用错包管理器或假设 systemd 始终存在。

---

## 2. 目标与非目标

### 2.1 目标

1. SSH（或本机 Linux）**首次成功连通**后，只读探测发行版指纹  
2. 结果写入主机（及可选会话）的 **`distro_profile`**，可手改、可重探  
3. 所有「生成命令」路径注入同一套 **命令族提示**（包管理器 / init / 防火墙偏好）  
4. 闭环修复过滤与提示与 `shell_profile=unix` 叠加，仍禁止 PowerShell  

### 2.2 非目标

- 穷尽一切衍生发行版营销名（只映射到少数 **命令族**）  
- 替代用户审批高危变更  
- Windows / macOS 深度指纹（可后续对称扩展）  
- 在未连通主机时「猜」发行版  

---

## 3. 命令族（DistroProfile）

不按营销名分一百种，按**运维命令习惯**收敛：

| `family` | 典型发行版 | 包管理 | 服务/init | 备注 |
|----------|------------|--------|-----------|------|
| `debian` | Debian / Ubuntu / Mint | `apt` / `apt-get` | `systemctl`（为主） | 默认 SSH Linux 回落时可偏此 |
| `rhel` | RHEL / CentOS / Rocky / Alma / Fedora | `dnf`（优先）或 `yum` | `systemctl` | 探测到 `dnf` 则提示优先 dnf |
| `suse` | openSUSE / SLES | `zypper` | `systemctl` | |
| `alpine` | Alpine | `apk` | **OpenRC**（`rc-service`）为主 | 勿默认 systemctl |
| `arch` | Arch / Manjaro | `pacman` | `systemctl` | |
| `linux_generic` | 未能识别 | POSIX 通用 | 不假定 systemctl/apt | 先 `uname`/`ps`，装包前先探测 |

主机字段建议：

```text
distro_profile:
  family: debian|rhel|suse|alpine|arch|linux_generic
  id_like: []           # 来自 /etc/os-release ID_LIKE
  pretty_name: ""       # PRETTY_NAME
  id: ""                # ID（ubuntu/centos/…）
  version_id: ""
  pkg_manager: apt|dnf|yum|apk|zypper|pacman|unknown
  init_system: systemd|openrc|sysv|unknown
  probed_at: ISO8601
  probe_source: ssh_oneshot|session_connect|manual
  stale: false          # 超过 TTL 或用户点「重新探测」
```

会话侧可缓存一份只读副本；**权威落点在 Host**，避免每开 Tab 丢指纹。

---

## 4. 探测契约（只读、短超时）

### 4.1 触发时机（建议顺序）

| 时机 | 行为 |
|------|------|
| P0：终端会话 `CONNECTED`（SSH） | 后台 oneshot 探测；不堵首屏输入 |
| P0：主机「测试连接」/「重新探测发行版」按钮 | 同步探测并写回 Host |
| P1：掌上无头首次绑定该 `host_id` | 若 `stale` 或空则探测 |
| 手改 | UI 允许覆盖 `family` / `pkg_manager`（`probe_source=manual`） |

### 4.2 探测命令（单次 SSH exec，串成一段）

```bash
# 退出码 0；输出 JSON 友好键值（实现可用简单 KEY=VAL）
if [ -r /etc/os-release ]; then . /etc/os-release; fi
echo "PRETTY_NAME=${PRETTY_NAME-}"
echo "ID=${ID-}"
echo "ID_LIKE=${ID_LIKE-}"
echo "VERSION_ID=${VERSION_ID-}"
command -v systemctl >/dev/null && echo "HAS_SYSTEMCTL=1" || echo "HAS_SYSTEMCTL=0"
command -v rc-service >/dev/null && echo "HAS_OPENRC=1" || echo "HAS_OPENRC=0"
command -v apt-get >/dev/null && echo "HAS_APT=1" || echo "HAS_APT=0"
command -v dnf >/dev/null && echo "HAS_DNF=1" || echo "HAS_DNF=0"
command -v yum >/dev/null && echo "HAS_YUM=1" || echo "HAS_YUM=0"
command -v apk >/dev/null && echo "HAS_APK=1" || echo "HAS_APK=0"
command -v zypper >/dev/null && echo "HAS_ZYPPER=1" || echo "HAS_ZYPPER=0"
command -v pacman >/dev/null && echo "HAS_PACMAN=1" || echo "HAS_PACMAN=0"
uname -s; uname -m
```

约束：超时短（如 8～15s）；失败 → `linux_generic` + `stale=true`，**不阻断**会话。

### 4.3 映射规则（确定性，不交给 LLM）

```text
HAS_APK=1 且 (ID=alpine 或无 systemctl)     → alpine, pkg=apk, init=openrc
HAS_APT=1 且 ID/ID_LIKE 含 debian|ubuntu   → debian, pkg=apt
HAS_DNF=1 或 (HAS_YUM=1 且 rhel|centos|fedora|rocky|alma) → rhel, pkg=dnf|yum
HAS_ZYPPER=1                               → suse, pkg=zypper
HAS_PACMAN=1                               → arch, pkg=pacman
否则                                       → linux_generic
init: HAS_SYSTEMCTL=1 → systemd; HAS_OPENRC=1 → openrc; 否则 unknown
```

冲突时：**包管理器实装优先于 ID 字符串**（容器里常有假 os-release）。

---

## 5. 注入点（同一文案源）

新增 `build_distro_runtime_hint(profile) -> str`，挂到现有 `build_llm_runtime_hint` 之后（仅 unix）：

```text
【发行版命令族 — 必须遵守】
family=debian pretty=Ubuntu 22.04
- 装包/卸包：apt-get / apt（勿用 yum/dnf/apk）
- 服务：systemctl；日志：journalctl
- 防火墙：优先 ufw 或 nft/iptables（勿默认 firewalld）
```

| 调用方 | 用法 |
|--------|------|
| `llm_shell` NL→命令 | `runtime_hint` 追加 distro 段 |
| `closure_llm_fix` | `shell_profile=unix` + `distro_family` 写入 user JSON；system 提示点名包管理器 |
| 掌上 `hermes_protocol` / preamble | SSH 目标附 `distro_profile` 一行 |
| Agent / 命令集风险说明 | 可选展示「探测为 Ubuntu · apt」 |

**过滤（可选 P0.5）**：unix + `family=debian` 时，对修复候选中的 `yum install`/`dnf install`/`apk add` 打警告或降权（硬丢弃仅限明显跨族装包命令，避免误杀脚本里的字符串）。

---

## 6. API / UI

| 项 | 说明 |
|----|------|
| Host 模型 | 增加可选 `distro_profile` 对象（见上） |
| `POST /api/hosts/{id}/probe-distro` | 触发探测并返回/落库 |
| 主机列表 / 编辑 | 展示 `pretty_name` + family；按钮「重新探测」 |
| 终端状态栏 | 可选：`Linux · Ubuntu 22.04 (apt)`；手选 target_os 仍管大类 |
| TTL | 默认 7～30 天 `stale`；或主机 IP/镜像变更后强制重探 |

---

## 7. 落地顺序

| 步 | 内容 | 验收 |
|----|------|------|
| **P0a** ✅ | 探测脚本 + 映射纯函数 + Host 字段落库 | 单测覆盖 Ubuntu/Rocky/Alpine 映射 |
| **P0b** ✅ | 会话连接后异步探测；`build_distro_runtime_hint` 接入 `llm_shell` | NL hint 含 apt/dnf/apk（实机验收待做） |
| **P0c** ✅ | 闭环修复 JSON 带 `distro_family`；跨族装包候选过滤 | 单测：debian 丢弃 yum/apk |
| **P1** ✅ | 主机 UI 展示/重探；掌上 Hermes preamble + 多主机目录带 pkg | 徽章/重探/preamble 含 apt·dnf |
| **P2** ✅ | 多主机 Job 扇出按 per-host family 改写命令 | `job_distro_adapt`：apt↔dnf↔apk；Alpine `rc-service` |

---

## 8. 一句话验收

添加三台 SSH：Ubuntu、Rocky、Alpine → 连通后主机卡片分别显示 apt / dnf / apk → 右侧 Agent「安装 curl」生成对应包管理器命令，且闭环修复提示含同一 family。

---

## 9. 风险与克制

- **探测失败不等于不能用**：回落 `linux_generic` + 通用 POSIX。  
- **容器/精简镜像**：可能无 `systemctl`，映射以实装命令为准。  
- **不要**在 LLM 里「自由猜测发行版」；只允许执行探测脚本 + 确定性映射。  
- 与「修复失败自动回滚」正交：回滚管变更可逆，本稿管**命令方言**正确。
