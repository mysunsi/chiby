"""终端执行策略：拒绝列表（可扩展）。工业级 P0 — 与 LLM 危险模式互补，在网关层硬拦截。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from chibycore.os_risk_loader import DEFAULT_RULES_PATH, load_os_rules_file


def policy_enabled() -> bool:
    v = (os.environ.get("OPS_POLICY_ENABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


# 与 terminal.llm_shell 危险模式对齐并略增运维常见毁灭操作
_DEFAULT_DENY: List[str] = [
    r"\brm\s+-rf\s+/",
    r"\bdd\b.*\bof=/dev/[hs]d",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\bmkfs\.",
    r"\bformat\s+[a-z]:",
    r">\s*/dev/sd[a-z]",
    r"\bcurl\b[^|\n]*\|\s*bash",
    r"\bwget\b[^|\n]*\|\s*bash",
    r"\bInvoke-Expression\b.*\bIEX\b",
]


@dataclass
class PolicyResult:
    allowed: bool
    reason: str = ""
    #: 命中规则的归类（便于网关/API 打标签）
    rule_kind: str = ""
    #: 实际匹配到的正则模式串（可能较长，展示时已截断）
    matched_pattern: str = ""


class PolicyEngine:
    """可单测的策略引擎；启用时命中任一 deny 正则即拒绝。"""

    def __init__(self, extra_patterns: Optional[List[str]] = None):
        raw = list(_DEFAULT_DENY)
        extra = os.environ.get("OPS_POLICY_EXTRA_DENY", "")
        if extra.strip():
            for part in extra.split(","):
                p = part.strip()
                if p:
                    raw.append(p)
        rules_path = (os.environ.get("OPS_POLICY_OS_RULES_FILE") or "").strip()
        yaml_extra, _ = (
            load_os_rules_file(Path(rules_path)) if rules_path else load_os_rules_file(DEFAULT_RULES_PATH)
        )
        raw.extend(yaml_extra)
        if extra_patterns:
            raw.extend(extra_patterns)
        self._compiled: List[re.Pattern] = []
        for p in raw:
            try:
                self._compiled.append(re.compile(p, re.IGNORECASE | re.DOTALL))
            except re.error:
                continue

    def evaluate_line(self, line: str) -> PolicyResult:
        if not policy_enabled():
            return PolicyResult(True, "")
        if not line or not line.strip():
            return PolicyResult(True, "")
        for pat in self._compiled:
            if pat.search(line):
                return PolicyResult(
                    False,
                    f"策略拒绝：匹配规则 `{pat.pattern}`",
                    rule_kind="deny_regex",
                    matched_pattern=pat.pattern,
                )
        return PolicyResult(True, "")


_default_engine: Optional[PolicyEngine] = None


def get_policy_engine() -> PolicyEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = PolicyEngine()
    return _default_engine


def reset_policy_engine_for_tests() -> None:
    global _default_engine
    _default_engine = None
