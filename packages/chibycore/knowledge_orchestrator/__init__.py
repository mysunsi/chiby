"""统一知识检索调度：KnowledgeHub + DocHub → RRF 融合。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_RRF_K = 60


@dataclass
class KnowledgeSnippet:
    source_type: str
    title: str
    snippet: str
    full_id: str
    score: float = 0.0
    rank: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type,
            "title": self.title,
            "snippet": self.snippet,
            "full_id": self.full_id,
            "score": round(float(self.score), 4),
            "rank": int(self.rank),
            "meta": dict(self.meta or {}),
        }


def _rrf(lists: Sequence[Sequence[str]], *, k: int = _RRF_K) -> List[Tuple[str, float]]:
    scores: Dict[str, float] = {}
    for ranked in lists:
        for rank, item in enumerate(ranked, start=1):
            if not item:
                continue
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def search_knowledge(
    query: str,
    *,
    sources: Optional[Sequence[str]] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """并行检索 kb + doc，RRF 融合。"""
    t0 = time.time()
    q = (query or "").strip()
    if not q:
        return {
            "ok": False,
            "error_code": "query_required",
            "error": "缺少 query",
            "results": [],
        }
    src = [str(s).strip().lower() for s in (sources or ("kb", "doc")) if str(s).strip()]
    if not src:
        src = ["kb", "doc"]
    lim = max(1, min(int(limit or 5), 20))
    fetch = max(lim * 2, lim)

    by_id: Dict[str, KnowledgeSnippet] = {}
    ranks: List[List[str]] = []

    if "kb" in src:
        try:
            from chibycore.knowledge_hub.tool_api import run_kb_search

            kb = run_kb_search(q=q, mode="all", limit=fetch)
            ranked: List[str] = []
            if kb.get("ok"):
                for h in kb.get("results") or kb.get("hits") or []:
                    eid = str(h.get("entry_id") or h.get("id") or "")
                    et = str(h.get("entry_type") or h.get("type") or "kb")
                    if not eid:
                        continue
                    fid = f"kb:{et}:{eid}"
                    ranked.append(fid)
                    by_id[fid] = KnowledgeSnippet(
                        source_type="kb",
                        title=str(h.get("title") or eid),
                        snippet=str(h.get("snippet") or h.get("symptom") or "")[:420],
                        full_id=fid,
                        meta={"entry_id": eid, "entry_type": et},
                    )
            if ranked:
                ranks.append(ranked)
        except Exception as e:  # noqa: BLE001
            logger.warning("knowledge orch kb_search failed: %s", e)

    if "doc" in src:
        try:
            from chibycore.doc_hub.tool_api import run_doc_search

            doc = run_doc_search(q=q, limit=fetch)
            ranked = []
            if doc.get("ok"):
                for h in doc.get("hits") or []:
                    cid = str(h.get("chunk_id") or "")
                    if not cid:
                        continue
                    fid = f"doc:chunk:{cid}"
                    ranked.append(fid)
                    by_id[fid] = KnowledgeSnippet(
                        source_type="doc",
                        title=str(h.get("title") or cid),
                        snippet=str(h.get("snippet") or "")[:420],
                        full_id=fid,
                        meta={
                            "doc_id": h.get("doc_id"),
                            "chunk_id": cid,
                            "title_chain": h.get("title_chain"),
                        },
                    )
            if ranked:
                ranks.append(ranked)
        except Exception as e:  # noqa: BLE001
            logger.warning("knowledge orch doc_search failed: %s", e)

    fused = _rrf(ranks) if ranks else []
    results: List[Dict[str, Any]] = []
    for i, (fid, score) in enumerate(fused[:lim], start=1):
        sn = by_id.get(fid)
        if not sn:
            continue
        sn.score = score
        sn.rank = i
        results.append(sn.to_dict())

    return {
        "ok": True,
        "query": q,
        "sources": src,
        "total": len(results),
        "took_ms": int((time.time() - t0) * 1000),
        "results": results,
    }


def get_content(full_id: str) -> Dict[str, Any]:
    """按 full_id 路由到 kb_get / doc_get。"""
    fid = (full_id or "").strip()
    if not fid:
        return {"ok": False, "error_code": "id_required", "error": "缺少 full_id"}
    try:
        if fid.startswith("kb:"):
            parts = fid.split(":", 2)
            if len(parts) < 3:
                return {"ok": False, "error_code": "bad_id", "error": f"无效 full_id: {fid}"}
            _, entry_type, entry_id = parts[0], parts[1], parts[2]
            from chibycore.knowledge_hub.tool_api import run_kb_get

            data = run_kb_get(entry_id=entry_id, entry_type=entry_type)
            data = dict(data)
            data["full_id"] = fid
            data["source_type"] = "kb"
            return data
        if fid.startswith("doc:chunk:"):
            cid = fid[len("doc:chunk:") :]
            from chibycore.doc_hub.tool_api import run_doc_get

            data = run_doc_get(chunk_id=cid)
            data = dict(data)
            data["full_id"] = fid
            data["source_type"] = "doc"
            return data
        if fid.startswith("doc:doc:"):
            did = fid[len("doc:doc:") :]
            from chibycore.doc_hub.tool_api import run_doc_get

            data = run_doc_get(doc_id=did)
            data = dict(data)
            data["full_id"] = fid
            data["source_type"] = "doc"
            return data
    except Exception as e:  # noqa: BLE001
        logger.warning("get_content failed: %s", e)
        return {"ok": False, "error_code": "get_failed", "error": str(e)}
    return {"ok": False, "error_code": "bad_id", "error": f"无法识别 full_id: {fid}"}


class KnowledgeOrchestrator:
    """统一知识调度门面（开源核；无闭源依赖）。"""

    def search(
        self,
        query: str,
        *,
        sources: Optional[Sequence[str]] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        return search_knowledge(query, sources=sources, limit=limit)

    def get(self, full_id: str) -> Dict[str, Any]:
        return get_content(full_id)


__all__ = [
    "KnowledgeSnippet",
    "KnowledgeOrchestrator",
    "search_knowledge",
    "get_content",
]
