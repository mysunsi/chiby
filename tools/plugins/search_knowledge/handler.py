"""search_knowledge 插件。"""
from __future__ import annotations
from typing import Any, Dict

def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.orchestrator_tools import extract_orch_args, run_search_knowledge
    raw = {**(context.get("raw") or {}), **(params or {})}
    args = extract_orch_args(raw)
    return run_search_knowledge(q=str(args.get("q") or ""), limit=int(args.get("limit") or 5), sources=args.get("sources"))

def format_result(data: dict) -> str:
    from terminal.mobile.orchestrator_tools import format_orch_result_summary
    return format_orch_result_summary(data)
