"""
Phase 7.1：轻量级修复（无 LLM）。

对常见模式返回单行可执行修正命令；调用方负责执行与策略校验。
"""
from __future__ import annotations

import re
from typing import Optional

from remediator.remediation.models import EnvironmentSnapshot, ErrorCategory, StructuredError

_DID_YOU_MEAN = re.compile(r"Did you mean\s+['`\"]?([^'\"`\n]+)['`]?", re.IGNORECASE)
_SUGGEST_PATH = re.compile(
    r"(?:suggest|提示)[:：]?\s*['\"]?((?:/|[A-Za-z]:\\)[^'\"\n]+)", re.IGNORECASE
)
_PKG_SAFE = re.compile(r"^[A-Za-z0-9._+/@-]+$")


def _extract_suggested_path(blob: str) -> Optional[str]:
    m = _DID_YOU_MEAN.search(blob)
    if m:
        return m.group(1).strip()
    m2 = _SUGGEST_PATH.search(blob)
    if m2:
        return m2.group(1).strip()
    return None


def _install_line(pkg: str, env: Optional[EnvironmentSnapshot]) -> str:
    """返回安装包命令（不执行）；按 os 名称粗略区分 apt / yum / zypper。"""
    safe = (pkg or "").strip()
    if not _PKG_SAFE.match(safe):
        return ""
    blob = f"{getattr(env, 'os_name', '')} {getattr(env, 'os_version', '')}".lower()
    if any(x in blob for x in ("rhel", "fedora", "centos", "rocky", "alma", "amazon")):
        return f"sudo yum install -y {safe}"
    if "suse" in blob or "sles" in blob:
        return f"sudo zypper install -y {safe}"
    return f"sudo apt-get install -y {safe}"


def try_lite_fix(
    error: StructuredError,
    *,
    env: Optional[EnvironmentSnapshot] = None,
) -> Optional[str]:
    """
    若命中规则则返回一条修正命令，否则 ``None``。

    ``error.metadata["command"]`` 应为原始命令（与 ``parse_execution_error`` 一致）。
    """
    orig = (error.metadata.get("command") or "").strip()
    cat = error.error_category

    if cat == ErrorCategory.PERMISSION_DENIED_SUDO:
        if not orig:
            return None
        if orig.startswith("sudo "):
            return orig
        return f"sudo {orig}"

    if cat == ErrorCategory.FILE_NOT_FOUND_PATH_TYPO:
        blob = (error.raw_stderr or "") + "\n" + (error.raw_stdout or "")
        blob_lc = blob.lower()
        if "did you mean" not in blob_lc and "是否想" not in blob_lc:
            if _SUGGEST_PATH.search(blob) is None:
                return None
        suggested = _extract_suggested_path(blob)
        if not suggested:
            return None
        wrong = (error.path or "").strip()
        if wrong and orig and wrong in orig:
            return orig.replace(wrong, suggested, 1)
        return None

    if cat == ErrorCategory.COMMAND_NOT_FOUND_PKG_MISSING:
        pkg = (error.requires_package or "").strip()
        if not pkg:
            return None
        line = _install_line(pkg, env)
        return line or None

    return None


__all__ = ["try_lite_fix"]
