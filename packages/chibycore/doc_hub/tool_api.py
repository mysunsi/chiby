"""DocHub 工具面 API（开源核）：供统一调度 / Agent 插件调用。

编排器与 Agent 应直接 import 本模块，勿经掌上包中转。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from chibycore.doc_hub.search import DocHubSearch
from chibycore.doc_hub.storage import DocHubStorage

logger = logging.getLogger(__name__)

_MAX_SNIPPET = 420
_MAX_GET_BODY = 12_000


def truncate_text(s: str, max_len: int) -> str:
    t = (s or "").replace("\r", " ").strip()
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)] + "…"


def _storage(storage: Optional[DocHubStorage] = None) -> DocHubStorage:
    return storage or DocHubStorage.get_instance()


def _bundle(
    storage: Optional[DocHubStorage] = None,
    search: Optional[DocHubSearch] = None,
) -> DocHubSearch:
    if search is not None:
        return search
    store = _storage(storage)
    return DocHubSearch(storage=store)


def run_doc_search(
    *,
    q: str,
    limit: int = 8,
    strategy: str = "hybrid",
    expand_context: bool = True,
    storage: Optional[DocHubStorage] = None,
    search: Optional[DocHubSearch] = None,
) -> Dict[str, Any]:
    query = (q or "").strip()
    if not query:
        return {
            "ok": False,
            "error_code": "query_required",
            "error": "缺少 q（检索关键词）",
        }
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 8
    lim = max(1, min(lim, 20))
    try:
        resp = _bundle(storage, search).search(
            query,
            limit=lim,
            strategy=strategy or "hybrid",
            expand_context=bool(expand_context),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("doc_search failed: %s", e)
        return {
            "ok": False,
            "error_code": "search_failed",
            "error": truncate_text(str(e), 400),
        }
    hits = []
    for h in resp.results:
        hits.append(
            {
                "doc_id": h.doc_id,
                "chunk_id": h.chunk_id,
                "title": truncate_text(h.title, 200),
                "title_chain": truncate_text(h.title_chain, 300),
                "snippet": truncate_text(h.snippet, _MAX_SNIPPET),
                "score": round(float(h.score), 4),
                "source_path": truncate_text(h.source_path, 300),
                "ordinal": h.ordinal,
            }
        )
    return {
        "ok": True,
        "query": query,
        "total": resp.total,
        "took_ms": resp.took_ms,
        "strategy": resp.strategy,
        "hits": hits,
    }


def run_doc_get(
    *,
    doc_id: str = "",
    chunk_id: str = "",
    storage: Optional[DocHubStorage] = None,
) -> Dict[str, Any]:
    store = _storage(storage)
    cid = (chunk_id or "").strip()
    did = (doc_id or "").strip()
    if cid:
        chunk = store.get_chunk(cid)
        if not chunk:
            return {
                "ok": False,
                "error_code": "not_found",
                "error": f"片段不存在: {cid}",
            }
        if did and chunk.doc_id != did:
            return {
                "ok": False,
                "error_code": "mismatch",
                "error": "doc_id 与 chunk_id 不匹配",
            }
        doc = store.get_document(chunk.doc_id)
        return {
            "ok": True,
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.id,
            "title": (doc.title if doc else chunk.title) or "",
            "ordinal": chunk.ordinal,
            "source_path": chunk.source_path,
            "text": truncate_text(chunk.text, _MAX_GET_BODY),
        }
    if not did:
        return {
            "ok": False,
            "error_code": "id_required",
            "error": "需要 doc_id 或 chunk_id",
        }
    doc = store.get_document(did)
    if not doc:
        return {
            "ok": False,
            "error_code": "not_found",
            "error": f"文档不存在: {did}",
        }
    chunks = store.list_chunks(did)
    return {
        "ok": True,
        "doc_id": doc.id,
        "title": doc.title,
        "status": doc.status.value,
        "source_path": doc.source_path,
        "chunk_count": doc.chunk_count,
        "chunks": [
            {
                "chunk_id": c.id,
                "ordinal": c.ordinal,
                "preview": truncate_text(c.text, 200),
            }
            for c in chunks[:40]
        ],
    }
