"""经验沉淀：SQLite 本地知识库（指纹 + 三级命中 + 写入去重）。"""
from __future__ import annotations

import difflib
import json
import logging
import math
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from pydantic import ValidationError

from .models import (
    EnvironmentSnapshot,
    ErrorCategory,
    KnowledgeRecord,
    StructuredError,
    compute_error_fingerprint,
    normalize_command_for_fingerprint,
    normalize_text_for_fingerprint,
    os_fingerprint_key,
)

logger = logging.getLogger(__name__)


def _token_freq(text: str) -> dict[str, float]:
    """轻量词袋（无外部 embedding 依赖），用于 stderr+命令 相似度。"""
    toks = re.findall(r"[a-zA-Z0-9_/.\-:@]+", (text or "").lower())
    d: dict[str, float] = {}
    for t in toks:
        if len(t) < 2:
            continue
        d[t] = d.get(t, 0.0) + 1.0
    return d


def _cosine_dict(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


# ── KnowledgeHub 跨写（懒加载，避免循环导入） ──────────────────────────────
_KNOWLEDGE_HUB_INSTANCE = None


def _cross_write_to_knowledge_hub(record: KnowledgeRecord, metadata: Optional[dict[str, Any]] = None) -> None:
    """将 remediator 的成功案例同步写入 KnowledgeHub 统一知识库。"""
    global _KNOWLEDGE_HUB_INSTANCE
    try:
        if _KNOWLEDGE_HUB_INSTANCE is None:
            from chibycore.knowledge_hub.storage import KnowledgeHubStorage
            from chibycore.knowledge_hub.models import KBEntry, KBCategory, KBConfidence
            _KNOWLEDGE_HUB_INSTANCE = object.__new__(object)
            _KNOWLEDGE_HUB_INSTANCE.storage = KnowledgeHubStorage.get_instance()
            _KNOWLEDGE_HUB_INSTANCE.KBEntry = KBEntry
            _KNOWLEDGE_HUB_INSTANCE.KBCategory = KBCategory
            _KNOWLEDGE_HUB_INSTANCE.KBConfidence = KBConfidence

        meta = metadata or {}
        KBEntry = _KNOWLEDGE_HUB_INSTANCE.KBEntry
        KBCategory = _KNOWLEDGE_HUB_INSTANCE.KBCategory
        KBConfidence = _KNOWLEDGE_HUB_INSTANCE.KBConfidence

        cmd = (record.original_command or "").strip()
        fixed = (record.fixed_command or "").strip()
        stderr = (record.stderr_snippet or "")[:1500]
        cat = record.error_category.value.lower() if record.error_category else "other"

        # error_category -> KBCategory 映射
        CAT_MAP = {
            "package_missing": KBCategory.PACKAGE_MANAGEMENT,
            "service_failed": KBCategory.SERVICE_OPS,
            "permission_denied": KBCategory.SECURITY,
            "network": KBCategory.NETWORK_OPS,
            "config_error": KBCategory.SERVICE_OPS,
            "disk_full": KBCategory.SYSTEM_MONITOR,
            "memory": KBCategory.SYSTEM_MONITOR,
            "timeout": KBCategory.NETWORK_OPS,
            "dependency": KBCategory.PACKAGE_MANAGEMENT,
            "port_conflict": KBCategory.NETWORK_OPS,
            "ssl_tls": KBCategory.SECURITY,
            "auth": KBCategory.SECURITY,
        }
        kb_cat = CAT_MAP.get(cat, KBCategory.FAILURE_RECOVERY)

        entry = KBEntry(
            title=f"[{cat}] {fixed[:80]}",
            category=kb_cat,
            symptom=(f"stderr: {stderr}" if stderr else f"命令失败: {cmd}"),
            root_cause=record.root_cause or "remediator 自动修复成功",
            remediation=fixed,
            applicable_os=[record.env_os] if record.env_os else [],
            tags=[cat, record.env_os or ""] if cat else ["remediator"],
            error_fingerprint=record.fingerprint,
            original_command=cmd,
            confidence=KBConfidence.MEDIUM,
            source="remediator_success",
            source_id=meta.get("trace_id") or str(record.created_at.timestamp()),
            success_count=1,
        )
        _KNOWLEDGE_HUB_INSTANCE.storage.save_kb_entry(entry)
        logging.getLogger(__name__).info(
            "KnowledgeHub 已同步 remediator 成功案例 id=%s cmd=%s",
            entry.id, fixed[:60],
        )
    except Exception as ex:
        logging.getLogger(__name__).warning(
            "KnowledgeHub 跨写失败（非致命）: %s", ex
        )


class RemediationKnowledgeBase:
    """成功案例存储；支持 fingerprint 精确命中与多级兜底检索。"""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        info = {r["name"] for r in conn.execute("PRAGMA table_info(remediation_cases)").fetchall()}
        alters = []
        if "fingerprint" not in info:
            alters.append("ALTER TABLE remediation_cases ADD COLUMN fingerprint TEXT DEFAULT ''")
        if "requires_package" not in info:
            alters.append(
                "ALTER TABLE remediation_cases ADD COLUMN requires_package TEXT DEFAULT ''"
            )
        for stmt in alters:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS remediation_cases (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        error_category TEXT NOT NULL,
                        env_os TEXT DEFAULT '',
                        env_privilege TEXT DEFAULT '',
                        original_command TEXT NOT NULL,
                        fixed_command TEXT NOT NULL,
                        root_cause TEXT DEFAULT '',
                        stderr_snippet TEXT DEFAULT '',
                        created_at REAL NOT NULL,
                        metadata_json TEXT DEFAULT '{}',
                        fingerprint TEXT DEFAULT '',
                        requires_package TEXT DEFAULT ''
                    )
                    """
                )
                self._migrate_columns(conn)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kb_category ON remediation_cases(error_category)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kb_orig_cmd ON remediation_cases(original_command)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kb_fingerprint ON remediation_cases(fingerprint)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kb_pkg ON remediation_cases(error_category, requires_package)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_cache (
                        prompt_hash TEXT PRIMARY KEY,
                        llm_response_json TEXT NOT NULL,
                        timestamp REAL NOT NULL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    _LLM_CACHE_TTL_SEC = 86400

    def get_llm_response_cache(self, prompt_hash: str) -> Optional[str]:
        """
        查询 LLM 响应缓存（JSON 文本）。

        命中条件：存在记录且写入时间在 24 小时内；否则返回 ``None``。
        ``timestamp`` 存 Unix 时间戳（REAL），便于比较。
        """
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT llm_response_json, timestamp FROM llm_cache WHERE prompt_hash = ?",
                    (prompt_hash,),
                ).fetchone()
                if not row:
                    return None
                ts = float(row["timestamp"])
                if time.time() - ts > self._LLM_CACHE_TTL_SEC:
                    return None
                return str(row["llm_response_json"])
            finally:
                conn.close()

    def put_llm_response_cache(self, prompt_hash: str, response_json: str) -> None:
        """写入或覆盖缓存（INSERT OR REPLACE）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO llm_cache (prompt_hash, llm_response_json, timestamp)
                    VALUES (?, ?, ?)
                    """,
                    (prompt_hash, response_json, time.time()),
                )
                conn.commit()
            finally:
                conn.close()

    def save_success(
        self,
        record: KnowledgeRecord,
        metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        """
        写入成功案例。
        去重：若 (fingerprint, fixed_command) 已存在 → 跳过（返回 0）。
        若仅 (error_category, fixed_command) 相同但 fingerprint 不同 → 仍插入（不同环境/路径噪声）。
        """
        cmd = (record.original_command or "").strip()
        fp = (record.fingerprint or "").strip()
        if not fp:
            fp = compute_error_fingerprint(
                record.error_category.value,
                normalize_command_for_fingerprint(cmd),
                (record.env_os or "").lower(),
            )

        pkg = (record.requires_package or "").strip() or ""

        with self._lock:
            conn = self._connect()
            try:
                dup = conn.execute(
                    """
                    SELECT id FROM remediation_cases
                    WHERE fingerprint = ? AND fixed_command = ?
                    LIMIT 1
                    """,
                    (fp, record.fixed_command.strip()),
                ).fetchone()
                if dup:
                    logger.info(
                        "知识库去重跳过：(fingerprint, fixed_command) 已存在 id=%s",
                        dup["id"],
                    )
                    conn.commit()
                    return 0

                cur = conn.execute(
                    """
                    INSERT INTO remediation_cases (
                        error_category, env_os, env_privilege,
                        original_command, fixed_command, root_cause,
                        stderr_snippet, created_at, metadata_json,
                        fingerprint, requires_package
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.error_category.value,
                        record.env_os,
                        record.env_privilege,
                        record.original_command,
                        record.fixed_command,
                        record.root_cause,
                        record.stderr_snippet,
                        record.created_at.timestamp(),
                        json.dumps(metadata or {}, ensure_ascii=False),
                        fp,
                        pkg,
                    ),
                )
                conn.commit()
                row_id = int(cur.lastrowid or 0)
                # 同步写入 KnowledgeHub（非致命）
                if row_id:
                    _cross_write_to_knowledge_hub(record, metadata)
                return row_id
            finally:
                conn.close()

    def query_best_match(
        self,
        error: StructuredError,
        env: EnvironmentSnapshot,
    ) -> Optional[KnowledgeRecord]:
        """
        三级命中（顺序执行，先命中先返回）：
        1) fingerprint 精确
        2) error_category + requires_package（requires_package 非空时）
        3) error_category + 规范化 stderr 模糊相似（SequenceMatcher）
        """
        cmd = (error.metadata.get("command") or "").strip()
        os_key = os_fingerprint_key(env)
        ncmd = normalize_command_for_fingerprint(cmd)
        fp = compute_error_fingerprint(error.error_category.value, ncmd, os_key)

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM remediation_cases
                    WHERE fingerprint = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (fp,),
                ).fetchone()
                if row:
                    return self._row_to_record(row)

                pkg = (error.requires_package or "").strip()
                if pkg:
                    row = conn.execute(
                        """
                        SELECT * FROM remediation_cases
                        WHERE error_category = ? AND requires_package = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (error.error_category.value, pkg),
                    ).fetchone()
                    if row:
                        return self._row_to_record(row)

                blob_err = error.stderr_snippet or error.raw_stderr or ""
                ne = normalize_text_for_fingerprint(blob_err)
                if len(ne) < 8:
                    return None

                rows = conn.execute(
                    """
                    SELECT * FROM remediation_cases
                    WHERE error_category = ?
                    ORDER BY created_at DESC
                    LIMIT 40
                    """,
                    (error.error_category.value,),
                ).fetchall()

                best_row = None
                best_score = 0.0
                for r in rows:
                    ns = normalize_text_for_fingerprint(r["stderr_snippet"] or "")
                    if not ns:
                        continue
                    score = difflib.SequenceMatcher(None, ne, ns).ratio()
                    if score > best_score:
                        best_score = score
                        best_row = r

                if best_row is not None and best_score >= 0.50:
                    return self._row_to_record(best_row)
                return None
            finally:
                conn.close()

    def find_similar(
        self,
        error_category: ErrorCategory,
        original_command: str,
        stderr_snippet: str = "",
        limit: int = 3,
    ) -> List[KnowledgeRecord]:
        """兼容保留：粗粒度检索（未使用 fingerprint）。"""
        out: List[KnowledgeRecord] = []
        oc = (original_command or "").strip()
        snip = (stderr_snippet or "").strip()[:120]

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM remediation_cases
                    WHERE error_category = ? AND original_command = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (error_category.value, oc, limit),
                ).fetchall()

                if not rows and len(oc) >= 4:
                    like_q = f"%{oc[:min(len(oc), 48)]}%"
                    rows = conn.execute(
                        """
                        SELECT * FROM remediation_cases
                        WHERE error_category = ? AND original_command LIKE ?
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (error_category.value, like_q, limit),
                    ).fetchall()

                if not rows and snip:
                    like_e = f"%{snip[:48]}%"
                    rows = conn.execute(
                        """
                        SELECT * FROM remediation_cases
                        WHERE error_category = ? AND stderr_snippet LIKE ?
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (error_category.value, like_e, limit),
                    ).fetchall()

                if not rows:
                    rows = conn.execute(
                        """
                        SELECT * FROM remediation_cases
                        WHERE error_category = ?
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (error_category.value, limit),
                    ).fetchall()

                for row in rows:
                    try:
                        out.append(self._row_to_record(row))
                    except ValidationError:
                        continue
                return out
            finally:
                conn.close()

    def query_vector_similar(
        self,
        *,
        query_text: str,
        query_command: str = "",
        error_category: Optional[ErrorCategory] = None,
        k: int = 4,
        pool_limit: int = 160,
        min_score: float = 0.12,
    ) -> List[Tuple[KnowledgeRecord, float]]:
        """
        在本地案例池中按「词袋余弦」排序，返回 top-k（无向量 DB 依赖）。
        优先过滤同 error_category（若有）；否则在全池抽样中比对。
        """
        blob = f"{query_command}\n{query_text}".strip()
        qv = _token_freq(blob)
        if not qv:
            return []

        with self._lock:
            conn = self._connect()
            try:
                if error_category is not None:
                    rows = conn.execute(
                        """
                        SELECT * FROM remediation_cases
                        WHERE error_category = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (error_category.value, pool_limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM remediation_cases
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (pool_limit,),
                    ).fetchall()
            finally:
                conn.close()

        scored: List[Tuple[KnowledgeRecord, float]] = []
        for row in rows:
            try:
                rec = self._row_to_record(row)
            except ValidationError:
                continue
            cand = f"{rec.original_command}\n{rec.stderr_snippet}".strip()
            cv = _token_freq(cand)
            sc = _cosine_dict(qv, cv)
            if sc >= min_score:
                scored.append((rec, sc))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[: max(1, k)]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> KnowledgeRecord:
        try:
            fp = str(row["fingerprint"] or "").strip()
        except (KeyError, IndexError, TypeError):
            fp = ""
        pkg: Optional[str] = None
        try:
            rp = row["requires_package"]
            if rp:
                p = str(rp).strip()
                pkg = p or None
        except (KeyError, IndexError, TypeError):
            pkg = None

        return KnowledgeRecord(
            error_category=ErrorCategory(row["error_category"]),
            env_os=row["env_os"] or "",
            env_privilege=row["env_privilege"] or "",
            original_command=row["original_command"],
            fixed_command=row["fixed_command"],
            root_cause=row["root_cause"] or "",
            stderr_snippet=row["stderr_snippet"] or "",
            fingerprint=fp,
            requires_package=pkg,
            created_at=datetime.fromtimestamp(row["created_at"], tz=timezone.utc),
        )
