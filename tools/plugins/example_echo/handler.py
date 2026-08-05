"""example_echo — Hello World 本地只读插件。"""
from __future__ import annotations

from typing import Any, Dict

_MAX_TEXT = 2_000


def truncate_text(s: str, max_len: int = _MAX_TEXT) -> str:
    t = (s or "").replace("\r", " ").strip()
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)] + "…"


def run(params: dict, context: dict) -> Dict[str, Any]:
    """本地只读：回显 text。不访问主机、不落盘、不调外网。"""
    _ = context
    text = params.get("text")
    if text is None:
        text = params.get("q") or params.get("message") or params.get("content") or ""
    body = truncate_text(str(text))
    if not body:
        return {
            "ok": False,
            "error_code": "text_required",
            "error": "缺少 text（要回显的字符串）",
        }
    return {
        "ok": True,
        "echo": body,
        "chars": len(body),
        "hint": "这是 Hello World 插件工具；见 docs/tool-plugin-architecture.md",
    }


def format_result(data: dict) -> str:
    if not data.get("ok"):
        return str(data.get("error") or "example_echo_failed")
    return f"echo ({data.get('chars', 0)} chars):\n{data.get('echo')}"
