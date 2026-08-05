"""host_list — 列出当前用户 ACL 可见主机（本地只读插件）。

主机列表由运行时经 context['list_visible_hosts'] 注入，handler 不读凭据。
"""
from __future__ import annotations

from typing import Any, Dict, List


def run(params: dict, context: dict) -> Dict[str, Any]:
    _ = params
    list_fn = (context or {}).get("list_visible_hosts")
    rows: List[Dict[str, Any]] = []
    if callable(list_fn):
        try:
            raw = list_fn()
            if isinstance(raw, list):
                rows = [r for r in raw if isinstance(r, dict)]
        except Exception as e:
            return {
                "ok": False,
                "error_code": "host_list_failed",
                "error": str(e),
            }
    return {
        "ok": True,
        "hosts": rows,
        "count": len(rows),
    }


def format_result(data: dict) -> str:
    if not data.get("ok"):
        return str(data.get("error") or "host_list_failed")
    n = int(data.get("count") or 0)
    return f"可见主机 {n} 台"
