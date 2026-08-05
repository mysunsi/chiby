"""一键重建向量索引：按 stored_path / source_path 重跑入库。"""
from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from chibycore.doc_hub.ingest import DocHubIngester
from chibycore.doc_hub.storage import DocHubStorage

logger = logging.getLogger(__name__)


class ReindexJobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def active_job_id(self) -> Optional[str]:
        with self._lock:
            for jid, j in self._jobs.items():
                if j.get("status") == "running":
                    return jid
        return None

    def progress_summary(self) -> Dict[str, Any]:
        """供 /stats：reindex_in_progress + 进度字段。"""
        with self._lock:
            for jid, j in self._jobs.items():
                if j.get("status") == "running":
                    return {
                        "reindex_in_progress": True,
                        "reindex_job_id": jid,
                        "reindex_done": int(j.get("done") or 0),
                        "reindex_total": int(j.get("total") or 0),
                        "reindex_ok": int(j.get("ok") or 0),
                        "reindex_failed": int(j.get("failed") or 0),
                    }
        return {
            "reindex_in_progress": False,
            "reindex_job_id": None,
            "reindex_done": 0,
            "reindex_total": 0,
            "reindex_ok": 0,
            "reindex_failed": 0,
        }

    def start(
        self,
        *,
        ingester: DocHubIngester,
        storage: Optional[DocHubStorage] = None,
        wipe_vectors: bool = False,
    ) -> str:
        existing = self.active_job_id()
        if existing:
            return existing

        store = storage or ingester.storage
        job_id = uuid.uuid4().hex[:12]
        doc_ids = store.list_all_document_ids()
        with self._lock:
            self._jobs[job_id] = {
                "status": "running",
                "total": len(doc_ids),
                "done": 0,
                "ok": 0,
                "failed": 0,
                "errors": [],
            }

        def _run() -> None:
            if wipe_vectors:
                try:
                    ingester.vectors.reset()
                except Exception as e:  # noqa: BLE001
                    logger.warning("wipe vectors: %s", e)
            for i, did in enumerate(doc_ids):
                doc = store.get_document(did)
                if not doc:
                    continue
                path = None
                if doc.stored_path and Path(doc.stored_path).is_file():
                    path = Path(doc.stored_path)
                elif doc.source_path and Path(doc.source_path).is_file():
                    path = Path(doc.source_path)
                if path is None:
                    with self._lock:
                        self._jobs[job_id]["failed"] += 1
                        self._jobs[job_id]["errors"].append(
                            {"doc_id": did, "error": "找不到原始文件"}
                        )
                        self._jobs[job_id]["done"] = i + 1
                    continue
                # 删除旧记录后按同路径重建（ingest 会按 title 覆盖）
                try:
                    r = ingester._ingest_file_sync(  # noqa: SLF001
                        path,
                        copy_into_store=True,
                        display_title=doc.title,
                        source_name=doc.source_path or path.name,
                    )
                    with self._lock:
                        if r.get("ok"):
                            self._jobs[job_id]["ok"] += 1
                        else:
                            self._jobs[job_id]["failed"] += 1
                            self._jobs[job_id]["errors"].append(
                                {"doc_id": did, "error": r.get("error")}
                            )
                        self._jobs[job_id]["done"] = i + 1
                except Exception as e:  # noqa: BLE001
                    logger.exception("reindex doc %s", did)
                    with self._lock:
                        self._jobs[job_id]["failed"] += 1
                        self._jobs[job_id]["errors"].append(
                            {"doc_id": did, "error": str(e)}
                        )
                        self._jobs[job_id]["done"] = i + 1
            with self._lock:
                self._jobs[job_id]["status"] = "done"

        threading.Thread(target=_run, daemon=True).start()
        return job_id

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None


_REINDEX = ReindexJobManager()


def get_reindex_manager() -> ReindexJobManager:
    return _REINDEX
