"""向量存储：Chroma 持久化；测试可用内存实现。"""
from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


class VectorStore(Protocol):
    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Dict[str, Any]],
    ) -> None: ...

    def query(
        self,
        *,
        embedding: Sequence[float],
        n_results: int = 8,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]: ...

    def delete_by_doc_id(self, doc_id: str) -> None: ...

    def delete_ids(self, ids: Sequence[str]) -> None: ...

    def reset(self) -> None: ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return float(dot / (na * nb))


def _is_dim_mismatch_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "dimension" in msg or "expecting embedding" in msg


class InMemoryVectorStore:
    """纯 Python 余弦检索（单测 / 无 chromadb 时）。"""

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._dim: Optional[int] = None

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Dict[str, Any]],
    ) -> None:
        if embeddings:
            dim = len(embeddings[0])
            if self._dim is not None and self._dim != dim:
                logger.warning(
                    "InMemoryVectorStore 维度变化 %s→%s，清空旧向量", self._dim, dim
                )
                self._rows.clear()
            self._dim = dim
        for i, cid in enumerate(ids):
            self._rows[str(cid)] = {
                "id": str(cid),
                "embedding": [float(x) for x in embeddings[i]],
                "document": documents[i],
                "metadata": dict(metadatas[i] or {}),
            }

    def query(
        self,
        *,
        embedding: Sequence[float],
        n_results: int = 8,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in self._rows.values():
            meta = row["metadata"]
            if where:
                ok = True
                for k, v in where.items():
                    if meta.get(k) != v:
                        ok = False
                        break
                if not ok:
                    continue
            if len(row["embedding"]) != len(embedding):
                continue
            score = _cosine(embedding, row["embedding"])
            scored.append(
                (
                    score,
                    {
                        "id": row["id"],
                        "document": row["document"],
                        "metadata": meta,
                        "score": score,
                    },
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[: max(1, n_results)]]

    def delete_by_doc_id(self, doc_id: str) -> None:
        drop = [k for k, v in self._rows.items() if v["metadata"].get("doc_id") == doc_id]
        for k in drop:
            del self._rows[k]

    def delete_ids(self, ids: Sequence[str]) -> None:
        for i in ids:
            self._rows.pop(str(i), None)

    def reset(self) -> None:
        self._rows.clear()
        self._dim = None


class ChromaVectorStore:
    def __init__(self, persist_dir: Path, collection_name: str = "doc_hub") -> None:
        import chromadb
        from chromadb.config import Settings

        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._collection_name = collection_name
        self._client = chromadb.PersistentClient(
            path=str(self._persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self._dim: Optional[int] = None
        self._col = self._get_or_create()

    def _get_or_create(self):
        return self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def _recreate(self, dim: Optional[int] = None) -> None:
        name = self._collection_name
        try:
            self._client.delete_collection(name)
        except Exception as e:  # noqa: BLE001
            logger.debug("delete_collection %s: %s", name, e)
        meta: Dict[str, Any] = {"hnsw:space": "cosine"}
        if dim:
            meta["doc_hub_dim"] = int(dim)
        self._col = self._client.get_or_create_collection(name=name, metadata=meta)
        self._dim = dim
        logger.warning("Chroma collection %s 已重建（dim=%s）", name, dim)

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Dict[str, Any]],
    ) -> None:
        if not ids:
            return
        dim = len(embeddings[0]) if embeddings else None
        vectors = [[float(x) for x in e] for e in embeddings]
        docs = list(documents)
        metas = [dict(m) for m in metadatas]
        id_list = list(ids)

        # 已知维度变化时先重建，避免 InvalidArgumentError
        if dim and self._dim is not None and self._dim != dim:
            self._recreate(dim)

        try:
            self._col.upsert(
                ids=id_list,
                embeddings=vectors,
                documents=docs,
                metadatas=metas,
            )
            if dim:
                self._dim = dim
        except Exception as e:  # noqa: BLE001
            if _is_dim_mismatch_error(e):
                logger.warning("Chroma 维度冲突，重建后重试: %s", e)
                self._recreate(dim)
                self._col.upsert(
                    ids=id_list,
                    embeddings=vectors,
                    documents=docs,
                    metadatas=metas,
                )
                if dim:
                    self._dim = dim
            else:
                raise

    def query(
        self,
        *,
        embedding: Sequence[float],
        n_results: int = 8,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {
            "query_embeddings": [[float(x) for x in embedding]],
            "n_results": max(1, int(n_results)),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        try:
            res = self._col.query(**kwargs)
        except Exception as e:  # noqa: BLE001
            if _is_dim_mismatch_error(e):
                logger.warning("Chroma 查询维度冲突（库为空或需重建）: %s", e)
                return []
            raise
        out: List[Dict[str, Any]] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, cid in enumerate(ids):
            dist = float(dists[i]) if i < len(dists) else 1.0
            score = 1.0 - dist
            out.append(
                {
                    "id": cid,
                    "document": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "score": score,
                }
            )
        return out

    def delete_by_doc_id(self, doc_id: str) -> None:
        try:
            self._col.delete(where={"doc_id": doc_id})
        except Exception as e:  # noqa: BLE001
            logger.warning("chroma delete_by_doc_id failed: %s", e)

    def delete_ids(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        try:
            self._col.delete(ids=list(ids))
        except Exception as e:  # noqa: BLE001
            logger.warning("chroma delete_ids failed: %s", e)

    def reset(self) -> None:
        self._recreate(None)


def wipe_chroma_dir(persist_dir: Path) -> None:
    """删除整个 Chroma 持久化目录（切换 embedding 维度时用）。"""
    p = Path(persist_dir)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
        logger.warning("已清空 Chroma 目录: %s", p)
    p.mkdir(parents=True, exist_ok=True)


def open_vector_store(
    persist_dir: Path,
    *,
    backend: Optional[str] = None,
) -> VectorStore:
    """backend: chroma | qdrant | memory；缺依赖时回退。"""
    import os

    b = (backend or os.getenv("DOC_HUB_VECTOR_BACKEND") or "chroma").strip().lower()
    if b in ("memory", "mem", "inmemory"):
        return InMemoryVectorStore()
    if b == "qdrant":
        try:
            return QdrantVectorStore(
                url=os.getenv("QDRANT_URL") or "http://localhost:6333",
                collection_name=os.getenv("DOC_HUB_QDRANT_COLLECTION") or "doc_hub",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Qdrant 不可用，回退 Chroma/memory: %s", e)
    try:
        return ChromaVectorStore(persist_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("Chroma 不可用，回退 InMemoryVectorStore: %s", e)
        return InMemoryVectorStore()


class QdrantVectorStore:
    """生产向向量库（可选依赖 qdrant-client）。"""

    def __init__(
        self,
        *,
        url: str = "http://localhost:6333",
        collection_name: str = "doc_hub",
        api_key: Optional[str] = None,
    ) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm

        self._qm = qm
        self._collection = collection_name
        self._dim: Optional[int] = None
        kwargs: Dict[str, Any] = {"url": url}
        key = api_key or __import__("os").getenv("QDRANT_API_KEY")
        if key:
            kwargs["api_key"] = key
        self._client = QdrantClient(**kwargs)
        self._ensure_collection(None)

    def _ensure_collection(self, dim: Optional[int]) -> None:
        from qdrant_client.http import models as qm

        names = {c.name for c in self._client.get_collections().collections}
        if self._collection not in names:
            if not dim:
                return
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qm.VectorParams(size=int(dim), distance=qm.Distance.COSINE),
            )
            self._dim = dim
            return
        if dim and self._dim and self._dim != dim:
            self._client.delete_collection(self._collection)
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qm.VectorParams(size=int(dim), distance=qm.Distance.COSINE),
            )
            self._dim = dim

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Dict[str, Any]],
    ) -> None:
        from qdrant_client.http import models as qm

        if not ids:
            return
        dim = len(embeddings[0])
        self._ensure_collection(dim)
        if self._collection not in {
            c.name for c in self._client.get_collections().collections
        }:
            self._ensure_collection(dim)
        points = []
        for i, cid in enumerate(ids):
            meta = dict(metadatas[i] or {})
            meta["document"] = documents[i]
            meta["chunk_id"] = str(cid)
            points.append(
                qm.PointStruct(
                    id=self._point_id(str(cid)),
                    vector=[float(x) for x in embeddings[i]],
                    payload=meta,
                )
            )
        self._client.upsert(collection_name=self._collection, points=points)
        self._dim = dim

    @staticmethod
    def _point_id(cid: str) -> str:
        # Qdrant 支持 UUID 或无符号 int；用稳定 hash 映射到 uuid5
        import uuid

        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"dochub:{cid}"))

    def query(
        self,
        *,
        embedding: Sequence[float],
        n_results: int = 8,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        from qdrant_client.http import models as qm

        try:
            names = {c.name for c in self._client.get_collections().collections}
            if self._collection not in names:
                return []
        except Exception:
            return []
        flt = None
        if where:
            must = [
                qm.FieldCondition(key=k, match=qm.MatchValue(value=v))
                for k, v in where.items()
            ]
            flt = qm.Filter(must=must)
        res = self._client.search(
            collection_name=self._collection,
            query_vector=[float(x) for x in embedding],
            limit=max(1, int(n_results)),
            query_filter=flt,
            with_payload=True,
        )
        out: List[Dict[str, Any]] = []
        for hit in res:
            payload = dict(hit.payload or {})
            doc = payload.pop("document", "")
            cid = str(payload.get("chunk_id") or hit.id)
            out.append(
                {
                    "id": cid,
                    "document": doc,
                    "metadata": payload,
                    "score": float(hit.score or 0.0),
                }
            )
        return out

    def delete_by_doc_id(self, doc_id: str) -> None:
        from qdrant_client.http import models as qm

        try:
            self._client.delete(
                collection_name=self._collection,
                points_selector=qm.FilterSelector(
                    filter=qm.Filter(
                        must=[
                            qm.FieldCondition(
                                key="doc_id", match=qm.MatchValue(value=doc_id)
                            )
                        ]
                    )
                ),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("qdrant delete_by_doc_id failed: %s", e)

    def delete_ids(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        try:
            self._client.delete(
                collection_name=self._collection,
                points_selector=[self._point_id(str(i)) for i in ids],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("qdrant delete_ids failed: %s", e)

    def reset(self) -> None:
        try:
            self._client.delete_collection(self._collection)
        except Exception as e:  # noqa: BLE001
            logger.debug("qdrant reset: %s", e)
        self._dim = None
