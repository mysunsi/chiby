"""Phase 2c：可选软删除重写（仅建议在有效环境启用，由调用方决定）。"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class SoftDeleteOutcome:
    rewritten: str
    applied: bool
    note: str


def _linux_rm_to_trash(original: str) -> SoftDeleteOutcome:
    """将行首简单 `rm ...` 重写为 trash-put（需远端已安装 trash-cli）。"""
    s = original.strip()
    m = re.match(r"^rm\s+(.+)$", s)
    if not m:
        return SoftDeleteOutcome(original, False, "非简单 rm 行，跳过")
    rest = m.group(1).strip()
    if not rest:
        return SoftDeleteOutcome(original, False, "无目标路径")
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return SoftDeleteOutcome(original, False, "无法解析 rm 参数")
    if "-rf" in tokens or ("-r" in tokens and "-f" in tokens) or "-r" in tokens or "-R" in tokens:
        pass
    else:
        return SoftDeleteOutcome(original, False, "非递归 rm，暂不重写")
    paths = [t for t in tokens if not t.startswith("-")]
    if not paths:
        return SoftDeleteOutcome(original, False, "无目标路径")
    rewritten = "trash-put " + " ".join(shlex.quote(p) for p in paths)
    return SoftDeleteOutcome(rewritten, True, "已重写为 trash-put（需 trash-cli）")


def soften_linux_command(original: str) -> SoftDeleteOutcome:
    if (os.environ.get("OPS_SOFT_DELETE_LINUX") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return SoftDeleteOutcome(original, False, "OPS_SOFT_DELETE_LINUX 未启用")
    return _linux_rm_to_trash(original)


def suggest_win_recycle(original: str) -> Tuple[str, str]:
    """
    Phase 2c：仅返回建议 Powershell（不自动执行）。
    Move-Item 到用户目录下回收占位目录。
    """
    o = original.strip()
    if not re.match(r"(?i)^(remove-item|del|rmdir|erase)\b", o):
        return original, ""
    ps = (
        "# 建议人工审核后执行：将目标移至用户目录回收占位\n"
        "$rb = Join-Path $env:USERPROFILE 'OpsRecycleBin';\n"
        "New-Item -ItemType Directory -Force -Path $rb | Out-Null;\n"
        "# Replace <TARGET>\nMove-Item -LiteralPath '<TARGET>' -Destination $rb\n"
    )
    return original, ps
