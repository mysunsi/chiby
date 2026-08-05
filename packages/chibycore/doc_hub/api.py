"""DocHub FastAPI 路由 — 挂载 prefix=/api/docs。"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile as StarletteUploadFile

from chibycore.doc_hub.ingest import DocHubIngester
from chibycore.doc_hub.search import DocHubSearch
from chibycore.doc_hub.storage import DocHubStorage

logger = logging.getLogger(__name__)
router = APIRouter()

_storage: Optional[DocHubStorage] = None
_ingester: Optional[DocHubIngester] = None
_search: Optional[DocHubSearch] = None


def _get_storage() -> DocHubStorage:
    global _storage
    if _storage is None:
        _storage = DocHubStorage.get_instance()
    return _storage


def _reset_runtime(*, wipe_vectors: bool = False) -> None:
    """重建 ingester/search，避免长驻进程卡在旧的 hash embedder。"""
    global _ingester, _search
    from chibycore.doc_hub.embeddings import reset_embedder
    from chibycore.doc_hub.vector_store import wipe_chroma_dir

    if _ingester is not None:
        try:
            _ingester.vectors.reset()
        except Exception:  # noqa: BLE001
            pass
    reset_embedder()
    _ingester = None
    _search = None
    if wipe_vectors:
        wipe_chroma_dir(_get_storage().chroma_dir)


def _get_ingester() -> DocHubIngester:
    global _ingester, _search
    from chibycore.doc_hub.embeddings import get_embedder, embedder_backend_label

    # 若已从 hash 升级到真向量，丢掉旧 ingester 里缓存的 embedder
    get_embedder()
    if _ingester is not None:
        old = getattr(_ingester, "_backend_label", "")
        cur = embedder_backend_label()
        if old and old != cur:
            _ingester = None
            _search = None
    if _ingester is None:
        store = _get_storage()
        _ingester = DocHubIngester(storage=store)
        _ingester._backend_label = embedder_backend_label()  # type: ignore[attr-defined]
        _search = DocHubSearch(
            storage=store,
            vector_store=_ingester.vectors,
            embedder=_ingester.embedder,
        )
    return _ingester


def _get_search() -> DocHubSearch:
    global _search
    if _search is None:
        _get_ingester()
    assert _search is not None
    return _search


class IngestPathRequest(BaseModel):
    path: str = Field(..., description="本机绝对目录路径")
    recursive: bool = True
    async_mode: bool = True


@router.get("/stats")
async def docs_stats() -> Dict[str, Any]:
    from chibycore.doc_hub.embeddings import embedder_backend_label, resolve_embedding_credentials
    from chibycore.doc_hub.reindex_job import get_reindex_manager

    # 触发 hash→真向量自动切换，并同步 runtime
    _get_ingester()
    s = _get_storage()
    creds = resolve_embedding_credentials()
    label = embedder_backend_label()
    corpus = s.chunk_corpus_stats()
    reindex = get_reindex_manager().progress_summary()
    return {
        "documents": s.count_documents(),
        "root_dir": str(s.root_dir),
        "db_path": str(s.db_path),
        "embedding_backend": label,
        "vector_backend": (
            __import__("os").getenv("DOC_HUB_VECTOR_BACKEND") or "chroma"
        ).strip().lower(),
        "embedding_configured": bool(creds),
        "embedding_hint": (
            None
            if (creds and not label.startswith("hash"))
            else (
                "已配置 DOC_HUB_EMBEDDING_* 但仍为 hash，请重启服务或检查 Ollama"
                if creds
                else "未配置 OPENAI_API_KEY / DOC_HUB_EMBEDDING_*，当前用本地 hash 向量"
            )
        ),
        **corpus,
        **reindex,
    }


@router.post("/reload-embedding")
async def reload_embedding(wipe_vectors: bool = Query(True)) -> Dict[str, Any]:
    """强制重建 embedding / 向量运行时。

    默认 wipe_vectors=true：清空旧 Chroma（hash 256 维与 nomic 768 维不兼容）。
    """
    _reset_runtime(wipe_vectors=wipe_vectors)
    _get_ingester()
    from chibycore.doc_hub.embeddings import embedder_backend_label, resolve_embedding_credentials

    creds = resolve_embedding_credentials()
    label = embedder_backend_label()
    return {
        "ok": True,
        "embedding_backend": label,
        "embedding_configured": bool(creds),
        "model": (creds or {}).get("model"),
        "api_base": (creds or {}).get("api_base"),
        "vectors_wiped": wipe_vectors,
        "hint": "请重新上传文档以用新向量入库" if wipe_vectors else None,
    }


@router.get("")
async def list_docs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    s = _get_storage()
    entries = [d.model_dump_compat() for d in s.list_documents(limit=limit, offset=offset)]
    return {"total": s.count_documents(), "entries": entries}


@router.get("/search")
async def search_docs(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=20),
    strategy: str = Query("hybrid", description="hybrid|vector|keyword"),
    expand_context: bool = Query(True),
    debug: bool = Query(False, description="返回两路原始分数与 RRF 细节"),
    rrf_k: Optional[int] = Query(None, ge=1, le=200, description="RRF k，默认 30"),
) -> Dict[str, Any]:
    resp = _get_search().search(
        q,
        limit=limit,
        strategy=strategy,
        expand_context=expand_context,
        debug=debug,
        rrf_k=rrf_k,
    )
    data = resp.model_dump()
    if not debug:
        data.pop("debug", None)
    return data


class ReindexRequest(BaseModel):
    wipe_vectors: bool = False


@router.post("/reindex")
async def reindex_docs(body: Optional[ReindexRequest] = None) -> Dict[str, Any]:
    """一键按原始文件重建向量索引。"""
    from chibycore.doc_hub.reindex_job import get_reindex_manager

    req = body or ReindexRequest()
    ing = _get_ingester()
    job_id = get_reindex_manager().start(
        ingester=ing,
        storage=_get_storage(),
        wipe_vectors=bool(req.wipe_vectors),
    )
    return {"ok": True, "job_id": job_id, "wipe_vectors": req.wipe_vectors}


@router.get("/reindex/{job_id}/status")
async def reindex_status(job_id: str) -> Dict[str, Any]:
    from chibycore.doc_hub.reindex_job import get_reindex_manager

    job = get_reindex_manager().get(job_id)
    if not job:
        raise HTTPException(404, "重建任务不存在")
    return {"ok": True, "job_id": job_id, **job}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    job = _get_ingester().get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@router.get("/{doc_id}")
async def get_doc(doc_id: str) -> Dict[str, Any]:
    doc = _get_storage().get_document(doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    chunks = _get_storage().list_chunks(doc_id)
    data = doc.model_dump_compat()
    data["chunks"] = [
        {
            "id": c.id,
            "ordinal": c.ordinal,
            "text_preview": (c.text[:240] + "…") if len(c.text) > 240 else c.text,
        }
        for c in chunks
    ]
    return data


@router.get("/{doc_id}/chunks/{chunk_id}")
async def get_chunk(doc_id: str, chunk_id: str) -> Dict[str, Any]:
    chunk = _get_storage().get_chunk(chunk_id)
    if not chunk or chunk.doc_id != doc_id:
        raise HTTPException(404, "片段不存在")
    return chunk.model_dump()


@router.delete("/{doc_id}")
async def delete_doc(doc_id: str) -> Dict[str, Any]:
    ok = _get_ingester().delete_document(doc_id)
    if not ok:
        raise HTTPException(404, "文档不存在")
    return {"ok": True}


def _decode_upload_name(raw_name: str) -> str:
    name = raw_name or "upload.bin"
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name


def _collect_upload_files(form) -> List[UploadFile]:
    """从 multipart 用 getlist 收集同名多文件（files / file）。

    注意：不要用 ``Optional[List[UploadFile]] = File(None)``——依赖注入在
    多 part 同字段时经常只拿到 1 个或 None；必须走 FormData.getlist。
    """
    batch: List[UploadFile] = []
    seen: set = set()
    for key in ("files", "file"):
        try:
            items = form.getlist(key)
        except Exception:  # noqa: BLE001
            continue
        for item in items:
            if not isinstance(item, StarletteUploadFile):
                continue
            name = (item.filename or "").strip()
            if not name:
                continue
            # 同一对象可能同时出现在 file/files
            oid = id(item)
            if oid in seen:
                continue
            seen.add(oid)
            batch.append(item)
    return batch


async def _ingest_one_upload(file: UploadFile) -> Dict[str, Any]:
    name = _decode_upload_name(file.filename or "upload.bin")
    suffix = Path(name).suffix.lower() or ".bin"
    display_stem = Path(name).stem.strip() or "upload"
    tmp_dir = Path(tempfile.mkdtemp(prefix="dochub_"))
    try:
        dest = tmp_dir / f"upload{suffix}"
        raw = await file.read()
        dest.write_bytes(raw)
        result = _get_ingester().ingest_file(
            dest,
            copy_into_store=True,
            async_if_large=True,
            display_title=display_stem,
            source_name=name,
        )
        result = dict(result)
        result["filename"] = name
        return result
    finally:
        try:
            await file.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass


@router.post("/upload")
async def upload_doc(request: Request) -> Dict[str, Any]:
    """上传入库。支持单文件或多文件（表单字段 ``files`` / ``file``，可重复）。

    多文件在服务端**顺序**处理（避免 embedding/Chroma 并发打架）；
    单文件过大仍走异步 job。
    """
    form = await request.form()
    batch = _collect_upload_files(form)
    if not batch:
        raise HTTPException(400, "请选择至少一个文件")

    if len(batch) == 1:
        result = await _ingest_one_upload(batch[0])
        if not result.get("ok"):
            raise HTTPException(400, result.get("error") or "入库失败")
        return result

    results: List[Dict[str, Any]] = []
    ok_n = 0
    for uf in batch:
        r = await _ingest_one_upload(uf)
        results.append(r)
        if r.get("ok"):
            ok_n += 1
    return {
        "ok": ok_n > 0,
        "total": len(batch),
        "imported": ok_n,
        "failed": len(batch) - ok_n,
        "results": results,
    }


@router.post("/ingest-path")
async def ingest_path(body: IngestPathRequest) -> Dict[str, Any]:
    result = _get_ingester().ingest_path(
        Path(body.path),
        recursive=body.recursive,
        async_mode=body.async_mode,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "导入失败")
    return result
