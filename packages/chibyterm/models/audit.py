"""审计条目基础字段与最小脱敏 stub（开源契约）。

完整脱敏实现仍在 ``chibycore.redaction``；本模块提供数据结构与无依赖兜底。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    """审计 JSONL 单行的基础字段。"""

    ts: str = ""
    event: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


def redact_payload_stub(
    obj: Any,
    *,
    max_str: int = 8000,
    _depth: int = 0,
) -> Any:
    """最小脱敏 stub：截断长字符串、遮蔽常见密钥键名。

    生产路径优先 ``chibycore.redaction.redact_payload``；本函数供无 chibycore 时降级。
    """
    if _depth > 8:
        return "<max-depth>"
    if isinstance(obj, str):
        s = obj if len(obj) <= max_str else obj[:max_str] + "…(truncated)"
        return s
    if isinstance(obj, list):
        return [redact_payload_stub(x, max_str=max_str, _depth=_depth + 1) for x in obj[:64]]
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in list(obj.items())[:128]:
            key = str(k)
            kl = key.lower()
            if any(
                x in kl
                for x in (
                    "password",
                    "passwd",
                    "secret",
                    "token",
                    "api_key",
                    "private_key",
                    "credential",
                )
            ):
                out[key] = "***"
            else:
                out[key] = redact_payload_stub(v, max_str=max_str, _depth=_depth + 1)
        return out
    return obj


def safe_redact_payload(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """优先 chibycore 脱敏，失败则 stub。"""
    raw = payload or {}
    try:
        from chibycore.redaction import redact_payload

        safe = redact_payload(raw)
        return safe if isinstance(safe, dict) else {"_": safe}
    except Exception:
        safe = redact_payload_stub(raw)
        return safe if isinstance(safe, dict) else {"_": safe}
