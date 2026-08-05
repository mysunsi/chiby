"""KnowledgeHub — SQLite 持久化存储层。

使用 SQLAlchemy ORM，复用 chibycore/database.py 的 engine 基础设施。
支持：KBEntry / ScriptEntry / BestPractice 的 CRUD 操作。
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from sqlalchemy import (
    JSON, Column, DateTime, Float, Index,
    Integer, String, Text, create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from chibycore.knowledge_hub.models import (
    BestPractice,
    KBConfidence,
    KBCategory,
    KBEntry,
    KBPendingCandidate,
    PendingKBStatus,
    ScriptEntry,
    ScriptLanguage,
    ScriptRiskLevel,
)

logger = logging.getLogger(__name__)

Base = declarative_base()


# ─────────────────────────────────────────────────────────────────────────────
# ORM 模型
# ─────────────────────────────────────────────────────────────────────────────

class KBEntryRow(Base):
    __tablename__ = "kh_kb_entries"

    id = Column(String(16), primary_key=True)
    title = Column(Text, nullable=False)
    category = Column(String(64), nullable=False)
    symptom = Column(Text, nullable=False)
    root_cause = Column(Text, nullable=False)
    remediation = Column(Text, nullable=False)
    verify_method = Column(Text, nullable=True)
    applicable_os = Column(JSON, default=list)
    applicable_service = Column(String(128), nullable=True)
    tags = Column(JSON, default=list)
    error_fingerprint = Column(String(128), nullable=True)
    original_command = Column(Text, nullable=True)
    confidence = Column(String(16), nullable=False, default="medium")
    source = Column(String(32), nullable=False)
    source_id = Column(String(64), nullable=True)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    rating = Column(Float, default=0.0)
    rating_count = Column(Integer, default=0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    created_by = Column(String(64), default="system")

    __table_args__ = (
        Index("ix_kb_category", "category"),
        Index("ix_kb_fingerprint", "error_fingerprint"),
        Index("ix_kb_source", "source"),
        Index("ix_kb_created", "created_at"),
    )


class ScriptEntryRow(Base):
    __tablename__ = "kh_script_entries"

    id = Column(String(16), primary_key=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    language = Column(String(32), nullable=False, default="bash")
    applicable_os = Column(JSON, default=list)
    parameters = Column(JSON, nullable=True)
    parameter_examples = Column(JSON, nullable=True)
    prerequisites = Column(Text, nullable=True)
    risk_level = Column(String(16), nullable=False, default="medium")
    expected_duration_sec = Column(Integer, default=30)
    category = Column(String(64), nullable=False, default="other")
    tags = Column(JSON, default=list)
    version = Column(String(32), default="1.0.0")
    version_notes = Column(Text, nullable=True)
    related_kb_ids = Column(JSON, default=list)
    use_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    created_by = Column(String(64), default="system")

    __table_args__ = (
        Index("ix_script_category", "category"),
        Index("ix_script_language", "language"),
        Index("ix_script_created", "created_at"),
    )


class BestPracticeRow(Base):
    __tablename__ = "kh_best_practices"

    id = Column(String(16), primary_key=True)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    steps = Column(Text, nullable=False)
    applicable_scenarios = Column(JSON, default=list)
    applicable_os = Column(JSON, default=list)
    category = Column(String(64), nullable=False, default="other")
    tags = Column(JSON, default=list)
    source_url = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_bp_category", "category"),
    )


class PendingKBCandidateRow(Base):
    """闭环生成的 KB 候选（人工批准后写入 kh_kb_entries）。"""
    __tablename__ = "kh_kb_pending_candidates"

    id = Column(String(16), primary_key=True)
    trace_id = Column(String(48), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="pending")
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_pending_status_created", "status", "created_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _token_freq(text: str) -> Dict[str, float]:
    """轻量词袋：用于无外部 embedding 依赖时的文本相似度计算。"""
    toks = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_/.\-:@]{2,}", text or "")
    d: Dict[str, float] = {}
    for t in toks:
        t_low = t.lower()
        d[t_low] = d.get(t_low, 0.0) + 1.0
    return d


def _cosine_sim(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


def _levenshtein_ratio(a: str, b: str) -> float:
    """归一化编辑距离相似度。"""
    a, b = (a or "").strip(), (b or "").strip()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    n, m = len(a), len(b)
    if n > m:
        a, b, n, m = b, a, m, n
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    dist = dp[n][m]
    return 1.0 - dist / max(n, m)


# ─────────────────────────────────────────────────────────────────────────────
# 存储层
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeHubStorage:
    """
    统一存储：KB / Script / BestPractice。
    支持 SQLite（开发）+ PostgreSQL（生产，连接串通过 DATABASE_URL 环境变量切换）。
    """

    _instance: Optional["KnowledgeHubStorage"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path:
            self.db_path = Path(db_path)
        else:
            # 默认放在 chibycore/data 下
            self.db_path = (
                __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data" / "knowledge_hub.db"
            )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: Engine = self._build_engine()
        self._Session: Type[Session] = sessionmaker(bind=self._engine)
        self._init_schema()

    @classmethod
    def get_instance(cls) -> "KnowledgeHubStorage":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _build_engine(self) -> Engine:
        db_url = os.environ.get("DATABASE_URL", "").strip()
        if db_url:
            engine = create_engine(db_url, pool_pre_ping=True, pool_size=5)
            logger.info("KnowledgeHub 使用 PostgreSQL: %s", db_url.split("@")[1] if "@" in db_url else db_url)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                f"sqlite:///{self.db_path}",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            logger.info("KnowledgeHub 使用 SQLite: %s", self.db_path)
        return engine

    def _init_schema(self) -> None:
        Base.metadata.create_all(self._engine)

    def _session(self) -> Session:
        return self._Session()

    # ── KB Entry ─────────────────────────────────────────────────────────────

    def save_kb_entry(self, entry: KBEntry) -> KBEntry:
        row = self._to_kb_row(entry)
        with self._session() as s:
            existing = s.get(KBEntryRow, entry.id)
            if existing:
                for k, v in self._kb_row_to_dict(row).items():
                    if k not in ("id", "created_at"):
                        setattr(existing, k, v)
            else:
                s.add(row)
            s.commit()
        return entry

    def get_kb_entry(self, entry_id: str) -> Optional[KBEntry]:
        with self._session() as s:
            row = s.get(KBEntryRow, entry_id)
            return self._from_kb_row(row) if row else None

    def list_kb_entries(
        self,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[KBEntry]:
        with self._session() as s:
            q = s.query(KBEntryRow)
            if category:
                q = q.filter(KBEntryRow.category == category)
            if source:
                q = q.filter(KBEntryRow.source == source)
            q = q.order_by(KBEntryRow.updated_at.desc())
            rows = q.offset(offset).limit(limit).all()
            entries = [self._from_kb_row(r) for r in rows]

        if tags:
            entries = [e for e in entries if any(t in e.tags for t in tags)]
        return entries

    def delete_kb_entry(self, entry_id: str) -> bool:
        with self._session() as s:
            row = s.get(KBEntryRow, entry_id)
            if row:
                s.delete(row)
                s.commit()
                return True
        return False

    def count_kb_entries(self, category: Optional[str] = None) -> int:
        with self._session() as s:
            q = s.query(KBEntryRow)
            if category:
                q = q.filter(KBEntryRow.category == category)
            return q.count()

    def get_all_kb_rows(self) -> List[KBEntryRow]:
        """获取所有 KB 行（用于全文检索）"""
        with self._session() as s:
            return s.query(KBEntryRow).all()

    # ── Script Entry ──────────────────────────────────────────────────────────

    def save_script_entry(self, entry: ScriptEntry) -> ScriptEntry:
        row = self._to_script_row(entry)
        with self._session() as s:
            existing = s.get(ScriptEntryRow, entry.id)
            if existing:
                for k, v in self._script_row_to_dict(row).items():
                    if k not in ("id", "created_at"):
                        setattr(existing, k, v)
            else:
                s.add(row)
            s.commit()
        return entry

    def get_script_entry(self, entry_id: str) -> Optional[ScriptEntry]:
        with self._session() as s:
            row = s.get(ScriptEntryRow, entry_id)
            return self._from_script_row(row) if row else None

    def list_script_entries(
        self,
        category: Optional[str] = None,
        language: Optional[str] = None,
        risk_level: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ScriptEntry]:
        with self._session() as s:
            q = s.query(ScriptEntryRow)
            if category:
                q = q.filter(ScriptEntryRow.category == category)
            if language:
                q = q.filter(ScriptEntryRow.language == language)
            if risk_level:
                q = q.filter(ScriptEntryRow.risk_level == risk_level)
            q = q.order_by(ScriptEntryRow.updated_at.desc())
            rows = q.offset(offset).limit(limit).all()
            entries = [self._from_script_row(r) for r in rows]

        if tags:
            entries = [e for e in entries if any(t in e.tags for t in tags)]
        return entries

    def delete_script_entry(self, entry_id: str) -> bool:
        with self._session() as s:
            row = s.get(ScriptEntryRow, entry_id)
            if row:
                s.delete(row)
                s.commit()
                return True
        return False

    def count_script_entries(self, category: Optional[str] = None) -> int:
        with self._session() as s:
            q = s.query(ScriptEntryRow)
            if category:
                q = q.filter(ScriptEntryRow.category == category)
            return q.count()

    def get_all_script_rows(self) -> List[ScriptEntryRow]:
        with self._session() as s:
            return s.query(ScriptEntryRow).all()

    # ── Best Practice ─────────────────────────────────────────────────────────

    def save_best_practice(self, entry: BestPractice) -> BestPractice:
        row = self._to_bp_row(entry)
        with self._session() as s:
            existing = s.get(BestPracticeRow, entry.id)
            if existing:
                for k, v in self._bp_row_to_dict(row).items():
                    if k not in ("id", "created_at"):
                        setattr(existing, k, v)
            else:
                s.add(row)
            s.commit()
        return entry

    def get_best_practice(self, entry_id: str) -> Optional[BestPractice]:
        with self._session() as s:
            row = s.get(BestPracticeRow, entry_id)
            return self._from_bp_row(row) if row else None

    def list_best_practices(
        self,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[BestPractice]:
        with self._session() as s:
            q = s.query(BestPracticeRow)
            if category:
                q = q.filter(BestPracticeRow.category == category)
            q = q.order_by(BestPracticeRow.updated_at.desc())
            rows = q.offset(offset).limit(limit).all()
            entries = [self._from_bp_row(r) for r in rows]

        if tags:
            entries = [e for e in entries if any(t in e.tags for t in tags)]
        return entries

    def delete_best_practice(self, entry_id: str) -> bool:
        with self._session() as s:
            row = s.get(BestPracticeRow, entry_id)
            if row:
                s.delete(row)
                s.commit()
                return True
        return False

    def get_all_bp_rows(self) -> List[BestPracticeRow]:
        with self._session() as s:
            return s.query(BestPracticeRow).all()

    # ── KB 候选队列（闭环 → 人工批准）──────────────────────────────────────────

    def save_pending_candidate(self, cand: KBPendingCandidate) -> KBPendingCandidate:
        payload = cand.model_dump(mode="json")
        row = PendingKBCandidateRow(
            id=cand.id,
            trace_id=cand.trace_id,
            status=cand.status.value,
            payload=payload,
            created_at=cand.created_at,
            reviewed_at=cand.reviewed_at,
            reviewed_by=cand.reviewed_by or None,
        )
        with self._session() as s:
            s.merge(row)
            s.commit()
        return cand

    def get_pending_candidate(self, candidate_id: str) -> Optional[KBPendingCandidate]:
        with self._session() as s:
            row = s.get(PendingKBCandidateRow, candidate_id)
            return self._pending_row_to_model(row) if row else None

    def list_pending_candidates(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[KBPendingCandidate]:
        with self._session() as s:
            q = s.query(PendingKBCandidateRow)
            if status:
                q = q.filter(PendingKBCandidateRow.status == status)
            q = q.order_by(PendingKBCandidateRow.created_at.desc())
            rows = q.offset(offset).limit(limit).all()
            return [self._pending_row_to_model(r) for r in rows]

    def count_pending_candidates(self, *, status: Optional[str] = None) -> int:
        with self._session() as s:
            q = s.query(PendingKBCandidateRow)
            if status:
                q = q.filter(PendingKBCandidateRow.status == status)
            return q.count()

    def update_pending_candidate_review(
        self,
        candidate_id: str,
        *,
        status: PendingKBStatus,
        reviewed_by: str = "",
        reject_reason: Optional[str] = None,
    ) -> bool:
        now = datetime.utcnow()
        with self._session() as s:
            row = s.get(PendingKBCandidateRow, candidate_id)
            if not row:
                return False
            row.status = status.value
            row.reviewed_at = now
            row.reviewed_by = reviewed_by or None
            data = dict(row.payload or {})
            data["status"] = status.value
            data["reviewed_at"] = now.isoformat()
            data["reviewed_by"] = reviewed_by
            if reject_reason is not None:
                data["reject_reason"] = reject_reason
            row.payload = data
            s.commit()
            return True

    def _pending_row_to_model(self, row: PendingKBCandidateRow) -> KBPendingCandidate:
        data = dict(row.payload or {})
        data["id"] = row.id
        data["trace_id"] = row.trace_id
        data["status"] = row.status
        return KBPendingCandidate.model_validate(data)

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        with self._session() as s:
            pending_n = (
                s.query(PendingKBCandidateRow)
                .filter(PendingKBCandidateRow.status == PendingKBStatus.PENDING.value)
                .count()
            )
            return {
                "kb_entries": s.query(KBEntryRow).count(),
                "script_entries": s.query(ScriptEntryRow).count(),
                "best_practices": s.query(BestPracticeRow).count(),
                "pending_kb_candidates": pending_n,
                "db_path": str(self.db_path),
            }

    # ── 导出/导入 ──────────────────────────────────────────────────────────────

    def export_all(self) -> Dict[str, Any]:
        """导出所有数据（用于备份）"""
        with self._session() as s:
            return {
                "kb_entries": [self._row_to_dict(r) for r in s.query(KBEntryRow).all()],
                "script_entries": [self._script_row_to_dict(r) for r in s.query(ScriptEntryRow).all()],
                "best_practices": [self._bp_row_to_dict(r) for r in s.query(BestPracticeRow).all()],
                "exported_at": datetime.utcnow().isoformat(),
            }

    # ── Row ↔ Model 转换 ──────────────────────────────────────────────────────

    def _to_kb_row(self, e: KBEntry) -> KBEntryRow:
        return KBEntryRow(
            id=e.id,
            title=e.title,
            category=e.category.value,
            symptom=e.symptom,
            root_cause=e.root_cause,
            remediation=e.remediation,
            verify_method=e.verify_method,
            applicable_os=e.applicable_os,
            applicable_service=e.applicable_service,
            tags=e.tags,
            error_fingerprint=e.error_fingerprint,
            original_command=e.original_command,
            confidence=e.confidence.value,
            source=e.source,
            source_id=e.source_id,
            success_count=e.success_count,
            failure_count=e.failure_count,
            rating=e.rating,
            rating_count=e.rating_count,
            notes=e.notes,
            created_at=e.created_at,
            updated_at=e.updated_at,
            created_by=e.created_by,
        )

    def _from_kb_row(self, r: KBEntryRow) -> KBEntry:
        return KBEntry(
            id=r.id,
            title=r.title,
            category=KBCategory(r.category),
            symptom=r.symptom,
            root_cause=r.root_cause,
            remediation=r.remediation,
            verify_method=r.verify_method,
            applicable_os=r.applicable_os or [],
            applicable_service=r.applicable_service,
            tags=r.tags or [],
            error_fingerprint=r.error_fingerprint,
            original_command=r.original_command,
            confidence=KBConfidence(r.confidence),
            source=r.source,
            source_id=r.source_id,
            success_count=r.success_count,
            failure_count=r.failure_count,
            rating=r.rating or 0.0,
            rating_count=r.rating_count or 0,
            notes=r.notes,
            created_at=r.created_at,
            updated_at=r.updated_at,
            created_by=r.created_by,
        )

    def _to_script_row(self, e: ScriptEntry) -> ScriptEntryRow:
        return ScriptEntryRow(
            id=e.id,
            name=e.name,
            description=e.description,
            content=e.content,
            language=e.language.value,
            applicable_os=e.applicable_os,
            parameters=e.parameters,
            parameter_examples=e.parameter_examples,
            prerequisites=e.prerequisites,
            risk_level=e.risk_level.value,
            expected_duration_sec=e.expected_duration_sec,
            category=e.category.value,
            tags=e.tags,
            version=e.version,
            version_notes=e.version_notes,
            related_kb_ids=e.related_kb_ids,
            use_count=e.use_count,
            success_count=e.success_count,
            failure_count=e.failure_count,
            created_at=e.created_at,
            updated_at=e.updated_at,
            created_by=e.created_by,
        )

    def _from_script_row(self, r: ScriptEntryRow) -> ScriptEntry:
        return ScriptEntry(
            id=r.id,
            name=r.name,
            description=r.description,
            content=r.content,
            language=ScriptLanguage(r.language),
            applicable_os=r.applicable_os or [],
            parameters=r.parameters,
            parameter_examples=r.parameter_examples,
            prerequisites=r.prerequisites,
            risk_level=ScriptRiskLevel(r.risk_level),
            expected_duration_sec=r.expected_duration_sec or 30,
            category=KBCategory(r.category),
            tags=r.tags or [],
            version=r.version or "1.0.0",
            version_notes=r.version_notes,
            related_kb_ids=r.related_kb_ids or [],
            use_count=r.use_count or 0,
            success_count=r.success_count or 0,
            failure_count=r.failure_count or 0,
            created_at=r.created_at,
            updated_at=r.updated_at,
            created_by=r.created_by,
        )

    def _to_bp_row(self, e: BestPractice) -> BestPracticeRow:
        return BestPracticeRow(
            id=e.id,
            title=e.title,
            description=e.description,
            steps=e.steps,
            applicable_scenarios=e.applicable_scenarios,
            applicable_os=e.applicable_os,
            category=e.category.value,
            tags=e.tags,
            source_url=e.source_url,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )

    def _from_bp_row(self, r: BestPracticeRow) -> BestPractice:
        return BestPractice(
            id=r.id,
            title=r.title,
            description=r.description,
            steps=r.steps,
            applicable_scenarios=r.applicable_scenarios or [],
            applicable_os=r.applicable_os or [],
            category=KBCategory(r.category),
            tags=r.tags or [],
            source_url=r.source_url,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )

    def _row_to_dict(self, r: KBEntryRow) -> Dict[str, Any]:
        return {c.name: getattr(r, c.name) for c in r.__table__.columns}

    def _script_row_to_dict(self, r: ScriptEntryRow) -> Dict[str, Any]:
        return {c.name: getattr(r, c.name) for c in r.__table__.columns}

    def _bp_row_to_dict(self, r: BestPracticeRow) -> Dict[str, Any]:
        return {c.name: getattr(r, c.name) for c in r.__table__.columns}


# ── 延迟导入 os（避免顶部 import 引发循环依赖） ─────────────────────────────
import os
