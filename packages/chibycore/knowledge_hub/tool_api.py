"""KnowledgeHub 工具面 API（开源核）：供统一调度 / Agent 插件调用。

编排器与 Agent 应直接 import 本模块，勿经掌上包中转。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from chibycore.knowledge_hub.models import SearchQuery
from chibycore.knowledge_hub.search import KnowledgeHubSearch
from chibycore.knowledge_hub.storage import KnowledgeHubStorage

logger = logging.getLogger(__name__)

_MAX_SNIPPET = 420
_MAX_FIELD = 4_000
_MAX_GET_BODY = 12_000


def truncate_text(s: str, max_len: int) -> str:
    t = (s or "").replace("\r", " ").strip()
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)] + "…"


def _storage(storage: Optional[KnowledgeHubStorage] = None) -> KnowledgeHubStorage:
    return storage or KnowledgeHubStorage.get_instance()


def run_kb_search(
    *,
    q: str,
    mode: str = "kb",
    limit: int = 8,
    storage: Optional[KnowledgeHubStorage] = None,
) -> Dict[str, Any]:
    """检索本地知识库 / 脚本库。"""
    query = (q or "").strip()
    if not query:
        return {
            "ok": False,
            "error_code": "query_required",
            "error": "缺少 q（检索关键词）",
        }
    m = (mode or "kb").strip().lower()
    if m not in ("kb", "script", "best_practice", "all"):
        m = "kb"
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 8
    lim = max(1, min(lim, 20))

    try:
        search = KnowledgeHubSearch(_storage(storage))
        resp = search.search(SearchQuery(q=query, mode=m, limit=lim))  # type: ignore[arg-type]
    except Exception as e:
        logger.warning("kb_search failed: %s", e)
        return {
            "ok": False,
            "error_code": "search_failed",
            "error": truncate_text(str(e), 400),
        }

    hits: List[Dict[str, Any]] = []
    for r in resp.results:
        hits.append(
            {
                "entry_type": r.entry_type,
                "entry_id": r.entry_id,
                "title": truncate_text(r.title, 200),
                "snippet": truncate_text(r.snippet, _MAX_SNIPPET),
                "score": round(float(r.score), 3),
                "category": r.category,
                "tags": list(r.tags or [])[:12],
                "confidence": r.confidence,
                "risk_level": r.risk_level,
            }
        )
    return {
        "ok": True,
        "query": resp.query,
        "mode": resp.mode,
        "total": int(resp.total),
        "took_ms": int(resp.took_ms),
        "results": hits,
    }


def run_kb_get(
    *,
    entry_id: str,
    entry_type: str = "kb",
    storage: Optional[KnowledgeHubStorage] = None,
) -> Dict[str, Any]:
    """按 id 取条目全文（截断保护）。"""
    eid = (entry_id or "").strip()
    if not eid:
        return {
            "ok": False,
            "error_code": "entry_id_required",
            "error": "缺少 entry_id",
        }
    et = (entry_type or "kb").strip().lower()
    if et in ("auto", "", "any"):
        et = "kb"
    store = _storage(storage)

    try:
        if et == "script":
            ent = store.get_script_entry(eid)
            if ent is None:
                return {
                    "ok": False,
                    "error_code": "not_found",
                    "error": f"脚本不存在: {eid}",
                }
            return {
                "ok": True,
                "entry_type": "script",
                "entry_id": ent.id,
                "name": truncate_text(ent.name, 200),
                "description": truncate_text(ent.description, _MAX_FIELD),
                "content": truncate_text(ent.content, _MAX_GET_BODY),
                "language": getattr(ent.language, "value", str(ent.language)),
                "risk_level": getattr(ent.risk_level, "value", str(ent.risk_level)),
                "tags": list(ent.tags or [])[:20],
                "related_kb_ids": list(ent.related_kb_ids or [])[:20],
            }

        if et == "best_practice":
            ent = store.get_best_practice(eid)
            if ent is None:
                return {
                    "ok": False,
                    "error_code": "not_found",
                    "error": f"最佳实践不存在: {eid}",
                }
            return {
                "ok": True,
                "entry_type": "best_practice",
                "entry_id": ent.id,
                "title": truncate_text(ent.title, 200),
                "description": truncate_text(ent.description, _MAX_FIELD),
                "steps": truncate_text(ent.steps, _MAX_GET_BODY),
                "tags": list(ent.tags or [])[:20],
            }

        ent = store.get_kb_entry(eid)
        if ent is None and (entry_type or "").strip().lower() in ("", "kb", "auto", "any"):
            script = store.get_script_entry(eid)
            if script is not None:
                return run_kb_get(entry_id=eid, entry_type="script", storage=store)
        if ent is None:
            return {
                "ok": False,
                "error_code": "not_found",
                "error": f"知识条目不存在: {eid}",
            }
        return {
            "ok": True,
            "entry_type": "kb",
            "entry_id": ent.id,
            "title": truncate_text(ent.title, 200),
            "category": getattr(ent.category, "value", str(ent.category)),
            "symptom": truncate_text(ent.symptom, _MAX_FIELD),
            "root_cause": truncate_text(ent.root_cause, _MAX_FIELD),
            "remediation": truncate_text(ent.remediation, _MAX_GET_BODY),
            "verify_method": truncate_text(ent.verify_method or "", _MAX_FIELD),
            "tags": list(ent.tags or [])[:20],
            "confidence": getattr(ent.confidence, "value", str(ent.confidence)),
            "applicable_os": list(ent.applicable_os or [])[:12],
            "applicable_service": ent.applicable_service,
            "notes": truncate_text(ent.notes or "", _MAX_FIELD),
        }
    except Exception as e:
        logger.warning("kb_get failed: %s", e)
        return {
            "ok": False,
            "error_code": "get_failed",
            "error": truncate_text(str(e), 400),
        }
