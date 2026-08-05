"""kb_ingest 插件。"""
from __future__ import annotations
from typing import Any, Dict

def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.kb_tools import extract_kb_args, run_kb_ingest
    raw = {**(context.get("raw") or {}), **(params or {})}
    args = extract_kb_args(raw)
    return run_kb_ingest(
        title=str(args.get("title") or ""),
        symptom=str(args.get("symptom") or ""),
        root_cause=str(args.get("root_cause") or ""),
        remediation=str(args.get("remediation") or ""),
        verify_method=str(args.get("verify_method") or ""),
        category=str(args.get("category") or "other"),
        tags=list(args.get("tags") or []),
        applicable_os=list(args.get("applicable_os") or []),
        applicable_service=str(args.get("applicable_service") or ""),
        notes=str(args.get("notes") or ""),
        agent_mode=str((context or {}).get("agent_mode") or "omnipotent"),
    )

def format_result(data: dict) -> str:
    from terminal.mobile.kb_tools import format_kb_result_summary
    return format_kb_result_summary(data)
