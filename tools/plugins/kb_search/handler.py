"""kb_search 插件 — 委托 terminal.mobile.kb_tools。"""
from __future__ import annotations
from typing import Any, Dict

def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.kb_tools import extract_kb_args, format_kb_result_summary, run_kb_search
    raw = {**(context.get("raw") or {}), **(params or {})}
    args = extract_kb_args(raw)
    return run_kb_search(q=args.get("q") or "", mode=str(args.get("mode") or "kb"), limit=int(args.get("limit") or 8))

def format_result(data: dict) -> str:
    from terminal.mobile.kb_tools import format_kb_result_summary
    return format_kb_result_summary(data)
