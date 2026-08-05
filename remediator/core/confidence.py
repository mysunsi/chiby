"""
Phase 8.1：修复置信度（0~1）。

基础分 × (1 - 风险权重)；HIGH 权重 0.8 → KB 1.0 沦为 0.2。
"""
from __future__ import annotations

from typing import Literal

from remediator.core.risk_levels import RiskLevel, infer_risk_level

RemediationSource = Literal["kb", "lite", "llm"]

# 与 RiskLevel 对应的扣分权重（乘在 (1-w) 上）
RISK_WEIGHT: dict[RiskLevel, float] = {
    RiskLevel.LOW: 0.0,
    RiskLevel.MEDIUM: 0.35,
    RiskLevel.HIGH: 0.8,
}

BASE_CONFIDENCE: dict[str, float] = {
    "kb": 1.0,
    "lite": 0.9,
    "llm": 0.7,
}


def finalize_confidence(base: float, risk: RiskLevel) -> float:
    """最终置信度 = base × (1 - 风险权重)，裁剪到 [0,1]。"""
    w = RISK_WEIGHT.get(risk, 0.0)
    v = base * (1.0 - w)
    return max(0.0, min(1.0, float(v)))


def confidence_for_remediation_source(
    source: RemediationSource,
    command: str,
    risk_warning: str = "",
) -> float:
    """
    根据来源（KB / Lite / LLM）与修正命令 + 风险提示估计风险等级，再合成置信度。
    """
    b = BASE_CONFIDENCE.get(source, 0.5)
    r = infer_risk_level(command, risk_warning)
    return finalize_confidence(b, r)


__all__ = [
    "BASE_CONFIDENCE",
    "RISK_WEIGHT",
    "RemediationSource",
    "confidence_for_remediation_source",
    "finalize_confidence",
]
