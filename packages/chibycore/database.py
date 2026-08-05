"""数据库层 — SQLite 本地（开发）/ PostgreSQL（生产）零配置切换。
切换方式：DATABASE_URL=sqlite...  或  postgresql://...
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    JSON, Column, DateTime, Index, Integer,
    String, Text, create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

Base = declarative_base()

# ── ORM 模型 ──────────────────────────────────────────────────────────────────

class TaskHistory(Base):
    """任务执行历史。"""
    __tablename__ = "task_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(16), unique=True, nullable=False, index=True)
    command = Column(Text, nullable=False)
    task_type = Column(String(32), nullable=False)       # chain | single
    chain_name = Column(String(128), nullable=True)       # e.g. "monitor_resources"
    action = Column(String(64), nullable=True)           # e.g. "system_info"
    params = Column(JSON, default=dict)                   # extracted params
    status = Column(String(32), nullable=False)            # success | failed | partial
    steps_count = Column(Integer, default=0)
    steps_detail = Column(JSON, default=list)             # list of step summaries
    final_output = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_task_history_created", "created_at"),
        Index("ix_task_history_status", "status"),
        Index("ix_task_history_chain", "chain_name"),
    )


class StepDetail(Base):
    """步骤明细（可选，保留完整 step 数据）。"""
    __tablename__ = "step_details"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_history_id = Column(Integer, nullable=False, index=True)
    step_index = Column(Integer, nullable=False)
    action = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)
    command = Column(Text, nullable=True)
    status = Column(String(32), nullable=False)
    exit_code = Column(Integer, nullable=True)
    stdout = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)
    verified = Column(Integer, default=0)                # 0/1 for SQLite bool
    duration_ms = Column(Integer, default=0)
    executed_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_step_details_task", "task_history_id", "step_index"),
    )


# ── 数据库连接 ─────────────────────────────────────────────────────────────────

def _build_engine() -> Engine:
    """根据 DATABASE_URL 创建 engine。SQLite（开发）/ PostgreSQL（生产）。"""
    db_url = os.getenv("DATABASE_URL", "sqlite:////home/sunsi/Open/ai-ops-assistant/data/ops.db")

    # SQLite 特殊处理
    if db_url.startswith("sqlite"):
        # 确保目录存在
        if "///" in db_url:          # sqlite:////absolute/path
            path_str = db_url.split("///", 1)[1].split("?")[0]
        elif ":///" in db_url:       # sqlite:///relative/path
            path_str = db_url.split("///", 1)[1].split("?")[0]
        else:
            path_str = ":memory:"
        Path(path_str).parent.mkdir(parents=True, exist_ok=True)

        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
    else:
        # PostgreSQL / 其他
        engine = create_engine(
            db_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )

    return engine


_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker] = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


def get_session() -> Session:
    return get_session_factory()()


def init_db() -> None:
    """初始化数据库（创建表）。"""
    Base.metadata.create_all(bind=get_engine())
    logger.info("数据库表初始化完成")


# ── Repository ─────────────────────────────────────────────────────────────────

def save_task_history(
    task_id: str,
    command: str,
    task_type: str,
    status: str,
    chain_name: Optional[str] = None,
    action: Optional[str] = None,
    params: Optional[dict] = None,
    steps: Optional[list] = None,
    final_output: Optional[str] = None,
    error_message: Optional[str] = None,
    duration_ms: int = 0,
) -> TaskHistory:
    """保存任务历史记录。"""
    session = get_session()
    try:
        finished = datetime.utcnow()
        # 提取步骤概要
        steps_count = 0
        steps_detail: list = []
        if steps:
            steps_count = len(steps)
            steps_detail = [
                {
                    "index": i,
                    "action": s.get("action", ""),
                    "description": s.get("description", ""),
                    "status": s.get("status", ""),
                    "exit_code": s.get("exit_code", 0),
                    "verified": s.get("verified", False),
                    "duration_ms": s.get("duration_ms", 0),
                }
                for i, s in enumerate(steps)
            ]

        record = TaskHistory(
            task_id=task_id,
            command=command,
            task_type=task_type,
            chain_name=chain_name,
            action=action,
            params=params or {},
            status=status,
            steps_count=steps_count,
            steps_detail=steps_detail,
            final_output=(final_output or "")[:10000],
            error_message=error_message,
            duration_ms=duration_ms,
            finished_at=finished,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        logger.info(f"任务历史已保存: {task_id} [{status}]")
        return record
    except Exception as e:
        session.rollback()
        logger.error(f"保存任务历史失败: {e}")
        raise
    finally:
        session.close()


def get_task_history(
    limit: int = 50,
    offset: int = 0,
    chain_name: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> tuple:
    """查询任务历史。返回 (records, total)。"""
    session = get_session()
    try:
        q = session.query(TaskHistory)
        if chain_name:
            q = q.filter(TaskHistory.chain_name == chain_name)
        if status_filter:
            q = q.filter(TaskHistory.status == status_filter)
        total = q.count()
        records = (
            q.order_by(TaskHistory.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return records, total
    finally:
        session.close()


def get_task_by_id(task_id: str) -> Optional[TaskHistory]:
    session = get_session()
    try:
        return session.query(TaskHistory).filter(TaskHistory.task_id == task_id).first()
    finally:
        session.close()
