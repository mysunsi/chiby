"""doc_get 插件。"""
from __future__ import annotations
from typing import Any, Dict

def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.doc_tools import extract_doc_args, run_doc_get
    raw = {**(context.get("raw") or {}), **(params or {})}
    args = extract_doc_args(raw)
    return run_doc_get(doc_id=str(args.get("doc_id") or ""), chunk_id=str(args.get("chunk_id") or ""))

def format_result(data: dict) -> str:
    from terminal.mobile.doc_tools import format_doc_result_summary
    return format_doc_result_summary(data)
