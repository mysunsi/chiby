"""KnowledgeHub — AI运维助手知识中枢。

统一管理三大模块数据：
1. 故障经验（KnowledgeEntry）— 从 remediator/terminal 成功案例自动沉淀
2. 运维脚本（ScriptEntry）— 脚本存储、检索、版本管理
3. 最佳实践（BestPractice）— 标准化操作流程

导出核心类供外部使用。
"""
from __future__ import annotations

from .models import (
    KBEntry,
    KBCategory,
    KBConfidence,
    ScriptEntry,
    ScriptLanguage,
    ScriptRiskLevel,
    BestPractice,
    SearchQuery,
    SearchResult,
    IngestSource,
)
from .storage import KnowledgeHubStorage
from .search import KnowledgeHubSearch
from .api import router as knowledge_hub_router

__all__ = [
    "KBEntry",
    "KBCategory",
    "KBConfidence",
    "ScriptEntry",
    "ScriptLanguage",
    "ScriptRiskLevel",
    "BestPractice",
    "SearchQuery",
    "SearchResult",
    "IngestSource",
    "KnowledgeHubStorage",
    "KnowledgeHubSearch",
    "knowledge_hub_router",
]
