"""加载 OS 高危规则 YAML（扩展 deny 模式 + 关键词风险标签）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


DEFAULT_RULES_PATH = Path(__file__).resolve().parent / "rules" / "os_critical_patterns.yaml"


def _parse_yaml(text: str) -> Dict[str, Any]:
    if not yaml:
        return {}
    data = yaml.safe_load(text) or {}
    return data if isinstance(data, dict) else {}


def load_os_rules_file(path: Optional[Path] = None) -> Tuple[List[str], Dict[str, List[str]]]:
    """
    Returns:
        extra_deny_patterns: 追加到策略引擎的正则列表
        risk_keywords: { level -> [substring hints] } 供启发式 risk 标签
    """
    p = Path(path) if path else DEFAULT_RULES_PATH
    if not p.is_file():
        return [], {}
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return [], {}
    data = _parse_yaml(raw)
    patterns = data.get("extra_deny_patterns") or []
    if not isinstance(patterns, list):
        patterns = []
    patterns = [str(x).strip() for x in patterns if str(x).strip()]

    rk = data.get("risk_keywords") or {}
    risk_keywords: Dict[str, List[str]] = {}
    if isinstance(rk, dict):
        for k, v in rk.items():
            if isinstance(v, list):
                risk_keywords[str(k)] = [str(x) for x in v if str(x).strip()]
    return patterns, risk_keywords
