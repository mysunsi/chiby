"""基于关键词配置的风险标签（不改变网关放行结果，仅供闭环/UI）。"""
from __future__ import annotations

from typing import Dict, List

from chibycore.executor_contract import RiskLevel
from chibycore.os_risk_loader import load_os_rules_file


_cached_keywords: Dict[str, List[str]] | None = None


def risk_keywords(use_default_yaml: bool = True) -> Dict[str, List[str]]:
    global _cached_keywords
    if _cached_keywords is not None:
        return _cached_keywords
    _, rk = load_os_rules_file(None)
    _cached_keywords = rk if use_default_yaml else {}
    return _cached_keywords


def reset_risk_keyword_cache_for_tests() -> None:
    global _cached_keywords
    _cached_keywords = None


def heuristic_risk_level(command_line: str, keywords: Dict[str, List[str]] | None = None) -> RiskLevel:
    if not command_line.strip():
        return RiskLevel.LOW
    cmp = keywords if keywords is not None else risk_keywords()
    ln = command_line
    ln_lower = ln.lower()
    for frag in cmp.get("critical", []):
        if frag.lower() in ln_lower:
            return RiskLevel.CRITICAL
    for frag in cmp.get("high", []):
        if frag.lower() in ln_lower:
            return RiskLevel.HIGH
    return RiskLevel.LOW
