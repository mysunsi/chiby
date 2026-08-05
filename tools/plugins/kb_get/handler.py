"""kb_get 插件。"""
from __future__ import annotations
from typing import Any, Dict

def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.kb_tools import extract_kb_args, run_kb_get
    raw = {**(context.get("raw") or {}), **(params or {})}
    args = extract_kb_args(raw)
    return run_kb_get(entry_id=str(args.get("entry_id") or ""), entry_type=str(args.get("entry_type") or "kb"))

def format_result(data: dict) -> str:
    from terminal.mobile.kb_tools import format_kb_result_summary
    return format_kb_result_summary(data)
