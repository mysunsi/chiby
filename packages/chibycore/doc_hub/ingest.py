"""文档入库流水线：parse → 语义切片 → embed → 向量库 + sqlite。"""
from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from chibycore.doc_hub.chunker_v2 import chunk_parsed_document
from chibycore.doc_hub.embeddings import Embedder, embed_texts, get_embedder
from chibycore.doc_hub.models import ChunkRecord, DocStatus
from chibycore.doc_hub.parse import SUPPORTED_SUFFIXES
from chibycore.doc_hub.storage import DocHubStorage
from chibycore.doc_hub.structured_parse import parse_to_document
from chibycore.doc_hub.vector_store import VectorStore, open_vector_store

logger = logging.getLogger(__name__)

SYNC_MAX_BYTES = 20 * 1024 * 1024  # 20MB
_EMBED_BATCH = 32
_MIN_TEXT_CHARS = 100


class DocHubIngester:
    def __init__(
        self,
        storage: Optional[DocHubStorage] = None,
        vector_store: Optional[VectorStore] = None,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self.storage = storage or DocHubStorage.get_instance()
        self.vectors = vector_store or open_vector_store(self.storage.chroma_dir)
        self.embedder = embedder or get_embedder()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def ingest_file(
        self,
        path: Path,
        *,
        copy_into_store: bool = True,
        async_if_large: bool = True,
        display_title: str = "",
        source_name: str = "",
    ) -> Dict[str, Any]:
        p = Path(path)
        if not p.is_file():
            return {"ok": False, "error": f"文件不存在: {p}"}
        size = p.stat().st_size
        if async_if_large and size > SYNC_MAX_BYTES:
            job_id = uuid.uuid4().hex[:12]
            with self._lock:
                self._jobs[job_id] = {"status": "pending", "path": str(p)}
            t = threading.Thread(
                target=self._job_ingest_file,
                args=(job_id, p, copy_into_store, display_title, source_name),
                daemon=True,
            )
            t.start()
            return {"ok": True, "async": True, "job_id": job_id, "bytes_size": size}
        return self._ingest_file_sync(
            p,
            copy_into_store=copy_into_store,
            display_title=display_title,
            source_name=source_name,
        )

    def _job_ingest_file(
        self,
        job_id: str,
        path: Path,
        copy_into_store: bool,
        display_title: str = "",
        source_name: str = "",
    ) -> None:
        try:
            result = self._ingest_file_sync(
                path,
                copy_into_store=copy_into_store,
                display_title=display_title,
                source_name=source_name,
            )
            with self._lock:
                self._jobs[job_id] = {**result, "status": "done" if result.get("ok") else "failed"}
        except Exception as e:  # noqa: BLE001
            logger.exception("async ingest failed")
            with self._lock:
                self._jobs[job_id] = {"ok": False, "status": "failed", "error": str(e)}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._jobs.get(job_id) or {}) or None

    def _ingest_file_sync(
        self,
        path: Path,
        *,
        copy_into_store: bool,
        display_title: str = "",
        source_name: str = "",
    ) -> Dict[str, Any]:
        suf = path.suffix.lower()
        if suf not in SUPPORTED_SUFFIXES:
            return {"ok": False, "error": f"不支持的格式: {suf}"}

        stored_path = ""
        source_path = source_name or str(path.resolve())
        try:
            parsed, plain = parse_to_document(path)
            title = parsed.title
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

        # 优先用上传时的原始文件名（中文），避免临时 ASCII 名/乱码
        if display_title and display_title.strip():
            title = display_title.strip()
            parsed.title = title
        elif title and any(ord(c) > 127 for c in title):
            pass
        elif source_name:
            title = Path(source_name).stem or title
            parsed.title = title

        plain_len = len((plain or "").strip())
        if plain_len < _MIN_TEXT_CHARS:
            return {
                "ok": False,
                "error": (
                    f"解析文本过短（{plain_len} 字符），文档可能为纯图或扫描件，"
                    "请提供可复制文本或先做 OCR"
                ),
                "error_code": "text_too_short",
                "text_len": plain_len,
            }

        # 同名覆盖：避免排障重传堆出多条重复
        replaced: List[str] = []
        for old in self.storage.find_documents_by_title(title):
            self.vectors.delete_by_doc_id(old.id)
            if self.storage.delete_document(old.id):
                replaced.append(old.id)
                logger.info("DocHub 同名覆盖，已删除旧文档 %s (%s)", old.id, title)

        doc = self.storage.create_document(
            title=title,
            source_path=source_path,
            mime_or_ext=suf,
            bytes_size=path.stat().st_size,
            status=DocStatus.PENDING,
        )
        try:
            if copy_into_store:
                dest = self.storage.files_dir / f"{doc.id}{suf}"
                shutil.copy2(path, dest)
                stored_path = str(dest)
                self.storage.update_document(doc.id, stored_path=stored_path)

            semantic = chunk_parsed_document(parsed)
            structure_quality = (
                getattr(parsed, "structure_quality", None) or ""
            ).strip().lower() or "low"
            if not semantic:
                self.storage.update_document(
                    doc.id, status=DocStatus.FAILED, error="切片结果为空", chunk_count=0
                )
                return {"ok": False, "error": "切片结果为空", "doc_id": doc.id}

            avg_len = sum(len(c.text) for c in semantic) / max(1, len(semantic))
            if avg_len < 40:
                logger.warning(
                    "DocHub 平均 chunk 过短 doc=%s avg=%.1f n=%s",
                    doc.id,
                    avg_len,
                    len(semantic),
                )

            chunk_recs: List[ChunkRecord] = []
            ids: List[str] = []
            docs: List[str] = []
            metas: List[Dict[str, Any]] = []
            for sc in semantic:
                cid = f"{doc.id}_{sc.ordinal:04d}"
                chunk_recs.append(
                    ChunkRecord(
                        id=cid,
                        doc_id=doc.id,
                        ordinal=sc.ordinal,
                        text=sc.text,
                        title=title,
                        source_path=source_path,
                        title_chain=sc.title_chain or "",
                    )
                )
                ids.append(cid)
                docs.append(sc.text)
                metas.append(
                    {
                        "doc_id": doc.id,
                        "chunk_id": cid,
                        "ordinal": sc.ordinal,
                        "title": title[:200],
                        "source_path": source_path[:500],
                        "title_chain": (sc.title_chain or "")[:500],
                        "structure_quality": structure_quality,
                    }
                )

            # 分批 embedding
            all_emb: List[List[float]] = []
            for i in range(0, len(docs), _EMBED_BATCH):
                batch = docs[i : i + _EMBED_BATCH]
                all_emb.extend(embed_texts(batch, self.embedder))

            self.vectors.delete_by_doc_id(doc.id)
            self.vectors.upsert(
                ids=ids,
                embeddings=all_emb,
                documents=docs,
                metadatas=metas,
            )
            self.storage.replace_chunks(doc.id, chunk_recs)
            self.storage.update_document(
                doc.id,
                title=title,
                status=DocStatus.READY,
                chunk_count=len(chunk_recs),
                error="",
            )
            return {
                "ok": True,
                "async": False,
                "doc_id": doc.id,
                "title": title,
                "chunk_count": len(chunk_recs),
                "structure_quality": structure_quality,
                "status": DocStatus.READY.value,
                "replaced_ids": replaced,
                "text_len": plain_len,
                "avg_chunk_len": round(avg_len, 1),
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("ingest failed for %s", path)
            self.storage.update_document(
                doc.id, status=DocStatus.FAILED, error=str(e)[:800]
            )
            return {"ok": False, "error": str(e), "doc_id": doc.id}

    def ingest_path(
        self,
        directory: Path,
        *,
        recursive: bool = True,
        async_mode: bool = True,
    ) -> Dict[str, Any]:
        d = Path(directory)
        if not d.is_dir():
            return {"ok": False, "error": f"目录不存在: {d}"}
        pattern = "**/*" if recursive else "*"
        files = [
            p
            for p in d.glob(pattern)
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        ]
        if async_mode:
            job_id = uuid.uuid4().hex[:12]
            with self._lock:
                self._jobs[job_id] = {
                    "status": "pending",
                    "total": len(files),
                    "done": 0,
                    "results": [],
                }

            def _run() -> None:
                results = []
                for i, f in enumerate(files):
                    r = self._ingest_file_sync(f, copy_into_store=True)
                    results.append({"path": str(f), **r})
                    with self._lock:
                        self._jobs[job_id]["done"] = i + 1
                        self._jobs[job_id]["results"] = results
                ok_n = sum(1 for r in results if r.get("ok"))
                with self._lock:
                    self._jobs[job_id].update(
                        {
                            "status": "done",
                            "ok": True,
                            "imported": ok_n,
                            "total": len(files),
                        }
                    )

            threading.Thread(target=_run, daemon=True).start()
            return {
                "ok": True,
                "async": True,
                "job_id": job_id,
                "total": len(files),
            }

        results = []
        for f in files:
            results.append({"path": str(f), **self._ingest_file_sync(f, copy_into_store=True)})
        ok_n = sum(1 for r in results if r.get("ok"))
        return {"ok": True, "async": False, "imported": ok_n, "total": len(files), "results": results}

    def delete_document(self, doc_id: str) -> bool:
        self.vectors.delete_by_doc_id(doc_id)
        return self.storage.delete_document(doc_id)
