"""DocHub 语义检索：向量 / 关键词 / 混合（RRF）+ 上下文扩展。"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from chibycore.doc_hub.embeddings import Embedder, embed_texts, get_embedder
from chibycore.doc_hub.models import SearchHit, SearchResponse
from chibycore.doc_hub.storage import DocHubStorage
from chibycore.doc_hub.vector_store import VectorStore, open_vector_store

logger = logging.getLogger(__name__)

# 运维语料短、重复少：默认 30 比经典 60 更利精确术语；可用 rrf_k 覆盖
_RRF_K = 30


def rrf_fuse(
    ranked_lists: List[List[str]],
    *,
    k: int = _RRF_K,
) -> List[Tuple[str, float]]:
    """倒数排名融合：返回 [(id, score), ...] 降序。"""
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            if not item_id:
                continue
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class DocHubSearch:
    def __init__(
        self,
        storage: Optional[DocHubStorage] = None,
        vector_store: Optional[VectorStore] = None,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self.storage = storage or DocHubStorage.get_instance()
        self.vectors = vector_store or open_vector_store(self.storage.chroma_dir)
        self.embedder = embedder or get_embedder()

    def search(
        self,
        q: str,
        *,
        limit: int = 8,
        strategy: str = "hybrid",
        expand_context: bool = True,
        rrf_k: Optional[int] = None,
        debug: bool = False,
    ) -> SearchResponse:
        t0 = time.time()
        query = (q or "").strip()
        if not query:
            return SearchResponse(query="", total=0, results=[], took_ms=0, strategy=strategy)
        lim = max(1, min(int(limit or 8), 20))
        strat = (strategy or "hybrid").strip().lower()
        if strat not in ("hybrid", "vector", "keyword"):
            strat = "hybrid"
        k_rrf = int(rrf_k) if rrf_k is not None else _RRF_K
        k_rrf = max(1, min(k_rrf, 200))

        fetch_n = max(lim * 2, lim)
        vector_hits: List[Dict] = []
        keyword_chunks = []

        if strat in ("hybrid", "vector"):
            try:
                q_emb = embed_texts([query], self.embedder)[0]
                vector_hits = self.vectors.query(embedding=q_emb, n_results=fetch_n)
            except Exception as e:  # noqa: BLE001
                logger.warning("doc_hub vector search failed: %s", e)
                vector_hits = []

        if strat in ("hybrid", "keyword"):
            try:
                keyword_chunks = self.storage.keyword_search(query, limit=fetch_n)
            except Exception as e:  # noqa: BLE001
                logger.warning("doc_hub keyword search failed: %s", e)
                keyword_chunks = []

        # 构建 id → 素材
        by_id: Dict[str, Dict] = {}
        vec_rank: List[str] = []
        vec_scores: Dict[str, float] = {}
        for row in vector_hits:
            meta = row.get("metadata") or {}
            cid = str(row.get("id") or meta.get("chunk_id") or "")
            if not cid:
                continue
            vec_rank.append(cid)
            vs = float(row.get("score") or 0.0)
            vec_scores[cid] = vs
            by_id[cid] = {
                "doc_id": str(meta.get("doc_id") or ""),
                "chunk_id": cid,
                "title": str(meta.get("title") or ""),
                "source_path": str(meta.get("source_path") or ""),
                "text": str(row.get("document") or ""),
                "ordinal": int(meta.get("ordinal") or 0),
                "title_chain": str(meta.get("title_chain") or ""),
                "structure_quality": str(meta.get("structure_quality") or ""),
                "vec_score": vs,
            }

        kw_rank: List[str] = []
        for ch in keyword_chunks:
            kw_rank.append(ch.id)
            if ch.id not in by_id:
                by_id[ch.id] = {
                    "doc_id": ch.doc_id,
                    "chunk_id": ch.id,
                    "title": ch.title,
                    "source_path": ch.source_path,
                    "text": ch.text,
                    "ordinal": ch.ordinal,
                    "title_chain": ch.title_chain,
                    "structure_quality": "",
                    "vec_score": 0.0,
                }
            else:
                if not by_id[ch.id].get("title_chain"):
                    by_id[ch.id]["title_chain"] = ch.title_chain
                if not by_id[ch.id].get("text"):
                    by_id[ch.id]["text"] = ch.text

        rrf_scores: Dict[str, float] = {}
        if strat == "vector":
            ordered = [(i, by_id[i].get("vec_score", 0.0)) for i in vec_rank if i in by_id]
        elif strat == "keyword":
            ordered = [(i, 1.0 / (1 + r)) for r, i in enumerate(kw_rank) if i in by_id]
        else:
            lists = []
            if vec_rank:
                lists.append(vec_rank)
            if kw_rank:
                lists.append(kw_rank)
            if not lists:
                ordered = []
            else:
                ordered = rrf_fuse(lists, k=k_rrf)

        hits: List[SearchHit] = []
        seen_docs_expand: set = set()
        for cid, score in ordered[:lim]:
            row = by_id.get(cid)
            if not row:
                continue
            # 补全 sqlite 字段
            if not row.get("text") or not row.get("title_chain"):
                ch = self.storage.get_chunk(cid)
                if ch:
                    row["text"] = row.get("text") or ch.text
                    row["title_chain"] = row.get("title_chain") or ch.title_chain
                    row["doc_id"] = row.get("doc_id") or ch.doc_id
                    row["ordinal"] = ch.ordinal
                    row["title"] = row.get("title") or ch.title
                    row["source_path"] = row.get("source_path") or ch.source_path

            text = str(row.get("text") or "")
            if expand_context and cid not in seen_docs_expand:
                neighbors = self.storage.get_chunk_neighbors(cid, before=1, after=1)
                if len(neighbors) > 1:
                    text = "\n\n---\n\n".join(n.text for n in neighbors)
                seen_docs_expand.add(cid)

            snippet = text.replace("\n", " ").strip()
            if len(snippet) > 420:
                snippet = snippet[:419] + "…"
            hits.append(
                SearchHit(
                    doc_id=str(row.get("doc_id") or ""),
                    chunk_id=cid,
                    title=str(row.get("title") or ""),
                    source_path=str(row.get("source_path") or ""),
                    snippet=snippet,
                    score=round(float(score), 4),
                    ordinal=int(row.get("ordinal") or 0),
                    title_chain=str(row.get("title_chain") or ""),
                )
            )

        debug_payload: Optional[Dict[str, Any]] = None
        if debug:
            vec_debug = [
                {
                    "chunk_id": cid,
                    "rank": i,
                    "vec_score": round(vec_scores.get(cid, 0.0), 4),
                }
                for i, cid in enumerate(vec_rank, start=1)
            ]
            kw_debug = [
                {"chunk_id": cid, "rank": i} for i, cid in enumerate(kw_rank, start=1)
            ]
            fused_debug = [
                {
                    "chunk_id": cid,
                    "rrf_score": round(float(sc), 6),
                    "vec_score": round(vec_scores.get(cid, 0.0), 4),
                    "in_vector": cid in vec_scores,
                    "in_keyword": cid in set(kw_rank),
                    "structure_quality": (by_id.get(cid) or {}).get(
                        "structure_quality"
                    )
                    or "",
                }
                for cid, sc in (ordered[: max(lim, fetch_n)])
            ]
            debug_payload = {
                "fetch_n": fetch_n,
                "rrf_k": k_rrf,
                "vector_count": len(vec_rank),
                "keyword_count": len(kw_rank),
                "vector": vec_debug,
                "keyword": kw_debug,
                "fused": fused_debug,
            }

        return SearchResponse(
            query=query,
            total=len(hits),
            results=hits,
            took_ms=int((time.time() - t0) * 1000),
            strategy=strat,
            debug=debug_payload,
        )
