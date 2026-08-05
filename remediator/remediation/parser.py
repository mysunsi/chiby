"""结构化解析：从 stderr/stdout + returncode 提取错误要素。"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from .models import ErrorCategory, StructuredError


# ─── 正则模式（顺序有意义：先匹配更具体的）───────────────────────────────────

_PERM_DENIED = re.compile(
    r"(Permission denied|不允许的操作|拒绝访问)[^\n]*",
    re.IGNORECASE,
)
_PERM_PATH = re.compile(
    r"(?:cannot create regular file|cannot touch|cannot mkdir|cannot remove|open)\s+[`'\"]?([^`'\"\s]+)[`'\"]?",
    re.IGNORECASE,
)
_PATH_IN_SINGLE_QUOTES = re.compile(r"[`']((?:/|[A-Za-z]:\\)[^`'\"]+)[`'\"]")

_NO_SUCH = re.compile(
    r"(?:No such file or directory|没有那个文件或目录)|(?:cannot access)\s+[`'\"]?([^`'\"\n]+)[`'\"]?",
    re.IGNORECASE,
)

# 拼写建议（git、部分 CLI）
_DID_YOU_MEAN = re.compile(
    r"Did you mean\s+['`\"]?([^'\"`\n]+)['`]?",
    re.IGNORECASE,
)
_SUGGEST_PATH = re.compile(
    r"(?:suggest|提示)[:：]?\s*['\"]?((?:/|[A-Za-z]:\\)[^'\"\n]+)",
    re.IGNORECASE,
)

_SUDO_HINT = re.compile(
    r"(try with sudo|Operation not permitted|需要管理员|请使用 sudo|use sudo|root required)",
    re.IGNORECASE,
)

_CMD_NOT_FOUND = re.compile(
    r"(?:command not found|not found|不是内部或外部命令)"
    r"|(?:(?:bash|sh|zsh|\./[^:]+):\s*[^:]+:\s*command not found)",
    re.IGNORECASE,
)
# bash: mvn: command not found
_CMD_NOT_FOUND_NAMED = re.compile(
    r"(?:^|\n)\s*([A-Za-z0-9._+/@-]+):\s*command not found",
    re.IGNORECASE | re.MULTILINE,
)
_WHICH_BINARY = re.compile(
    r"(?:^|\s)(?:command not found:|([^:\s]+):\s*command not found|([^:\s]+):\s+not found)",
    re.IGNORECASE | re.MULTILINE,
)

# 网络：先拆「超时/不可达」与泛化网络
_NET_UNREACHABLE = re.compile(
    r"(Connection timed out|timed out|ETIMEDOUT|Connection reset by peer|"
    r"Network is unreachable|No route to host|Name or service not known|"
    r"Could not resolve host|Connection refused|"
    r"无法连接|连接超时|没有到主机的路由|名称或服务未知)",
    re.IGNORECASE,
)
_NET_GENERIC = re.compile(
    r"(SSL connection|TLS|certificate|Operation not supported|"
    r"网络|连接错误|connect\(\) failed)",
    re.IGNORECASE,
)

_SERVER_DOWN = re.compile(
    r"(Connection refused).*:?\s*\d+|(?:errno\s*111)|(?:actively refused)",
    re.IGNORECASE,
)

_SYNTAX = re.compile(
    r"(Syntax error|unexpected token|语法错误|unexpected EOF|bash:\s*-c:\s*line)",
    re.IGNORECASE,
)

_DISK_FULL = re.compile(r"(No space left on device|磁盘已满)", re.IGNORECASE)

_HW = re.compile(r"(I/O error|Medium error|硬件|磁盘损坏)", re.IGNORECASE)

# 常见可执行名 → 包管理器包名（Debian/Ubuntu 系常见映射，供 requires_package 使用）
_BINARY_TO_PACKAGE: dict[str, str] = {
    "mvn": "maven",
    "gradle": "gradle",
    "node": "nodejs",
    "npm": "npm",
    "npx": "npm",
    "nginx": "nginx",
    "docker": "docker.io",
    "docker-compose": "docker-compose",
    "kubectl": "kubectl",
    "pip": "python3-pip",
    "pip3": "python3-pip",
    "python3": "python3",
    "java": "default-jre",
    "javac": "default-jdk",
    "go": "golang-go",
    "git": "git",
    "curl": "curl",
    "make": "build-essential",
    "systemctl": "systemd",
    "apt": "apt",
}

_SYSTEM_WRITE_PATH = re.compile(
    r"^/(?:etc|root|usr/lib|usr/sbin)(?:/|$)",
    re.IGNORECASE,
)


def _extract_path_blob(text: str) -> Optional[str]:
    m = _PATH_IN_SINGLE_QUOTES.search(text)
    if m:
        return m.group(1).strip()
    return None


def _extract_missing_command_name(blob: str) -> Optional[str]:
    m = _CMD_NOT_FOUND_NAMED.search(blob)
    if m:
        return m.group(1).strip()
    m2 = _WHICH_BINARY.search(blob)
    if m2:
        g = m2.group(1) or m2.group(2)
        if g and re.match(r"^[A-Za-z0-9._+/@-]+$", g):
            return g.strip()
    return None


def _package_for_binary(binary: str) -> Optional[str]:
    b = (binary or "").strip().lower()
    if not b or b in (":", "sh", "bash", "zsh"):
        return None
    if b in _BINARY_TO_PACKAGE:
        return _BINARY_TO_PACKAGE[b]
    if re.match(r"^[a-z][a-z0-9-]*$", b) and len(b) <= 32:
        return b
    return None


def _is_sudo_style_permission(path_hint: Optional[str], blob: str) -> bool:
    if _SUDO_HINT.search(blob):
        return True
    p = (path_hint or "").replace("\\", "/")
    if p and _SYSTEM_WRITE_PATH.match(p):
        return True
    if "Permission denied" in blob and ("/etc/" in blob or "/root/" in blob):
        return True
    return False


def _is_typo_suggested(blob: str) -> bool:
    if _DID_YOU_MEAN.search(blob) or _SUGGEST_PATH.search(blob):
        return True
    if re.search(r"没有那个文件|No such file", blob) and re.search(
        r"(typo|拼写|是否想|Did you mean)", blob, re.IGNORECASE
    ):
        return True
    return False


def parse_execution_error(
    *,
    command: str,
    return_code: int,
    stdout: str = "",
    stderr: str = "",
) -> StructuredError:
    """
    第一步：合并 stderr/stdout 与 return_code，输出 StructuredError。

    示例：
      cp: cannot create regular file '/tmp/app.log': Permission denied
    → PERMISSION_DENIED / PERMISSION_DENIED_SUDO, path=/tmp/app.log
    """
    blob = (stderr or "") + "\n" + (stdout or "")
    blob_lc = blob.lower()
    path_hint = _extract_path_blob(blob) or None

    category, reason, requires_pkg = _classify(
        blob, blob_lc, return_code, path_hint, command
    )

    if category in (ErrorCategory.PERMISSION_DENIED, ErrorCategory.PERMISSION_DENIED_SUDO) and not reason:
        reason = "无写入权限或不允许的操作"

    return StructuredError(
        error_category=category,
        return_code=return_code,
        path=path_hint,
        reason=reason or _default_reason(category, return_code),
        stderr_snippet=_snippet(stderr),
        stdout_snippet=_snippet(stdout),
        raw_stderr=stderr or "",
        raw_stdout=stdout or "",
        metadata={"command": (command or "").strip()},
        requires_package=requires_pkg,
    )


def _snippet(text: str, max_len: int = 800) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _default_reason(cat: ErrorCategory, rc: int) -> str:
    if rc == 127:
        return "命令未找到（退出码 127）"
    if rc == 126:
        return "命令不可执行（退出码 126）"
    if cat in (ErrorCategory.PERMISSION_DENIED, ErrorCategory.PERMISSION_DENIED_SUDO):
        return "权限不足"
    return f"进程退出码 {rc}"


def _classify(
    blob: str,
    blob_lc: str,
    return_code: int,
    path_hint: Optional[str],
    _original_command: str,
) -> Tuple[ErrorCategory, str, Optional[str]]:
    requires_pkg: Optional[str] = None

    if _HW.search(blob):
        return ErrorCategory.HARDWARE, "检测到硬件/磁盘相关错误提示", None

    if return_code == 127 or _CMD_NOT_FOUND.search(blob):
        missing = _extract_missing_command_name(blob)
        if missing:
            requires_pkg = _package_for_binary(missing)
        if re.search(
            r"\b(mvn|gradle|docker|kubectl|npm|pip3?|node|nginx)\b.*not found", blob_lc
        ):
            if missing and not requires_pkg:
                requires_pkg = _package_for_binary(missing)
            msg = (
                f"依赖或工具未安装（命令: {missing or 'unknown'}）"
                if missing
                else "依赖或工具未安装"
            )
            return ErrorCategory.COMMAND_NOT_FOUND_PKG_MISSING, msg, requires_pkg

        if missing and requires_pkg:
            return (
                ErrorCategory.COMMAND_NOT_FOUND_PKG_MISSING,
                f"命令未找到，推测对应软件包: {requires_pkg}",
                requires_pkg,
            )
        extra = f"（命令: {missing}）" if missing else ""
        return ErrorCategory.COMMAND_NOT_FOUND, f"命令未找到{extra}", requires_pkg

    if return_code == 126:
        return ErrorCategory.COMMAND_NOT_FOUND, "命令存在但不可执行（退出码 126）", None

    if _DISK_FULL.search(blob):
        return ErrorCategory.HARDWARE, "磁盘空间不足（可能需人工清理）", None

    if _SERVER_DOWN.search(blob) or (_NET_UNREACHABLE.search(blob) and re.search(r":\d{2,5}", blob)):
        return ErrorCategory.SERVER_UNAVAILABLE, "连接被拒绝或服务不可用（疑似宕机/未监听）", None

    if _NET_UNREACHABLE.search(blob):
        return (
            ErrorCategory.NETWORK_TIMEOUT_UNREACHABLE,
            "网络超时、不可达或解析失败",
            None,
        )

    if _NET_GENERIC.search(blob):
        return ErrorCategory.NETWORK, "网络相关错误", None

    if "permission denied" in blob_lc or "不允许的操作" in blob or "拒绝访问" in blob:
        p = path_hint
        if not p:
            pm = _PERM_PATH.search(blob)
            if pm:
                p = pm.group(1).strip()
        pr = "无写入权限或权限被拒绝"
        if p:
            pr = f"路径 {p} 无写入权限"
        if _is_sudo_style_permission(p, blob):
            return ErrorCategory.PERMISSION_DENIED_SUDO, pr + "（建议检查 sudo 或系统路径）", None
        return ErrorCategory.PERMISSION_DENIED, pr, None

    if _SYNTAX.search(blob):
        return ErrorCategory.SYNTAX, "脚本或命令语法错误", None

    if "no such file or directory" in blob_lc or "没有那个文件或目录" in blob:
        p = path_hint
        if not p:
            m = _NO_SUCH.search(blob)
            if m and m.lastindex:
                for i in range(1, m.lastindex + 1):
                    try:
                        g = m.group(i)
                        if g and ("/" in g or "\\" in g):
                            p = g.strip()
                            break
                    except IndexError:
                        break
        if _is_typo_suggested(blob):
            rs = "路径不存在或疑似拼写错误（存在纠错提示）"
            if p:
                rs = f"路径不存在/拼写疑似错误: {p}"
            return ErrorCategory.FILE_NOT_FOUND_PATH_TYPO, rs, None

        rs = "文件或目录不存在"
        if p:
            rs = f"路径不存在: {p}"
        return ErrorCategory.FILE_NOT_FOUND, rs, None

    if "cannot access" in blob_lc and path_hint:
        return ErrorCategory.PATH_ERROR, f"无法访问路径: {path_hint}", None

    if return_code != 0:
        return ErrorCategory.UNKNOWN, f"非零退出码 {return_code}", None

    return ErrorCategory.UNKNOWN, "未能归类错误（退出码为 0 或无匹配模式）", None


def assess_fixability(structured: StructuredError) -> Tuple[bool, str]:
    """
    第二步（规则部分）：是否「原则上」可走自动修正闭环。
    返回 (可自动修正, 说明)。
    """
    cat = structured.error_category
    if cat in (ErrorCategory.PERMISSION_DENIED, ErrorCategory.PERMISSION_DENIED_SUDO):
        return True, "权限不足：可通过 sudo / 调整目录或权限说明等修正"
    if cat in (
        ErrorCategory.FILE_NOT_FOUND,
        ErrorCategory.FILE_NOT_FOUND_PATH_TYPO,
        ErrorCategory.PATH_ERROR,
    ):
        return True, "路径类：可修正路径、拼写或创建目录"
    if cat == ErrorCategory.SYNTAX:
        return True, "语法类：可重写命令或脚本片段"
    if cat in (ErrorCategory.NETWORK, ErrorCategory.NETWORK_TIMEOUT_UNREACHABLE):
        return True, "网络类：可能调整地址/代理/重试（复杂场景需人工）"

    if cat == ErrorCategory.SERVER_UNAVAILABLE:
        return False, "服务器宕机或服务未监听：不可单靠命令闭环修正"
    if cat in (
        ErrorCategory.DEPENDENCY_MISSING,
        ErrorCategory.COMMAND_NOT_FOUND_PKG_MISSING,
    ):
        return False, "包/依赖未安装：需先安装软件包（通常超出单次命令闭环）"
    if cat == ErrorCategory.HARDWARE:
        return False, "硬件故障：需人工介入"
    if cat == ErrorCategory.COMMAND_NOT_FOUND:
        return False, "命令未找到：需安装软件或修正 PATH（通常为人工/包管理）"

    return False, "未知或未归类：不建议自动闭环"
