"""Healing — 自适应自愈引擎。

架构：
  ┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
  │  失败信号    │ ──→ │  HealingEngine   │ ──→ │  修复执行     │
  │ (命令+stderr)│     │  [编排器]         │     │  (execute)   │
  └─────────────┘     ├──────────────────┤     └──────┬───────┘
                      │  Knowledge       │            │
                      │  Retriever       │            ▼
                      │  [统一检索]       │     ┌──────────────┐
                      ├──────────────────┤     │  验证 & 归档  │
                      │  Confidence      │     │  (archive)   │
                      │  [评分器]         │     └──────────────┘
                      └──────────────────┘

工作流：
1. 收到失败信号（命令 + stderr/stdout）
2. KnowledgeRetriever 从 knowledge_hub + remediation_kb 检索
3. Confidence 对每个命中的结果评分（0.0~1.0）
4. 高置信度（≥0.7）→ 直接采用历史修复方案
5. 中置信度（0.4~0.7）→ 验证后采用
6. 低置信度（<0.4）→ 退回到 LLM 生成修复方案
7. 修复执行 → 验证成功 → 自动归档到 KnowledgeHub
"""

from __future__ import annotations

from .confidence import HealingConfidence, score_confidence
from .knowledge_retriever import (
    RetrievedKnowledge,
    HealingKnowledgeRetriever,
)
from .engine import (
    HealingEngine,
    HealingResult,
    HealDecision,
)

__all__ = [
    "HealingEngine",
    "HealingResult",
    "HealDecision",
    "HealingConfidence",
    "score_confidence",
    "HealingKnowledgeRetriever",
    "RetrievedKnowledge",
]
