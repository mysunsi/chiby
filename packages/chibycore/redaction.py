"""审计与日志中的敏感字段脱敏。"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# 常见 key=value / key: value 形式的口令片段
_PW_PAIR = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|apikey|api_key|access_key|"
    r"private_key_passphrase|passphrase)\b\s*[:=]\s*([^\s;\"']+)",
)
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s]+")
_BASIC_AUTH = re.compile(r"(?i)\b(Authorization:\s*Basic)\s+[A-Za-z0-9+/=]+")
_PEM = re.compile(
    r"-----BEGIN[^-]+PRIVATE KEY-----[\s\S]*?-----END[^-]+PRIVATE KEY-----",
    re.I,
)
# SSH / 云常见内联私钥片段（短窗）
_AWS_KEY = re.compile(r"(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])")
# mysql --password= / -p'secret'
_MYSQL_P = re.compile(r"(?i)(\s-p|--password=)([^\s]+)")

_AUDIT_KEYS = frozenset(
    {
        "command",
        "commands",
        "text",
        "preview",
        "detail",
        "stdout",
        "stderr",
        "error",
        "summary",
        "preface",
        "data",
        "body",
        "message",
        "prompt",
        "assistant_text",
        "last_turn_summary",
        "command_preview",
        "rollback_preview",
    }
)


def redact_command_text(text: str, max_len: int = 8000) -> str:
    """截断并脱敏单行/多行命令或输出文本，用于审计/回灌。"""
    if text is None:
        return ""
    s = text if len(text) <= max_len else text[:max_len] + "…(truncated)"
    s = _PEM.sub("-----BEGIN PRIVATE KEY-----\n***\n-----END PRIVATE KEY-----", s)
    s = _PW_PAIR.sub(lambda m: f"{m.group(1)}=***", s)
    s = _BEARER.sub("Bearer ***", s)
    s = _BASIC_AUTH.sub(r"\1 ***", s)
    s = _AWS_KEY.sub("AKIA***", s)
    s = _MYSQL_P.sub(r"\1***", s)
    return s


def redact_host_hint(host_id: Optional[str], host: Optional[str]) -> str:
    """仅保留 host_id；IP/域名不落审计或按需扩展。"""
    if host_id:
        return f"host_id={host_id}"
    if host:
        return "host=(redacted)"
    return ""


def redact_payload(
    obj: Any,
    *,
    max_str: int = 8000,
    _depth: int = 0,
) -> Any:
    """递归脱敏 dict/list 中的敏感字符串字段（审计 payload 用）。"""
    if _depth > 8:
        return obj
    if isinstance(obj, str):
        return redact_command_text(obj, max_len=max_str)
    if isinstance(obj, list):
        return [redact_payload(x, max_str=max_str, _depth=_depth + 1) for x in obj[:64]]
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in list(obj.items())[:64]:
            key = str(k)
            kl = key.lower()
            if kl in ("password", "passwd", "pwd", "secret", "token", "api_key"):
                out[key] = "***" if v else v
            elif key in _AUDIT_KEYS or kl in _AUDIT_KEYS:
                out[key] = redact_payload(v, max_str=max_str, _depth=_depth + 1)
            elif isinstance(v, (dict, list)):
                out[key] = redact_payload(v, max_str=max_str, _depth=_depth + 1)
            elif isinstance(v, str) and (
                "password=" in v.lower()
                or "bearer " in v.lower()
                or "BEGIN " in v
                and "PRIVATE KEY" in v
            ):
                out[key] = redact_command_text(v, max_len=max_str)
            else:
                out[key] = v
        return out
    return obj
