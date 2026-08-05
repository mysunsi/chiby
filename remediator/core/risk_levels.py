"""命令风险分级（供 executor_wrapper、置信度等复用）。"""
from __future__ import annotations

import re
from enum import Enum


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


_HIGH_CMD = re.compile(
    r"(?:^|\s)(?:sudo\s+)?rm\s+-rf\s+/|"
    r"\bdd\b.*\bof=/dev/|"
    r":\(\)\{\s*:\|:&\s*\};:|"
    r"\bshutdown\s+-h\s+now\b|"
    r"\bmkfs\b|"
    r">\s*/dev/sd",
    re.IGNORECASE | re.DOTALL,
)
_MEDIUM_CMD = re.compile(
    r"\bsudo\b|\brm\s+-|"
    r"\bkill\s+-9\b|"
    r"\b(systemctl|service)\s+\w+\s+(stop|disable)\b|"
    r"\bdocker\s+rm\s+-f\b",
    re.IGNORECASE,
)


def infer_risk_level(command: str, risk_warning: str = "") -> RiskLevel:
    blob = f"{command}\n{risk_warning or ''}"
    if _HIGH_CMD.search(blob) or _HIGH_CMD.search(command):
        return RiskLevel.HIGH
    if _MEDIUM_CMD.search(blob):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


__all__ = ["RiskLevel", "infer_risk_level"]
