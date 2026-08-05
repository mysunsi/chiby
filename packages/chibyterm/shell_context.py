"""根据终端会话推断 LLM 应使用的 shell / 命令体系。

发行版级（apt/dnf/apk）探测与注入见 docs/linux-distro-command-profile-design.md（设计稿）。
"""
from __future__ import annotations

import os
import sys
from enum import Enum
from typing import Any, Dict, List, Optional  # Any：可选 DistroProfile

from .models import ConnType, TerminalSession

# 与前端状态栏下拉框保持 id 一致；label 供 API / WebSocket 下发
TARGET_OS_OPTIONS: List[Dict[str, str]] = [
    {"id": "windows", "label": "Windows (PowerShell)"},
    {"id": "linux", "label": "Linux"},
    {"id": "macos", "label": "macOS"},
    {"id": "wsl", "label": "WSL (Windows 子系统)"},
    {"id": "freebsd", "label": "FreeBSD"},
    {"id": "unix_other", "label": "其他类 Unix"},
]

ALLOWED_TARGET_OS = frozenset(x["id"] for x in TARGET_OS_OPTIONS)

# 短标签：状态栏只读展示
TARGET_OS_SHORT_LABELS: Dict[str, str] = {
    "windows": "PowerShell",
    "linux": "Linux",
    "macos": "macOS",
    "wsl": "WSL",
    "freebsd": "FreeBSD",
    "unix_other": "Unix",
}


class ShellProfile(str, Enum):
    """生成命令与规则引擎所用的命令族。"""
    POWERSHELL = "powershell"  # 本机 Windows 终端或 WinRM
    UNIX = "unix"              # SSH 或本机 macOS/Linux 等


def target_os_from_uname(uname_s: str) -> Optional[str]:
    """由 uname -s 映射到 target_os；无法识别则 None。"""
    u = (uname_s or "").strip().lower()
    if not u:
        return None
    if u.startswith("darwin"):
        return "macos"
    if u.startswith("freebsd"):
        return "freebsd"
    if u.startswith("linux"):
        return "linux"
    if any(x in u for x in ("mingw", "msys", "cygwin", "windows_nt")):
        return "windows"
    if u.startswith("netbsd") or u.startswith("openbsd") or u.startswith("sunos") or u.startswith("aix"):
        return "unix_other"
    return "unix_other"


def target_os_from_distro_profile(profile: Any) -> Optional[str]:
    """SSH 发行版探测结果 → target_os（优先 uname）。"""
    if profile is None:
        return None
    uname = str(getattr(profile, "uname_s", "") or "").strip()
    tos = target_os_from_uname(uname)
    if tos:
        return tos
    family = str(getattr(profile, "family", "") or "").strip().lower()
    if family and family not in ("", "unknown"):
        # 有发行版指纹则按 Linux 处理（探测脚本本身是 Unix shell）
        return "linux"
    return None


def infer_default_target_os(session: TerminalSession) -> str:
    """
    打开终端时推断目标系统（连接后可经远端探测再校正）。
    - WinRM / 本机 Windows → windows
    - SSH → 默认 linux（探测 uname 后可改为 macos/freebsd 等）
    - 本机 macOS / Linux，并尝试识别 WSL
    """
    if session.conn_type == ConnType.WINRM:
        return "windows"
    if session.conn_type == ConnType.SSH:
        return "linux"
    # local
    plat = sys.platform
    if plat == "win32":
        return "windows"
    if plat == "darwin":
        return "macos"
    if plat.startswith("linux"):
        if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
            return "wsl"
        return "linux"
    if plat.startswith("freebsd"):
        return "freebsd"
    return "unix_other"


def target_os_short_label(os_id: str) -> str:
    return TARGET_OS_SHORT_LABELS.get(os_id, os_id or "?")


def _target_os_effective(session: Optional[TerminalSession]) -> str:
    if session is None:
        return "windows" if sys.platform == "win32" else "linux"
    v = (getattr(session, "target_os", None) or "").strip()
    if v in ALLOWED_TARGET_OS:
        return v
    return infer_default_target_os(session)


def resolve_shell_profile(session: Optional[TerminalSession]) -> ShellProfile:
    tos = _target_os_effective(session)
    if tos == "windows":
        return ShellProfile.POWERSHELL
    return ShellProfile.UNIX


def build_llm_runtime_hint(
    session: Optional[TerminalSession],
    distro_profile: Any = None,
) -> str:
    """
    注入到 LLM 用户消息前的硬性约束，避免在 PowerShell 下输出 df/free/systemctl。
    distro_profile：可选 DistroProfile，追加发行版命令族（apt/dnf/apk）。
    """
    tos = _target_os_effective(session)
    profile = resolve_shell_profile(session)

    if profile == ShellProfile.POWERSHELL:
        return (
            "【运行环境 — 必须严格遵守】\n"
            "当前终端为 Windows PowerShell（本机或 WinRM 远端）。\n"
            "- 禁止输出 Unix/Linux 专用命令：df、free、systemctl、ss、ip、iptables、"
            "uname、/proc、watch 等（除非用户明确要求在 WSL/bash 子系统中执行并写明）。\n"
            "- 磁盘/卷：Get-PSDrive、Get-Volume、Get-CimInstance Win32_LogicalDisk。\n"
            "- 内存：Get-CimInstance Win32_OperatingSystem；进程：Get-Process。\n"
            "- 网络：Get-NetTCPConnection、Test-NetConnection；服务：Get-Service / Restart-Service。\n"
            "- 系统信息：systeminfo 或 Get-ComputerInfo。\n"
            "仅生成可在当前 PowerShell 中直接执行的命令。"
        )

    if tos == "macos":
        base = (
            "【运行环境 — 必须严格遵守】\n"
            "当前为 macOS 终端（bash/zsh）。使用 macOS 常见工具："
            "diskutil、vm_stat、top、ps、lsof、networksetup、brew（若已装）、launchctl 等；"
            "不要输出 Windows PowerShell cmdlet 或仅适用于 Linux 发行版路径（如 /etc/os-release 可存在但注意差异）。\n"
        )
    elif tos == "wsl":
        base = (
            "【运行环境 — 必须严格遵守】\n"
            "当前为 WSL（Windows 子系统中的 Linux 用户区）。"
            "使用常规 Linux 命令（df、free、systemctl 在部分 WSL2 上可能无 systemd，需用 service 或说明限制）。\n"
            "不要输出 PowerShell cmdlet，除非用户明确要求在 Windows 侧执行。\n"
        )
    elif tos == "freebsd":
        base = (
            "【运行环境 — 必须严格遵守】\n"
            "当前为 FreeBSD 或类 BSD。优先使用 BSD 风格命令（pkg、service、bsdstat、dmesg、sysctl 等），"
            "避免假定 Linux 特有路径与 systemctl。\n"
        )
    elif tos == "unix_other":
        base = (
            "【运行环境 — 必须严格遵守】\n"
            "当前为类 Unix 环境。使用 POSIX 常见命令；避免 Windows PowerShell cmdlet；"
            "若不确认发行版，优先通用语法（sh、ps、df、netstat 等）。\n"
        )
    else:
        # linux (default unix)
        base = (
            "【运行环境 — 必须严格遵守】\n"
            "当前终端为 Linux（或 SSH 至 Linux）。\n"
            "使用 df、free、systemctl、ss、ip、journalctl 等常规 Linux 运维命令；不要输出 PowerShell cmdlet。\n"
        )

    if distro_profile is not None and profile != ShellProfile.POWERSHELL:
        try:
            from chibyterm.distro_profile import build_distro_runtime_hint

            extra = build_distro_runtime_hint(distro_profile)
            if extra:
                return base + "\n" + extra
        except Exception:
            pass
    return base


def session_meta_payload(session_id: str, session: TerminalSession) -> Dict[str, Any]:
    """WebSocket session_meta 消息体。"""
    tos = _target_os_effective(session)
    return {
        "type": "session_meta",
        "session_id": session_id,
        "target_os": tos,
        "target_os_label": target_os_short_label(tos),
        "os_options": list(TARGET_OS_OPTIONS),
        "os_auto": True,
    }
