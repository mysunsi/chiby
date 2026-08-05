"""知识统一调度（开源核入口）。

实现位于 ``chibycore.knowledge_orchestrator``；本模块提供稳定的 ``terminal.*`` 导入路径。
"""

from __future__ import annotations

from chibycore.knowledge_orchestrator import (
    KnowledgeOrchestrator,
    KnowledgeSnippet,
    get_content,
    search_knowledge,
)

__all__ = [
    "KnowledgeOrchestrator",
    "KnowledgeSnippet",
    "get_content",
    "search_knowledge",
]
