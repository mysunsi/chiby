"""DocHub — 企业文档向量检索（与 KnowledgeHub 双轨并行）。

KnowledgeHub：运维短经验（症状→根因→修复）。
DocHub：手册/规范等长文档（切片 + 向量 TopK）。
"""
from __future__ import annotations

from chibycore.doc_hub.api import router as doc_hub_router
from chibycore.doc_hub.search import DocHubSearch
from chibycore.doc_hub.storage import DocHubStorage

__all__ = [
    "DocHubStorage",
    "DocHubSearch",
    "doc_hub_router",
]
