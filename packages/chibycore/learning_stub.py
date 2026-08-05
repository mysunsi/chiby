"""Phase 4：从审计/闭环日志挖掘规则建议（占位骨架，离线任务可调）。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


def summarize_audit_denies(audit_jsonl_path: Path, limit: int = 5000) -> Dict[str, Any]:
    """
    读取 execution_gateway deny 审计行，计数 top reason（运营报表用）。
    """
    counts: Counter[str] = Counter()
    if not audit_jsonl_path.is_file():
        return {"sample": 0, "top_reasons": []}
    n = 0
    try:
        with audit_jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                if n >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("event") == "execution_gateway" and obj.get("decision") == "deny":
                    counts[str(obj.get("reason") or "unknown")] += 1
                    n += 1
    except OSError:
        return {"sample": 0, "top_reasons": []}
    top = [{"reason": r, "count": c} for r, c in counts.most_common(20)]
    return {"sample": sum(counts.values()), "top_reasons": top}


def propose_rule_hints(summary: Dict[str, Any]) -> List[str]:
    """占位：未来将 summary 转成 YAML 增补建议（需人工 PR）。"""
    hints = []
    for row in summary.get("top_reasons") or []:
        if row["count"] >= 3:
            hints.append(f"Review frequent deny: {row['reason'][:120]}…")
    return hints
