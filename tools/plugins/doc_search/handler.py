"""doc_search 插件。"""
from __future__ import annotations
from typing import Any, Dict

def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.doc_tools import extract_doc_args, run_doc_search
    raw = {**(context.get("raw") or {}), **(params or {})}
    args = extract_doc_args(raw)
    return run_doc_search(
        q=str(args.get("q") or ""),
        limit=int(args.get("limit") or 8),
        strategy=str(raw.get("strategy") or "hybrid"),
    )

def format_result(data: dict) -> str:
    from terminal.mobile.doc_tools import format_doc_result_summary
    return format_doc_result_summary(data)
