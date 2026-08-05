"""DocHub 元数据 SQLite 存储。"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from chibycore.doc_hub.models import ChunkRecord, DocStatus, DocumentRecord

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.utcnow().isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class DocHubStorage:
    _instance: Optional["DocHubStorage"] = None
    _instance_lock = threading.Lock()

    def __init__(self, root_dir: Optional[Path] = None) -> None:
        root = Path(root_dir) if root_dir else (
            __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root().parent / "data" / "doc_hub"
        )
        self.root_dir = root
        self.files_dir = root / "files"
        self.chroma_dir = root / "chroma"
        self.db_path = root / "docs.db"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    @classmethod
    def get_instance(cls, root_dir: Optional[Path] = None) -> "DocHubStorage":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(root_dir=root_dir)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS documents (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        source_path TEXT DEFAULT '',
                        stored_path TEXT DEFAULT '',
                        mime_or_ext TEXT DEFAULT '',
                        status TEXT NOT NULL,
                        chunk_count INTEGER DEFAULT 0,
                        error TEXT DEFAULT '',
                        bytes_size INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS chunks (
                        id TEXT PRIMARY KEY,
                        doc_id TEXT NOT NULL,
                        ordinal INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        title TEXT DEFAULT '',
                        source_path TEXT DEFAULT '',
                        title_chain TEXT DEFAULT '',
                        FOREIGN KEY(doc_id) REFERENCES documents(id)
                    );
                    CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
                    """
                )
                # 兼容旧库：补 title_chain 列
                cols = {
                    r[1]
                    for r in conn.execute("PRAGMA table_info(chunks)").fetchall()
                }
                if "title_chain" not in cols:
                    conn.execute(
                        "ALTER TABLE chunks ADD COLUMN title_chain TEXT DEFAULT ''"
                    )
                # FTS5：优先 trigram（利于中文子串）；失败则 unicode61
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
                ).fetchone()
                if not row:
                    try:
                        conn.execute(
                            """
                            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                                chunk_id UNINDEXED,
                                text,
                                title_chain,
                                tokenize='trigram'
                            )
                            """
                        )
                    except sqlite3.OperationalError:
                        conn.execute(
                            """
                            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                                chunk_id UNINDEXED,
                                text,
                                title_chain,
                                tokenize='unicode61'
                            )
                            """
                        )
                conn.commit()
            finally:
                conn.close()

    def create_document(
        self,
        *,
        title: str,
        source_path: str = "",
        stored_path: str = "",
        mime_or_ext: str = "",
        bytes_size: int = 0,
        status: DocStatus = DocStatus.PENDING,
        doc_id: Optional[str] = None,
    ) -> DocumentRecord:
        rid = doc_id or _new_id("d")
        now = _utcnow()
        rec = DocumentRecord(
            id=rid,
            title=title or rid,
            source_path=source_path,
            stored_path=stored_path,
            mime_or_ext=mime_or_ext,
            status=status,
            bytes_size=int(bytes_size or 0),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO documents(
                        id, title, source_path, stored_path, mime_or_ext,
                        status, chunk_count, error, bytes_size, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        rec.id,
                        rec.title,
                        rec.source_path,
                        rec.stored_path,
                        rec.mime_or_ext,
                        rec.status.value,
                        0,
                        "",
                        rec.bytes_size,
                        rec.created_at,
                        rec.updated_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return rec

    def update_document(
        self,
        doc_id: str,
        *,
        title: Optional[str] = None,
        status: Optional[DocStatus] = None,
        chunk_count: Optional[int] = None,
        error: Optional[str] = None,
        stored_path: Optional[str] = None,
    ) -> Optional[DocumentRecord]:
        with self._lock:
            existing = self.get_document(doc_id)
            if not existing:
                return None
            if title is not None:
                existing.title = title
            if status is not None:
                existing.status = status
            if chunk_count is not None:
                existing.chunk_count = int(chunk_count)
            if error is not None:
                existing.error = error
            if stored_path is not None:
                existing.stored_path = stored_path
            existing.updated_at = _utcnow()
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE documents SET title=?, status=?, chunk_count=?, error=?,
                        stored_path=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        existing.title,
                        existing.status.value,
                        existing.chunk_count,
                        existing.error,
                        existing.stored_path,
                        existing.updated_at,
                        doc_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            return existing

    def get_document(self, doc_id: str) -> Optional[DocumentRecord]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM documents WHERE id=?", (doc_id,)
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        return DocumentRecord(
            id=row["id"],
            title=row["title"],
            source_path=row["source_path"] or "",
            stored_path=row["stored_path"] or "",
            mime_or_ext=row["mime_or_ext"] or "",
            status=DocStatus(row["status"]),
            chunk_count=int(row["chunk_count"] or 0),
            error=row["error"] or "",
            bytes_size=int(row["bytes_size"] or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_documents(self, limit: int = 100, offset: int = 0) -> List[DocumentRecord]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM documents
                    ORDER BY datetime(updated_at) DESC
                    LIMIT ? OFFSET ?
                    """,
                    (max(1, min(int(limit), 500)), max(0, int(offset))),
                ).fetchall()
            finally:
                conn.close()
        return [
            DocumentRecord(
                id=r["id"],
                title=r["title"],
                source_path=r["source_path"] or "",
                stored_path=r["stored_path"] or "",
                mime_or_ext=r["mime_or_ext"] or "",
                status=DocStatus(r["status"]),
                chunk_count=int(r["chunk_count"] or 0),
                error=r["error"] or "",
                bytes_size=int(r["bytes_size"] or 0),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def count_documents(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                return int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            finally:
                conn.close()

    def chunk_corpus_stats(self) -> Dict[str, Any]:
        """观测用：chunk 总量、平均长度、长度方差、FTS 行数。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n,
                           COALESCE(AVG(LENGTH(text)), 0) AS avg_len,
                           COALESCE(AVG(LENGTH(text) * LENGTH(text)), 0) AS avg_len2
                    FROM chunks
                    """
                ).fetchone()
                n = int(row["n"] or 0)
                avg_len = float(row["avg_len"] or 0)
                avg_len2 = float(row["avg_len2"] or 0)
                # Var(X)=E[X^2]-E[X]^2
                var = max(0.0, avg_len2 - avg_len * avg_len)
                fts_n = 0
                try:
                    fts_n = int(
                        conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    fts_n = 0
                return {
                    "chunk_count": n,
                    "chunk_avg_len": round(avg_len, 1),
                    "chunk_len_stddev": round(var**0.5, 1),
                    "fts_row_count": fts_n,
                }
            finally:
                conn.close()

    def find_documents_by_title(self, title: str) -> List[DocumentRecord]:
        """按标题精确匹配（用于同名上传覆盖）。"""
        t = (title or "").strip()
        if not t:
            return []
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM documents WHERE title=? ORDER BY datetime(updated_at) DESC",
                    (t,),
                ).fetchall()
            finally:
                conn.close()
        return [
            DocumentRecord(
                id=r["id"],
                title=r["title"],
                source_path=r["source_path"] or "",
                stored_path=r["stored_path"] or "",
                mime_or_ext=r["mime_or_ext"] or "",
                status=DocStatus(r["status"]),
                chunk_count=int(r["chunk_count"] or 0),
                error=r["error"] or "",
                bytes_size=int(r["bytes_size"] or 0),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def replace_chunks(self, doc_id: str, chunks: List[ChunkRecord]) -> None:
        with self._lock:
            conn = self._connect()
            try:
                old_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM chunks WHERE doc_id=?", (doc_id,)
                    ).fetchall()
                ]
                conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
                if old_ids:
                    for oid in old_ids:
                        try:
                            conn.execute(
                                "DELETE FROM chunks_fts WHERE chunk_id=?", (oid,)
                            )
                        except sqlite3.OperationalError:
                            pass
                for c in chunks:
                    conn.execute(
                        """
                        INSERT INTO chunks(
                            id, doc_id, ordinal, text, title, source_path, title_chain
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            c.id,
                            c.doc_id,
                            c.ordinal,
                            c.text,
                            c.title,
                            c.source_path,
                            c.title_chain or "",
                        ),
                    )
                    try:
                        conn.execute(
                            """
                            INSERT INTO chunks_fts(chunk_id, text, title_chain)
                            VALUES (?,?,?)
                            """,
                            (c.id, c.text, c.title_chain or ""),
                        )
                    except sqlite3.OperationalError as e:
                        logger.debug("fts insert skip: %s", e)
                conn.commit()
            finally:
                conn.close()

    def _chunk_from_row(self, row: sqlite3.Row) -> ChunkRecord:
        keys = row.keys()
        return ChunkRecord(
            id=row["id"],
            doc_id=row["doc_id"],
            ordinal=int(row["ordinal"]),
            text=row["text"],
            title=row["title"] or "",
            source_path=row["source_path"] or "",
            title_chain=(row["title_chain"] if "title_chain" in keys else "") or "",
        )

    def get_chunk(self, chunk_id: str) -> Optional[ChunkRecord]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM chunks WHERE id=?", (chunk_id,)
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        return self._chunk_from_row(row)

    def list_chunks(self, doc_id: str) -> List[ChunkRecord]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM chunks WHERE doc_id=? ORDER BY ordinal ASC",
                    (doc_id,),
                ).fetchall()
            finally:
                conn.close()
        return [self._chunk_from_row(r) for r in rows]

    def get_chunk_neighbors(
        self, chunk_id: str, *, before: int = 1, after: int = 1
    ) -> List[ChunkRecord]:
        """同文档 ordinal 邻域（含自身）。"""
        cur = self.get_chunk(chunk_id)
        if not cur:
            return []
        all_c = self.list_chunks(cur.doc_id)
        idx = next((i for i, c in enumerate(all_c) if c.id == chunk_id), -1)
        if idx < 0:
            return [cur]
        lo = max(0, idx - max(0, before))
        hi = min(len(all_c), idx + max(0, after) + 1)
        return all_c[lo:hi]

    def keyword_search(self, q: str, *, limit: int = 20) -> List[ChunkRecord]:
        """FTS5 关键词检索；失败时 LIKE 回退。"""
        query = (q or "").strip()
        if not query:
            return []
        lim = max(1, min(int(limit), 50))
        with self._lock:
            conn = self._connect()
            try:
                rows = None
                # FTS：转义双引号；trigram 可直接 MATCH
                fts_q = query.replace('"', '""')
                try:
                    # 短语查询更稳；失败再裸查
                    for candidate in (f'"{fts_q}"', fts_q):
                        try:
                            rows = conn.execute(
                                """
                                SELECT c.* FROM chunks_fts f
                                JOIN chunks c ON c.id = f.chunk_id
                                WHERE f MATCH ?
                                LIMIT ?
                                """,
                                (candidate, lim),
                            ).fetchall()
                            if rows:
                                break
                        except sqlite3.OperationalError:
                            rows = None
                except sqlite3.OperationalError:
                    rows = None
                if not rows:
                    like = f"%{query}%"
                    rows = conn.execute(
                        """
                        SELECT * FROM chunks
                        WHERE text LIKE ? OR title_chain LIKE ? OR title LIKE ?
                        LIMIT ?
                        """,
                        (like, like, like, lim),
                    ).fetchall()
            finally:
                conn.close()
        return [self._chunk_from_row(r) for r in rows or []]

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            doc = self.get_document(doc_id)
            if not doc:
                return False
            conn = self._connect()
            try:
                ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM chunks WHERE doc_id=?", (doc_id,)
                    ).fetchall()
                ]
                conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
                for oid in ids:
                    try:
                        conn.execute(
                            "DELETE FROM chunks_fts WHERE chunk_id=?", (oid,)
                        )
                    except sqlite3.OperationalError:
                        pass
                conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
                conn.commit()
            finally:
                conn.close()
            if doc.stored_path:
                try:
                    p = Path(doc.stored_path)
                    if p.is_file() and self.files_dir in p.resolve().parents:
                        p.unlink(missing_ok=True)
                except OSError as e:
                    logger.debug("unlink stored file failed: %s", e)
            return True

    def list_all_document_ids(self) -> List[str]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id FROM documents ORDER BY datetime(created_at) ASC"
                ).fetchall()
            finally:
                conn.close()
        return [str(r[0]) for r in rows]
