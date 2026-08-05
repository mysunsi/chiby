"""终端与开源契约模型包。

- ``app``：原 ``terminal/models.py``（Host / Session / Closure API 等）
- ``session`` / ``exec`` / ``risk`` / ``audit`` / ``tools``：P0-2 共享契约锚点
"""

from __future__ import annotations

from chibyterm.models.app import *  # noqa: F403
from chibyterm.models.audit import AuditEntry, redact_payload_stub, safe_redact_payload
from chibyterm.models.exec import ExecResult, RiskLevel, RunOptions
from chibyterm.models.risk import (
    BLOCKED_MUTATE_RE,
    RISK_PATTERNS,
    RiskRule,
    ops_cmd_is_high_risk,
)
from chibyterm.models.session import ConversationState, PendingPermission
from chibyterm.models.tools import (
    DEFAULT_ALLOWED_TOOLS,
    FILE_READONLY_TOOLS,
    RemoteToolCall,
    ToolCall,
    ToolSchema,
)

__all__ = [
    # app re-exports filled dynamically below
    "PendingPermission",
    "ConversationState",
    "ExecResult",
    "RunOptions",
    "RiskLevel",
    "RiskRule",
    "RISK_PATTERNS",
    "BLOCKED_MUTATE_RE",
    "ops_cmd_is_high_risk",
    "AuditEntry",
    "redact_payload_stub",
    "safe_redact_payload",
    "DEFAULT_ALLOWED_TOOLS",
    "FILE_READONLY_TOOLS",
    "RemoteToolCall",
    "ToolCall",
    "ToolSchema",
]

# 合并 app 公开名，保持 ``from chibyterm.models import Host`` 可用
from chibyterm.models import app as _app

for _name in getattr(_app, "__all__", dir(_app)):
    if _name.startswith("_"):
        continue
    if _name not in __all__:
        __all__.append(_name)
