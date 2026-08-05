"""get_content 插件。"""
from __future__ import annotations
from typing import Any, Dict

def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.orchestrator_tools import extract_orch_args, run_get_content
    raw = {**(context.get("raw") or {}), **(params or {})}
    args = extract_orch_args(raw)
    return run_get_content(full_id=str(args.get("full_id") or ""))

def format_result(data: dict) -> str:
    from terminal.mobile.orchestrator_tools import format_orch_result_summary
    return format_orch_result_summary(data)
