"""Healing — 匹配可信度评分器。

评分因素与权重：
  1. 精确指纹命中（exact_fingerprint）         +0.40
  2. 类别+包/服务名匹配（category_package）      +0.25
  3. 命令编辑距离（levenshtein_ratio）          ×0.30
  4. stderr 文本余弦相似度（cosine_sim）        ×0.25
  5. 来源置信度（source_weight）               ×0~0.20
  6. 历史成功次数（success_count）             ×0~0.15
  7. 知识库自有置信度字段（kb_confidence）       ×0~0.15

综合公式：base + Σ(factor × weight)，clamp 到 0.0~1.0。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class MatchLevel(str, Enum):
    """匹配层级（从精确到模糊）。"""
    EXACT_FINGERPRINT = "exact_fingerprint"       # 指纹精确命中
    CATEGORY_PACKAGE = "category_package"          # 类别+包名匹配
    HIGH_SIMILARITY = "high_similarity"            # 高文本相似度（>0.6）
    MEDIUM_SIMILARITY = "medium_similarity"        # 中等文本相似度（0.35~0.6）
    LOW_SIMILARITY = "low_similarity"              # 低文本相似度（<0.35）
    TAG_CONTEXT = "tag_context"                    # 仅标签/上下文匹配


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class HealingConfidence:
    """单条检索结果的置信度评分。"""
    score: float                          # 0.0 ~ 1.0
    level: ConfidenceLevel                # high / medium / low
    match_level: MatchLevel               # 匹配层级
    reason: str = ""                      # 评分原因描述
    details: Dict[str, float] = field(default_factory=dict)  # 各维度得分明细


# ── 文本相似度工具 ────────────────────────────────────────────────────────


def _token_freq(text: str) -> Dict[str, float]:
    """轻量词袋，支持中英文混排。"""
    toks = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_/.\\-:@]{2,}", (text or "").lower())
    d: Dict[str, float] = {}
    for t in toks:
        d[t] = d.get(t, 0.0) + 1.0
    return d


def _cosine_sim(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


def _levenshtein_ratio(a: str, b: str) -> float:
    x, y = (a or "").strip(), (b or "").strip()
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    n, m = len(x), len(y)
    if n > m:
        x, y, n, m = y, x, m, n
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if x[i - 1] == y[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    dist = dp[n][m]
    return 1.0 - dist / max(n, m)


# ── 来源权重 ─────────────────────────────────────────────────────────────


_SOURCE_WEIGHTS = {
    "remediator": 0.20,           # remediator 成功案例，结构化程度最高
    "remediator_success": 0.20,
    "terminal_session": 0.15,     # 终端闭环成功
    "manual": 0.12,               # 手动录入
    "import": 0.08,               # 批量导入
    "unknown": 0.05,
}


def _source_weight(source: str) -> float:
    return _SOURCE_WEIGHTS.get(source.lower().strip(), 0.05)


def _confidence_field_weight(conf: str) -> float:
    """KBEntry.confidence 字段映射。"""
    m = {"high": 0.15, "medium": 0.08, "low": 0.02}
    return m.get(conf.lower().strip(), 0.05)


def _success_count_weight(count: int) -> float:
    """历史成功次数带来的额外置信度。"""
    if count >= 10:
        return 0.15
    if count >= 5:
        return 0.12
    if count >= 3:
        return 0.10
    if count >= 1:
        return 0.05
    return 0.0


# ── 评分主函数 ───────────────────────────────────────────────────────────


def score_confidence(
    *,
    query_command: str,
    query_stderr: str,
    # 匹配条目信息
    entry_command: Optional[str] = None,         # 原始命令
    entry_remediation: Optional[str] = None,     # 修复方案
    entry_fingerprint: Optional[str] = None,     # 指纹
    entry_category: Optional[str] = None,        # 类别
    entry_source: Optional[str] = None,          # 来源（knowledge_hub.source）
    entry_confidence_field: Optional[str] = None,  # KBConfidence 字段
    entry_success_count: int = 0,
    entry_tags: Optional[List[str]] = None,
    entry_symptom: Optional[str] = None,         # 症状（用于文本匹配）
    # 精确匹配标记
    exact_fingerprint_match: bool = False,
    category_package_match: bool = False,
) -> HealingConfidence:
    """
    综合评分一条检索结果。
    返回 HealingConfidence，包含 score / level / match_level / reason / details。
    """
    details: Dict[str, float] = {}
    base = 0.0

    # 1. 精确指纹命中
    if exact_fingerprint_match:
        details["exact_fingerprint"] = 0.40
        base += 0.40

    # 2. 类别+包名匹配
    if category_package_match:
        details["category_package"] = 0.25
        base += 0.25

    # 3. 命令编辑距离
    cmd_a = (query_command or "").strip().split(maxsplit=1)
    cmd_b = (entry_command or entry_remediation or "").strip().split(maxsplit=1)
    cmd_sim = _levenshtein_ratio(
        cmd_a[0] if cmd_a else "",
        cmd_b[0] if cmd_b else "",
    )
    cmd_score = cmd_sim * 0.30
    details["command_similarity"] = round(cmd_score, 4)
    base += cmd_score

    # 如果没有 fingerprint 匹配且有较高的命令相似度，提升 match level 判断
    if not exact_fingerprint_match and not category_package_match:
        if cmd_sim >= 0.6:
            cat_sim = _levenshtein_ratio(
                (query_command or "").strip(),
                (entry_command or "").strip(),
            )
            if cat_sim >= 0.6:
                details["high_cmd_sim_boost"] = 0.10
                base += 0.10

    # 4. stderr 文本余弦相似度
    symptom_text = entry_symptom or entry_command or ""
    stderr_sim = _cosine_sim(
        _token_freq(query_stderr or ""),
        _token_freq(symptom_text),
    )
    stderr_score = stderr_sim * 0.25
    details["stderr_similarity"] = round(stderr_score, 4)
    base += stderr_score

    # 5. 来源权重
    sw = _source_weight(entry_source or "")
    details["source_weight"] = round(sw, 4)
    base += sw

    # 6. 历史成功次数
    scw = _success_count_weight(entry_success_count)
    if scw > 0:
        details["success_count"] = round(scw, 4)
        base += scw

    # 7. 知识库自有置信度字段
    cfw = _confidence_field_weight(entry_confidence_field or "")
    if cfw > 0:
        details["kb_confidence"] = round(cfw, 4)
        base += cfw

    # 综合得分（clamp）
    final_score = max(0.0, min(1.0, base))

    # 判断层级
    if exact_fingerprint_match or final_score >= 0.7:
        level = ConfidenceLevel.HIGH
        match_lvl = MatchLevel.EXACT_FINGERPRINT if exact_fingerprint_match else MatchLevel.HIGH_SIMILARITY
    elif final_score >= 0.4:
        level = ConfidenceLevel.MEDIUM
        match_lvl = (
            MatchLevel.CATEGORY_PACKAGE if category_package_match
            else MatchLevel.MEDIUM_SIMILARITY
        )
    elif final_score >= 0.2:
        level = ConfidenceLevel.LOW
        match_lvl = MatchLevel.LOW_SIMILARITY
    else:
        level = ConfidenceLevel.LOW
        match_lvl = MatchLevel.TAG_CONTEXT

    # 生成可读原因
    parts = []
    for k, v in sorted(details.items(), key=lambda x: -x[1]):
        if v >= 0.01:
            label = k.replace("_", " ")
            parts.append(f"{label}={v:.2f}")
    reason = f"score={final_score:.2f} level={level.value} ({', '.join(parts)})"

    return HealingConfidence(
        score=round(final_score, 4),
        level=level,
        match_level=match_lvl,
        reason=reason,
        details=details,
    )
